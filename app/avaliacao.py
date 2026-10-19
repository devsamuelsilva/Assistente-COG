from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from app.config import (
    ACERVO_DIR,
    DATA_DIR,
    EMBED_MODEL,
    JUIZ_MODEL,
    MODO_CONTEXTO,
    MODOS_CONTEXTO,
    RERANKER_MODEL,
    RERANKER_SUGERIDO,
)
from app import confianca, gpt
from app.arquivos import gravar_json, ler_json
from app.chunking import secao_base, separar_cabecalho
from app.indice import _modelo, carregar, prefixos
from app.rag import _assinatura_resposta, estrategia, gerar_openai, responder
from app.uso import custo_usd

PARALELO = 4
# Perguntas de qualquer operador que nenhum normativo responde. Entram em toda prova: se o assistente passar a
# responder alguma, é invenção, e a segunda tentativa ou o prompt novo estão soltos demais.
_RECUSAS_PADRAO = (
    "Qual a previsão do tempo para amanhã na usina?",
    "Qual o salário de um operador do COG?",
    "Como faço uma receita de bolo de cenoura?",
    "Quem ganhou o último campeonato brasileiro de futebol?",
    "Qual a senha do sistema supervisório?",
    "Quantos dias de férias tem um colaborador novo?",
    "Qual a cor do uniforme do operador?",
    "Qual o preço atual da energia no mercado livre?",
    "Qual o horário do refeitório da usina?",
    "Quem é o presidente da empresa?",
)
AMOSTRA = 50
STATUS = ("acerto", "parcial", "erro", "recusa_indevida", "invencao", "falha")
VEREDITOS = ("confere", "corrigida", "descartada")

_JUIZ = """Você confere a resposta de um assistente de normativos contra a resposta esperada de um gabarito.
Compare só o conteúdo. Ignore diferença de redação, ordem e tamanho.
Os "Trechos citados", quando vierem, são o texto do normativo em que o assistente se apoiou.
- "correto": a resposta traz a informação essencial da esperada e não contradiz nada dela. Informação a mais não rebaixa a nota quando está nos trechos citados.
- "parcial": acerta parte do essencial, mas falta algo essencial da esperada, ou afirma algo que não está nem na esperada nem nos trechos citados.
- "incorreto": contradiz a esperada, erra valor, prazo ou classificação, ou responde outra coisa.
Responda só com JSON: {"nota": "correto" | "parcial" | "incorreto", "motivo": "uma frase em português"}"""
_LIMITE_TRECHOS_JUIZ = 6000

