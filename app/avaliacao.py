from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from app.config import ACERVO_DIR, DATA_DIR, EMBED_MODEL, OPENAI_MODEL
from app.indice import _modelo, carregar
from app.rag import _assinatura_resposta, gerar_openai, responder
from app.uso import custo_usd

PARALELO = 4
STATUS = ("acerto", "parcial", "erro", "recusa_indevida", "invencao", "falha")

_JUIZ = """Você confere a resposta de um assistente de normativos contra a resposta esperada de um gabarito.
Compare só o conteúdo. Ignore diferença de redação, ordem e tamanho.
- "correto": a resposta traz a informação essencial da esperada e não contradiz nada dela.
- "parcial": acerta parte do essencial, mas falta algo essencial ou acrescenta algo que a esperada não confirma.
- "incorreto": contradiz a esperada, erra valor, prazo ou classificação, ou responde outra coisa.
Responda só com JSON: {"nota": "correto" | "parcial" | "incorreto", "motivo": "uma frase em português"}"""

_NUMERO = re.compile(r"^(\d+(?:\.\d+)*)")
_ANEXO = re.compile(r"^anexo\s+0*(\d+)\b")
_REF_ITEM = re.compile(r"\b(?:item|itens|tabela|quadro|se[cç][aã]o|subitem)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
_REF_ANEXO = re.compile(r"\banexos?\s+(\d+(?:\s*(?:,|e)\s*\d+)*)", re.IGNORECASE)
_CAMPOS_RESPOSTA = ("resposta_esperada", "resposta", "resposta_correta", "gabarito", "expected_answer", "answer")
_TRAVA = threading.Lock()
_ESTADO: dict = {"rodando": False, "feitos": 0, "total": 0, "inicio": "", "rodada": "", "erro": ""}


def pasta(data_dir: Path) -> Path:
    return data_dir / "avaliacao"


def importar(data_dir: Path, conteudo: bytes | str) -> list[dict]:
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
    destino = pasta(data_dir)
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "gabarito.json").write_text(json.dumps(itens, ensure_ascii=False, indent=2), encoding="utf-8")
    return itens


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
    arquivo = pasta(data_dir) / "gabarito.json"
    if not arquivo.is_file():
        return []
    return json.loads(arquivo.read_text(encoding="utf-8"))


def estado(data_dir: Path) -> dict:
    itens = gabarito(data_dir)
    documentos: dict[str, int] = {}
    categorias: dict[str, int] = {}
    for item in itens:
        chave = item["documento"] or "Sem documento"
        documentos[chave] = documentos.get(chave, 0) + 1
        if item.get("categoria"):
            categorias[item["categoria"]] = categorias.get(item["categoria"], 0) + 1
    with _TRAVA:
        progresso = dict(_ESTADO)
    return {
        "gabarito": {
            "total": len(itens),
            "recusar": sum(1 for item in itens if item["deve_recusar"]),
            "documentos": [{"documento": nome, "perguntas": total} for nome, total in documentos.items()],
            "categorias": [{"categoria": nome, "perguntas": total} for nome, total in categorias.items()],
        },
        "rodadas": listar_rodadas(data_dir),
        "progresso": progresso,
    }