_NUMERO = re.compile(r"^(\d+(?:\.\d+)*)")
_ANEXO = re.compile(r"^anexo\s+0*(\d+)\b")
_REF_ITEM = re.compile(r"\b(?:item|itens|tabela|quadro|se[cç][aã]o|subitem)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
_REF_ANEXO = re.compile(r"\banexos?\s+(\d+(?:\s*(?:,|e)\s*\d+)*)", re.IGNORECASE)
_CAMPOS_RESPOSTA = ("resposta_esperada", "resposta", "resposta_correta", "gabarito", "expected_answer", "answer")
_TRAVA = threading.Lock()
_ESTADO: dict = {"rodando": False, "feitos": 0, "total": 0, "inicio": "", "rodada": "", "erro": ""}
_RESUMOS_RODADA: dict[str, tuple] = {}


def recusas_padrao() -> list[dict]:
    return [
        {
            "id": f"recusa-padrao-{posicao:02d}",
            "pergunta": pergunta,
            "resposta_esperada": "",
            "documento": "",
            "secao": "",
            "referencia": "",
            "categoria": "Fora do escopo (padrão)",
            "valores": [],
            "deve_recusar": True,
            "revisao": "",
        }
        for posicao, pergunta in enumerate(_RECUSAS_PADRAO, start=1)
    ]


def _com_recusas_padrao(itens: list[dict]) -> list[dict]:
    existentes = {item["id"] for item in itens}
    return [*itens, *(item for item in recusas_padrao() if item["id"] not in existentes)]


def pasta(data_dir: Path) -> Path:
    return data_dir / "avaliacao"


def importar(data_dir: Path, conteudo: bytes | str) -> dict:
    texto = conteudo.decode("utf-8-sig") if isinstance(conteudo, bytes) else conteudo
    texto = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", texto.strip())
    try:
        bruto = json.loads(texto)
    except json.JSONDecodeError as erro:
        raise ValueError(f"O arquivo não é um JSON válido (linha {erro.lineno}).") from erro
    lista = _lista_de_perguntas(bruto)
    if not lista:
        chaves = f" Chaves encontradas: {', '.join(list(bruto)[:8])}." if isinstance(bruto, dict) and bruto else ""
        raise ValueError(f"O JSON precisa ser uma lista de perguntas, cada uma com o campo \"pergunta\".{chaves}")
    documento_padrao = str(bruto.get("documento") or "").strip() if isinstance(bruto, dict) else ""
    itens: list[dict] = []
    problemas: list[str] = []
    vistos: set[str] = set()
    for posicao, item in enumerate(lista, start=1):
        if not isinstance(item, dict):
            problemas.append(f"item {posicao} não é um objeto")
            continue
        pergunta = " ".join(_campo(item, "pergunta", "question").split())
        deve_recusar = bool(item.get("deve_recusar"))
        esperada = _campo(item, *_CAMPOS_RESPOSTA)
        identificador = _campo(item, "id") or f"q-{posicao:03d}"
        referencia = _campo(item, "referencia", "referência", "fonte", "reference")
        secao = _campo(item, "secao", "seção", "section") or "; ".join(_secoes_da_referencia(referencia))
        documento = _campo(item, "documento") or _documento_da_referencia(referencia) or documento_padrao
        if not pergunta:
            problemas.append(f"item {posicao} sem pergunta")
        if not esperada and not deve_recusar:
            problemas.append(f"item {posicao} sem resposta_esperada")
        if identificador in vistos:
            problemas.append(f"id repetido: {identificador}")
        vistos.add(identificador)
        valores = item.get("valores") or []
        if isinstance(valores, str):
            valores = [valores]
        itens.append(
            {
                "id": identificador,
                "pergunta": pergunta,
                "resposta_esperada": esperada,
                "documento": documento,
                "secao": secao,
                "referencia": referencia,
                "categoria": _campo(item, "categoria", "category"),
                "valores": [str(valor).strip() for valor in valores if str(valor).strip()],
                "deve_recusar": deve_recusar,
            }
        )
    if problemas:
        extra = f" e mais {len(problemas) - 5}" if len(problemas) > 5 else ""
        raise ValueError("Gabarito com problema: " + "; ".join(problemas[:5]) + extra + ".")
    return _somar(data_dir, itens)


def _somar(data_dir: Path, novos: list[dict]) -> dict:
    blocos = {_chave_documento(item["documento"]) for item in novos}
    atuais = gabarito(data_dir)
    mantidos = [item for item in atuais if _chave_documento(item["documento"]) not in blocos]
    usados = {item["id"] for item in mantidos}
    for item in novos:
        if item["id"] in usados:
            item["id"] = f"{_chave_documento(item['documento'])}-{item['id']}"
        if item["id"] in usados:
            raise ValueError(f"Gabarito com problema: id {item['id']} já existe em outro documento.")
        usados.add(item["id"])
    final = mantidos + novos
    gravar_json(pasta(data_dir) / "gabarito.json", final)
    return {
        "itens": novos,
        "importadas": len(novos),
        "substituidas": len(atuais) - len(mantidos),
        "documentos": list(dict.fromkeys(item["documento"] or "Sem documento" for item in novos)),
        "total": len(final),
    }


def _chave_documento(documento: str) -> str:
    codigo = re.split(r"\s+-\s+|\s+rev\.?\s*\d", documento, maxsplit=1, flags=re.IGNORECASE)[0]
    codigo = unicodedata.normalize("NFKD", codigo).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "-", codigo).strip("-") or "SEM-DOCUMENTO"


def _campo(item: dict, *nomes: str) -> str:
    for nome in nomes:
        valor = item.get(nome)
        if valor is not None and str(valor).strip():
            return str(valor).strip()
    return ""


def _tem_pergunta(item) -> bool:
    return isinstance(item, dict) and ("pergunta" in item or "question" in item)


def _secoes_da_referencia(referencia: str) -> list[str]:
    secoes: list[str] = []
    for achado in _REF_ITEM.finditer(referencia):
        secoes.append(achado.group(1))
    for achado in _REF_ANEXO.finditer(referencia):
        secoes.extend(f"Anexo {int(numero):02d}" for numero in re.findall(r"\d+", achado.group(1)))
    return list(dict.fromkeys(secoes))


def _documento_da_referencia(referencia: str) -> str:
    inicio = referencia.split(",", 1)[0].strip()
    if "," not in referencia or _REF_ITEM.search(inicio) or _REF_ANEXO.search(inicio):
        return ""
    return inicio


def _lista_de_perguntas(bruto, profundidade: int = 0) -> list:
    if isinstance(bruto, list):
        return bruto
    if not isinstance(bruto, dict) or profundidade > 2:
        return []
    if _tem_pergunta(bruto):
        return [bruto]
    for valor in bruto.values():
        if isinstance(valor, list) and any(_tem_pergunta(item) for item in valor):
            return valor
    for valor in bruto.values():
        if isinstance(valor, dict):
            achado = _lista_de_perguntas(valor, profundidade + 1)
            if achado:
                return achado
    return []


def gabarito(data_dir: Path) -> list[dict]:
    itens = ler_json(pasta(data_dir) / "gabarito.json", [])
    return [item for item in itens if isinstance(item, dict)] if isinstance(itens, list) else []


def revisoes(data_dir: Path) -> dict:
    guardado = ler_json(pasta(data_dir) / "revisao.json")
    if not isinstance(guardado, dict):
        return {"amostra": [], "itens": {}}
    return {"amostra": list(guardado.get("amostra") or []), "itens": dict(guardado.get("itens") or {})}


def revisao(data_dir: Path, tamanho: int = AMOSTRA) -> dict:
    itens = gabarito(data_dir)
    guardado = revisoes(data_dir)
    por_id = {item["id"]: item for item in itens}
    amostra = [identificador for identificador in guardado["amostra"] if identificador in por_id]
    if len(amostra) < min(tamanho, len(itens)):
        amostra = _sortear(itens, tamanho, amostra)
        guardado["amostra"] = amostra
        _gravar_revisoes(data_dir, guardado)
    lista = []
    for identificador in amostra:
        item = por_id[identificador]
        feita = guardado["itens"].get(identificador) or {}
        if feita and feita.get("pergunta") != item["pergunta"]:
            feita = {}
        lista.append({**_base(item), "revisao": feita})
    return {
        "itens": lista,
        "feitas": sum(1 for item in lista if item["revisao"]),
        "total": len(lista),
    }


def revisar(data_dir: Path, identificador: str, veredito: str, corrigida: str = "", observacao: str = "") -> dict:
    if veredito not in VEREDITOS:
        raise ValueError("Veredito desconhecido.")
    corrigida = corrigida.strip()
    if veredito == "corrigida" and not corrigida:
        raise ValueError("Escreva a resposta corrigida.")
    item = next((item for item in gabarito(data_dir) if item["id"] == identificador), None)
    if item is None:
        raise FileNotFoundError("Pergunta não encontrada no gabarito.")
    guardado = revisoes(data_dir)
    guardado["itens"][identificador] = {
        "veredito": veredito,
        "resposta_corrigida": corrigida if veredito == "corrigida" else "",
        "observacao": observacao.strip(),
        "pergunta": item["pergunta"],
        "quando": _agora(),
    }
    _gravar_revisoes(data_dir, guardado)
    return revisao(data_dir)


def desfazer_revisao(data_dir: Path, identificador: str) -> dict:
    guardado = revisoes(data_dir)
    guardado["itens"].pop(identificador, None)
    _gravar_revisoes(data_dir, guardado)
    return revisao(data_dir)


def _sortear(itens: list[dict], tamanho: int, ja: list[str]) -> list[str]:
    grupos: dict[str, list[str]] = {}
    for item in itens:
        grupos.setdefault(item.get("categoria") or "", []).append(item["id"])
    ordem: list[tuple[float, str, str]] = []
    for grupo in grupos.values():
        grupo.sort(key=lambda identificador: hashlib.sha256(identificador.encode("utf-8")).hexdigest())
        for posto, identificador in enumerate(grupo):
            sorteio = hashlib.sha256(identificador.encode("utf-8")).hexdigest()
            ordem.append(((posto + 0.5) / len(grupo), sorteio, identificador))
    livres = [identificador for _, _, identificador in sorted(ordem) if identificador not in ja]
    return list(ja) + livres[: max(0, min(tamanho, len(itens)) - len(ja))]


def _gravar_revisoes(data_dir: Path, guardado: dict) -> None:
    gravar_json(pasta(data_dir) / "revisao.json", guardado)


def _aplicar_revisoes(itens: list[dict], guardado: dict) -> list[dict]:
    saida = []
    for item in itens:
        feita = guardado["itens"].get(item["id"]) or {}
        if feita.get("pergunta") != item["pergunta"]:
            feita = {}
        veredito = feita.get("veredito", "")
        if veredito == "descartada":
            continue
        if veredito == "corrigida":
            item = {**item, "resposta_esperada": feita["resposta_corrigida"]}
        saida.append({**item, "revisao": veredito})
    return saida