def listar_rodadas(data_dir: Path) -> list[dict]:
    rodadas = []
    for arquivo in sorted((pasta(data_dir) / "rodadas").glob("*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rodadas.append({chave: dados.get(chave) for chave in ("id", "quando", "versao", "resumo")})
    return rodadas


def ler_rodada(data_dir: Path, identificador: str) -> dict:
    if not re.fullmatch(r"[\w-]{1,40}", identificador):
        raise FileNotFoundError("Rodada não encontrada.")
    arquivo = pasta(data_dir) / "rodadas" / f"{identificador}.json"
    if not arquivo.is_file():
        raise FileNotFoundError("Rodada não encontrada.")
    return json.loads(arquivo.read_text(encoding="utf-8"))


def iniciar(data_dir: Path = DATA_DIR, acervo: Path = ACERVO_DIR) -> None:
    if not gabarito(data_dir):
        raise ValueError("Importe o gabarito antes de rodar.")
    with _TRAVA:
        if _ESTADO["rodando"]:
            raise ValueError("Já existe uma avaliação rodando.")
        _ESTADO.update(rodando=True, feitos=0, total=0, inicio=_agora(), rodada="", erro="")
    threading.Thread(target=_rodar_seguro, args=(data_dir, acervo), daemon=True).start()


def _rodar_seguro(data_dir: Path, acervo: Path) -> None:
    try:
        rodar(data_dir, acervo)
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
) -> dict:
    itens = gabarito(data_dir)
    if not itens:
        raise ValueError("Importe o gabarito antes de rodar.")
    indice = indice if indice is not None else carregar(data_dir)
    embedder = embedder or _embedder_travado()
    juiz = juiz or gerar_openai
    with _TRAVA:
        _ESTADO.update(rodando=True, feitos=0, total=len(itens), erro="")
        if not _ESTADO["inicio"]:
            _ESTADO["inicio"] = _agora()

    def avaliar(item: dict) -> dict:
        try:
            resultado = _avaliar(item, data_dir, acervo, indice, gerador, juiz, embedder)
        except Exception as erro:
            resultado = {**_base(item), "status": "falha", "motivo": str(erro)}
        with _TRAVA:
            _ESTADO["feitos"] += 1
        return resultado

    try:
        with ThreadPoolExecutor(max_workers=PARALELO) as executor:
            resultados = list(executor.map(avaliar, itens))
        identificador = datetime.now().strftime("%Y%m%d-%H%M%S")
        rodada = {
            "id": identificador,
            "quando": _agora(),
            "versao": hashlib.sha256(_assinatura_resposta(indice).encode("utf-8")).hexdigest()[:8],
            "resumo": _resumo(resultados),
            "itens": resultados,
        }
        destino = pasta(data_dir) / "rodadas"
        destino.mkdir(parents=True, exist_ok=True)
        (destino / f"{identificador}.json").write_text(
            json.dumps(rodada, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        with _TRAVA:
            _ESTADO.update(rodando=False, inicio="")
    with _TRAVA:
        _ESTADO["rodada"] = identificador
    return rodada


def _avaliar(item: dict, data_dir: Path, acervo: Path, indice, gerador, juiz, embedder) -> dict:
    resultado = responder(
        item["pergunta"],
        autor="avaliacao",
        origem="avaliacao",
        data_dir=data_dir,
        acervo=acervo,
        indice=indice,
        gerador=gerador,
        embedder=embedder,
    )
    custo = custo_usd(resultado.modelo, resultado.tokens_prompt, resultado.tokens_resposta, resultado.cache) or 0.0
    secoes_citadas = [citacao.get("secao", "") for citacao in resultado.citacoes]
    secoes_trechos = [trecho.get("secao", "") for trecho in resultado.trechos]
    saida = {
        **_base(item),
        "resposta": resultado.resposta,
        "coberto": resultado.coberto,
        "citacoes": resultado.citacoes,
        "cache": resultado.cache,
        "secao_ok": _secao_confere(item["secao"], secoes_citadas),
        "secao_recuperada": _secao_confere(item["secao"], secoes_trechos),
        "valores_ausentes": [valor for valor in item["valores"] if _normal(valor) not in _normal(resultado.resposta)],
        "nota_juiz": "",
        "motivo": "",
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
        nota, motivo, tokens_prompt, tokens_resposta, em_cache = _julgar(data_dir, item, resultado.resposta, juiz)
        custo += custo_usd(OPENAI_MODEL, tokens_prompt, tokens_resposta, em_cache) or 0.0
        saida["nota_juiz"] = nota
        saida["motivo"] = motivo
        regras_ok = saida["secao_ok"] is not False and not saida["valores_ausentes"]
        if nota == "correto" and regras_ok:
            saida["status"] = "acerto"
        elif nota == "incorreto":
            saida["status"] = "erro"
        else:
            saida["status"] = "parcial"
    saida["custo_usd"] = round(custo, 8)
    return saida


def _julgar(data_dir: Path, item: dict, resposta: str, juiz) -> tuple[str, str, int, int, bool]:
    chave = hashlib.sha256(
        "\n".join([_JUIZ, item["pergunta"], item["resposta_esperada"], resposta]).encode("utf-8")
    ).hexdigest()
    arquivo = pasta(data_dir) / "juiz" / f"{chave}.json"
    if arquivo.is_file():
        guardado = json.loads(arquivo.read_text(encoding="utf-8"))
        return guardado["nota"], guardado["motivo"], 0, 0, True
    mensagens = [
        {"role": "system", "content": _JUIZ},
        {
            "role": "user",
            "content": (
                f"Pergunta:\n{item['pergunta']}\n\n"
                f"Resposta esperada:\n{item['resposta_esperada']}\n\n"
                f"Resposta do assistente:\n{resposta}"
            ),
        },
    ]
    bruto, tokens_prompt, tokens_resposta = juiz(mensagens)
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        dados = {}
    nota = str(dados.get("nota") or "").strip().casefold()
    if nota not in {"correto", "parcial", "incorreto"}:
        nota = "parcial"
    motivo = str(dados.get("motivo") or "O juiz não explicou a nota.").strip()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps({"nota": nota, "motivo": motivo}, ensure_ascii=False, indent=2), encoding="utf-8")
    return nota, motivo, tokens_prompt, tokens_resposta, False


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
    }


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
    }


def _normal(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.casefold().split())


def _embedder_travado():
    trava = threading.Lock()
    modelo = _modelo(EMBED_MODEL)

    def embutir(textos: list[str]) -> list[np.ndarray]:
        with trava:
            return [np.asarray(vetor, dtype=np.float32) for vetor in modelo.embed(textos)]

    return embutir


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")