def _resumo_revisado(itens: list[dict], guardado: dict) -> dict:
    conferidos = []
    for item in itens:
        feita = guardado["itens"].get(item["id"]) or {}
        veredito = feita.get("veredito")
        if feita.get("pergunta") != item["pergunta"] or item["status"] == "falha":
            continue
        if veredito == "confere" or (
            veredito == "corrigida" and item.get("resposta_esperada") == feita.get("resposta_corrigida")
        ):
            conferidos.append(item)
    acertos = sum(1 for item in conferidos if item["status"] == "acerto")
    return {
        "total": len(conferidos),
        "acertos": acertos,
        "acertividade": round(100 * acertos / len(conferidos), 1) if conferidos else 0.0,
    }


def estado(data_dir: Path) -> dict:
    itens = gabarito(data_dir)
    documentos: dict[str, int] = {}
    categorias: dict[str, int] = {}
    for item in itens:
        chave = item["documento"] or "Sem documento"
        documentos[chave] = documentos.get(chave, 0) + 1
        if item.get("categoria"):
            categorias[item["categoria"]] = categorias.get(item["categoria"], 0) + 1
    progresso = ler_progresso()
    guardado = revisoes(data_dir)
    return {
        "opcoes": {
            "modos": list(MODOS_CONTEXTO),
            "modo": MODO_CONTEXTO,
            "reranker": RERANKER_MODEL,
            "reranker_sugerido": RERANKER_SUGERIDO,
        },
        "revisadas": sum(1 for item in guardado["itens"].values() if item.get("veredito")),
        "gabarito": {
            "total": len(itens),
            "recusar": sum(1 for item in itens if item["deve_recusar"]),
            "documentos": [{"documento": nome, "perguntas": total} for nome, total in documentos.items()],
            "categorias": [{"categoria": nome, "perguntas": total} for nome, total in categorias.items()],
        },
        "rodadas": listar_rodadas(data_dir),
        "progresso": progresso,
    }


def ler_progresso() -> dict:
    with _TRAVA:
        return dict(_ESTADO)


def listar_rodadas(data_dir: Path, origem: str | None = "prova") -> list[dict]:
    """Rodadas antigas não têm origem: eram todas da prova."""
    return [rodada for rodada in _todas_rodadas(data_dir) if origem is None or (rodada.get("origem") or "prova") == origem]


def _todas_rodadas(data_dir: Path) -> list[dict]:
    rodadas = []
    for arquivo in sorted((pasta(data_dir) / "rodadas").glob("*.json")):
        try:
            stat = arquivo.stat()
        except FileNotFoundError:
            continue
        versao = (stat.st_size, stat.st_mtime_ns)
        guardado = _RESUMOS_RODADA.get(str(arquivo))
        if guardado and guardado[0] == versao:
            rodadas.append(guardado[1])
            continue
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        resumo = {
            chave: dados.get(chave) for chave in ("id", "quando", "versao", "modo", "reranker", "origem", "documento", "resumo")
        }
        _RESUMOS_RODADA[str(arquivo)] = (versao, resumo)
        rodadas.append(resumo)
    return rodadas


def ler_rodada(data_dir: Path, identificador: str) -> dict:
    if not re.fullmatch(r"[\w-]{1,40}", identificador):
        raise FileNotFoundError("Rodada não encontrada.")
    arquivo = pasta(data_dir) / "rodadas" / f"{identificador}.json"
    if not arquivo.is_file():
        raise FileNotFoundError("Rodada não encontrada.")
    rodada = ler_json(arquivo)
    if not isinstance(rodada, dict):
        raise FileNotFoundError("Rodada ilegível.")
    rodada["revisadas"] = _resumo_revisado(rodada.get("itens") or [], revisoes(data_dir))
    return rodada


def iniciar(
    data_dir: Path = DATA_DIR, acervo: Path = ACERVO_DIR, modo: str | None = None, reranker: str | None = None
) -> None:
    if not gabarito(data_dir):
        raise ValueError("Importe o gabarito antes de rodar.")
    estrategia(modo, reranker)
    with _TRAVA:
        if _ESTADO["rodando"]:
            raise ValueError("Já existe uma avaliação rodando.")
        _ESTADO.update(rodando=True, feitos=0, total=0, inicio=_agora(), rodada="", erro="")
    threading.Thread(target=_rodar_seguro, args=(data_dir, acervo, modo, reranker), daemon=True).start()


def _rodar_seguro(data_dir: Path, acervo: Path, modo: str | None, reranker: str | None) -> None:
    try:
        rodar(data_dir, acervo, modo=modo, reranker=reranker)
    except Exception as erro:
        with _TRAVA:
            _ESTADO.update(rodando=False, erro=str(erro))


def rodar(
    data_dir: Path = DATA_DIR,
    acervo: Path = ACERVO_DIR,
    gerador=None,
    juiz=None,
    embedder=None,
    indice=None,
    modo: str | None = None,
    reranker: str | None = None,
    reordenador=None,
    itens: list[dict] | None = None,
    origem: str = "prova",
    documento: str = "",
    com_recusas: bool = True,
    progresso=None,
) -> dict:
    """Sem itens, roda a prova (gabarito importado). Com itens, roda a amostra do treino sem mexer no progresso da prova."""
    prova = itens is None
    itens = _aplicar_revisoes(gabarito(data_dir), revisoes(data_dir)) if prova else itens
    if not itens:
        raise ValueError("Importe o gabarito antes de rodar.")
    if prova and com_recusas:
        itens = _com_recusas_padrao(itens)
    modo, reranker = estrategia(modo, reranker)
    config = {"modo": modo, "reranker": reranker, "reordenador": reordenador}
    indice = indice if indice is not None else carregar(data_dir)
    embedder = embedder or _embedder_travado()
    juiz = juiz or (lambda mensagens: gerar_openai(mensagens, JUIZ_MODEL))
    if prova:
        with _TRAVA:
            _ESTADO.update(rodando=True, feitos=0, total=len(itens), erro="")
            if not _ESTADO["inicio"]:
                _ESTADO["inicio"] = _agora()

    concluidas = [0]

    def avaliar(item: dict) -> dict:
        try:
            resultado = _avaliar(item, data_dir, acervo, indice, gerador, juiz, embedder, config)
        except Exception as erro:
            resultado = {**_base(item), "status": "falha", "motivo": str(erro)}
        with _TRAVA:
            concluidas[0] += 1
            if prova:
                _ESTADO["feitos"] += 1
        if progresso is not None:
            progresso(concluidas[0], len(itens))
        return resultado

    try:
        with ThreadPoolExecutor(max_workers=1 if modo == "documento" else PARALELO) as executor:
            resultados = list(executor.map(avaliar, itens))
        destino = pasta(data_dir) / "rodadas"
        destino.mkdir(parents=True, exist_ok=True)
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        identificador = base
        repeticao = 1
        while (destino / f"{identificador}.json").exists():
            repeticao += 1
            identificador = f"{base}-{repeticao}"
        rodada = {
            "id": identificador,
            "quando": _agora(),
            "versao": hashlib.sha256(_assinatura_resposta(indice, modo, reranker).encode("utf-8")).hexdigest()[:8],
            "modo": modo,
            "reranker": reranker,
            "origem": origem,
            "documento": documento,
            "resumo": _resumo(resultados),
            "comparacao": comparar(_rodada_anterior(data_dir, origem, documento), resultados),
            "itens": resultados,
        }
        gravar_json(destino / f"{identificador}.json", rodada)
    finally:
        if prova:
            with _TRAVA:
                _ESTADO.update(rodando=False, inicio="")
    if prova:
        with _TRAVA:
            _ESTADO["rodada"] = identificador
    try:
        confianca.treinar(data_dir)
    except Exception:
        logging.exception("Falha ao treinar o modelo de confiança.")
    return rodada


def _avaliar(item: dict, data_dir: Path, acervo: Path, indice, gerador, juiz, embedder, config: dict) -> dict:
    resultado = responder(
        item["pergunta"],
        autor="avaliacao",
        origem="avaliacao",
        data_dir=data_dir,
        acervo=acervo,
        indice=indice,
        gerador=gerador,
        embedder=embedder,
        **config,
    )
    custo = (
        custo_usd(
            resultado.modelo, resultado.tokens_prompt, resultado.tokens_resposta, resultado.cache, resultado.tokens_cache
        )
        or 0.0
    )
    custo += (
        custo_usd(resultado.modelo_aux, resultado.tokens_aux_prompt, resultado.tokens_aux_resposta, False) or 0.0
    )
    secoes_citadas = _com_secoes_acima(indice, resultado.citacoes)
    secoes_trechos = _com_secoes_acima(indice, resultado.trechos)
    saida = {
        **_base(item),
        "resposta": resultado.resposta,
        "coberto": resultado.coberto,
        "citacoes": resultado.citacoes,
        "cache": resultado.cache,
        "secao_ok": _secao_confere(item["secao"], secoes_citadas),
        "secao_recuperada": _secao_confere(item["secao"], secoes_trechos),
        "posicao_recuperada": _posicao_secao(item["secao"], indice, resultado.trechos),
        "status_resposta": resultado.status,
        "alertas": resultado.alertas,
        "valores_ausentes": [valor for valor in item["valores"] if _normal(valor) not in _normal(resultado.resposta)],
        "nota_juiz": "",
        "motivo": "",
        "sinais": resultado.sinais,
    }
    if item["deve_recusar"]:
        saida["status"] = "invencao" if resultado.coberto else "acerto"
        saida["motivo"] = (
            "Respondeu um ponto que o gabarito diz não estar no normativo."
            if resultado.coberto
            else "Recusou, como esperado."
        )
    elif not resultado.coberto:
        saida["status"] = "recusa_indevida"
        saida["motivo"] = (
            "A seção esperada foi recuperada, mas a resposta recusou."
            if saida["secao_recuperada"]
            else "A seção esperada não chegou aos trechos enviados ao GPT."
        )
    else:
        custo += _aplicar_juiz(saida, data_dir, item, juiz, _texto_citado(indice, resultado.citacoes))
    saida["causa"] = causa(saida)
    saida["custo_usd"] = round(custo, 8)
    return saida


def causa(item: dict) -> str:
    """Onde a resposta falhou: a busca não trouxe a seção, o GPT recusou com ela em mãos, errou o conteúdo ou inventou."""
    status = item.get("status")
    if status in {"acerto", "falha"}:
        return ""
    if status == "invencao":
        return "invencao"
    if item.get("secao_recuperada") is False:
        return "busca"
    return "recusa" if status == "recusa_indevida" else "conteudo"


def _aplicar_juiz(saida: dict, data_dir: Path, item: dict, juiz, trechos: str) -> float:
    nota, motivo, saida_juiz = _julgar(data_dir, item, saida["resposta"], juiz, trechos)
    saida["nota_juiz"] = nota
    saida["motivo"] = motivo
    regras_ok = saida["secao_ok"] is not False and not saida["valores_ausentes"]
    if nota == "correto" and regras_ok:
        saida["status"] = "acerto"
    elif nota == "incorreto":
        saida["status"] = "erro"
    else:
        saida["status"] = "parcial"
    if saida_juiz is None:
        return 0.0
    return custo_usd(JUIZ_MODEL, saida_juiz.tokens_prompt, saida_juiz.tokens_resposta, False, saida_juiz.tokens_cache) or 0.0


def _com_secoes_acima(indice, referencias: list[dict]) -> list[str]:
    secoes: list[str] = []
    for referencia in referencias:
        arquivo = str(referencia.get("arquivo") or "")
        secao = secao_base(str(referencia.get("secao") or ""))
        secoes.append(secao)
        trecho = next(
            (trecho for trecho in indice.trechos if trecho.arquivo == arquivo and secao_base(trecho.secao) == secao), None
        )
        if trecho is None:
            continue
        for linha in separar_cabecalho(trecho.texto)[0].splitlines():
            if linha.startswith("Dentro de: "):
                secoes.extend(linha[len("Dentro de: ") :].split(" > "))
    return secoes


def _texto_citado(indice, citacoes: list[dict]) -> str:
    partes: list[str] = []
    vistos: set[int] = set()
    for citacao in citacoes:
        arquivo = str(citacao.get("arquivo") or "")
        secao = str(citacao.get("secao") or "")
        for posicao, trecho in enumerate(indice.trechos):
            if posicao in vistos or trecho.arquivo != arquivo or secao_base(trecho.secao) != secao:
                continue
            vistos.add(posicao)
            partes.append(trecho.texto)
    return "\n\n".join(partes)[:_LIMITE_TRECHOS_JUIZ]


def rejulgar(data_dir: Path = DATA_DIR, identificador: str = "", juiz=None, indice=None) -> dict:
    original = ler_rodada(data_dir, identificador)
    indice = indice if indice is not None else carregar(data_dir)
    juiz = juiz or (lambda mensagens: gerar_openai(mensagens, JUIZ_MODEL))
    itens: list[dict] = []
    for item in original.get("itens") or []:
        item = dict(item)
        if item.get("coberto") and not item.get("deve_recusar") and item.get("status") in {"acerto", "parcial", "erro"}:
            try:
                custo = _aplicar_juiz(item, data_dir, item, juiz, _texto_citado(indice, item.get("citacoes") or []))
                item["custo_usd"] = round((item.get("custo_usd") or 0.0) + custo, 8)
            except Exception as erro:
                item.update(status="falha", motivo=str(erro))
            item["causa"] = causa(item)
        itens.append(item)
    destino = pasta(data_dir) / "rodadas"
    novo = f"{original['id']}-rejulgada"
    rodada = {
        "id": novo,
        "quando": _agora(),
        "versao": original.get("versao"),
        "modo": original.get("modo"),
        "reranker": original.get("reranker"),
        "origem": original.get("origem") or "prova",
        "documento": original.get("documento") or "",
        "rejulgada_de": original["id"],
        "resumo": _resumo(itens),
        "itens": itens,
    }
    gravar_json(destino / f"{novo}.json", rodada)
    return rodada


def _julgar(data_dir: Path, item: dict, resposta: str, juiz, trechos: str = "") -> tuple[str, str, gpt.Saida | None]:
    esperada = _normal(item["resposta_esperada"]).rstrip(".")
    if esperada and esperada in _normal(resposta):
        return "correto", "A resposta contém a resposta esperada.", None
    chave = hashlib.sha256(
        "\n".join([_JUIZ, JUIZ_MODEL, item["pergunta"], item["resposta_esperada"], resposta, trechos]).encode("utf-8")
    ).hexdigest()
    arquivo = pasta(data_dir) / "juiz" / f"{chave}.json"
    try:
        guardado = json.loads(arquivo.read_text(encoding="utf-8"))
        return guardado["nota"], guardado["motivo"], None
    except (OSError, ValueError, KeyError, TypeError):
        pass
    mensagens = [
        {"role": "system", "content": _JUIZ},
        {
            "role": "user",
            "content": (
                f"Pergunta:\n{item['pergunta']}\n\n"
                f"Resposta esperada:\n{item['resposta_esperada']}\n\n"
                f"Resposta do assistente:\n{resposta}"
                + (f"\n\nTrechos citados:\n{trechos}" if trechos else "")
            ),
        },
    ]
    saida = gpt.Saida(*juiz(mensagens))
    try:
        dados = json.loads(saida.texto)
    except json.JSONDecodeError:
        dados = None
    nota = str(dados.get("nota") or "").strip().casefold() if isinstance(dados, dict) else ""
    if nota not in {"correto", "parcial", "incorreto"}:
        return "parcial", "O juiz devolveu uma nota ilegível.", saida
    motivo = str(dados.get("motivo") or "O juiz não explicou a nota.").strip()
    gravar_json(arquivo, {"nota": nota, "motivo": motivo})
    return nota, motivo, saida


def _posicao_secao(esperada: str, indice, trechos: list[dict]) -> int | None:
    """Posição (1 = primeira) do primeiro trecho enviado ao GPT que contém a seção esperada."""
    if not esperada.strip():
        return None
    for posicao, trecho in enumerate(trechos, start=1):
        if _secao_confere(esperada, _com_secoes_acima(indice, [trecho])):
            return posicao
    return None


def _secao_confere(esperada: str, secoes: list[str]) -> bool | None:
    partes = [parte.strip() for parte in esperada.split(";") if parte.strip()]
    if not partes:
        return None
    return any(_uma_secao_confere(parte, secoes) for parte in partes)


def _uma_secao_confere(esperada: str, secoes: list[str]) -> bool:
    numero = _NUMERO.match(esperada)
    anexo = _ANEXO.match(_normal(esperada))
    for secao in secoes:
        secao = secao.strip()
        if numero and numero.group(1) == esperada.rstrip("."):
            achado = _NUMERO.match(secao)
            if achado and (achado.group(1) == numero.group(1) or achado.group(1).startswith(numero.group(1) + ".")):
                return True
        elif anexo:
            achado = _ANEXO.match(_normal(secao))
            if achado and int(achado.group(1)) == int(anexo.group(1)):
                return True
        elif _normal(secao).startswith(_normal(esperada)):
            return True
    return False


def _resumo(resultados: list[dict]) -> dict:
    contagem = {status: sum(1 for item in resultados if item["status"] == status) for status in STATUS}
    avaliados = len(resultados) - contagem["falha"]
    return {
        "total": len(resultados),
        "acertos": contagem["acerto"],
        "parciais": contagem["parcial"],
        "erros": contagem["erro"],
        "recusas_indevidas": contagem["recusa_indevida"],
        "invencoes": contagem["invencao"],
        "falhas": contagem["falha"],
        "acertividade": round(100 * contagem["acerto"] / avaliados, 1) if avaliados else 0.0,
        "do_cache": sum(1 for item in resultados if item.get("cache")),
        "custo_usd": round(sum(item.get("custo_usd") or 0.0 for item in resultados), 6),
        "por_categoria": _por_categoria(resultados),
        **_metricas_busca(resultados),
    }


def _metricas_busca(resultados: list[dict]) -> dict:
    """Qualidade da busca, separada da resposta: a seção esperada chegou ao GPT e em que posição."""
    com_secao = [
        item for item in resultados if item.get("secao") and not item["deve_recusar"] and item["status"] != "falha"
    ]
    if not com_secao:
        return {"recall_busca": None, "mrr_busca": None}
    posicoes = [item.get("posicao_recuperada") for item in com_secao]
    return {
        "recall_busca": round(100 * sum(1 for posicao in posicoes if posicao) / len(com_secao), 1),
        "mrr_busca": round(sum(1 / posicao for posicao in posicoes if posicao) / len(com_secao), 3),
    }


def comparar(anterior: dict | None, resultados: list[dict]) -> dict | None:
    """Compara com a rodada anterior pelos mesmos ids. Regressão é pergunta que acertava e deixou de acertar."""
    if not anterior:
        return None
    antes = {item["id"]: item["status"] for item in anterior.get("itens", [])}
    comuns = [item for item in resultados if item["id"] in antes]
    if not comuns:
        return None
    regressoes = [item["id"] for item in comuns if antes[item["id"]] == "acerto" and item["status"] != "acerto"]
    melhorias = [item["id"] for item in comuns if antes[item["id"]] != "acerto" and item["status"] == "acerto"]
    taxa_antes = 100 * sum(1 for item in comuns if antes[item["id"]] == "acerto") / len(comuns)
    taxa_agora = 100 * sum(1 for item in comuns if item["status"] == "acerto") / len(comuns)
    return {
        "anterior": anterior.get("id"),
        "comparadas": len(comuns),
        "variacao_acertividade": round(taxa_agora - taxa_antes, 1),
        "regressoes": regressoes,
        "melhorias": melhorias,
    }


def aprovada(rodada: dict, minimo: float, permitir_regressao: bool = False) -> tuple[bool, list[str]]:
    """Porta de regressão: a rodada só passa se atingir o mínimo, não inventar e não piorar o que já acertava."""
    motivos: list[str] = []
    resumo = rodada["resumo"]
    if resumo["acertividade"] < minimo:
        motivos.append(f"acertividade {resumo['acertividade']}% abaixo do mínimo de {minimo}%")
    if resumo["invencoes"]:
        motivos.append(f"{resumo['invencoes']} invenção(ões): respondeu o que devia recusar")
    comparacao = rodada.get("comparacao") or {}
    if comparacao.get("regressoes") and not permitir_regressao:
        motivos.append(f"{len(comparacao['regressoes'])} pergunta(s) que acertavam e pioraram")
    return not motivos, motivos


def _rodada_anterior(data_dir: Path, origem: str, documento: str) -> dict | None:
    for resumo in reversed(listar_rodadas(data_dir, origem)):
        if (resumo.get("documento") or "") != documento:
            continue
        try:
            return ler_rodada(data_dir, str(resumo["id"]))
        except FileNotFoundError:
            continue
    return None


def _por_categoria(resultados: list[dict]) -> list[dict]:
    grupos: dict[str, list[dict]] = {}
    for item in resultados:
        if item.get("categoria"):
            grupos.setdefault(item["categoria"], []).append(item)
    saida = []
    for categoria, itens in grupos.items():
        avaliados = [item for item in itens if item["status"] != "falha"]
        acertos = sum(1 for item in avaliados if item["status"] == "acerto")
        saida.append(
            {
                "categoria": categoria,
                "total": len(itens),
                "acertos": acertos,
                "acertividade": round(100 * acertos / len(avaliados), 1) if avaliados else 0.0,
            }
        )
    return saida


def _base(item: dict) -> dict:
    return {
        "id": item["id"],
        "pergunta": item["pergunta"],
        "resposta_esperada": item["resposta_esperada"],
        "documento": item["documento"],
        "secao": item["secao"],
        "referencia": item.get("referencia", ""),
        "categoria": item.get("categoria", ""),
        "valores": item["valores"],
        "deve_recusar": item["deve_recusar"],
        "revisao": item.get("revisao", ""),
    }


def _normal(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.casefold().split())


def _embedder_travado(nome: str = EMBED_MODEL):
    trava = threading.Lock()
    modelo = _modelo(nome)
    prefixo, _ = prefixos(nome)

    def embutir(textos: list[str]) -> list[np.ndarray]:
        with trava:
            return [np.asarray(vetor, dtype=np.float32) for vetor in modelo.embed([prefixo + texto for texto in textos])]

    return embutir


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")
