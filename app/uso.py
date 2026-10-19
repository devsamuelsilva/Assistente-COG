from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path

from app import registro
from app.arquivos import gravar_json, ler_json

# Tabela pública padrão, USD por 1 milhão de tokens. "entrada_cache" vale para a parte da entrada que a
# OpenAI informa como reaproveitada (cached_tokens); consultas antigas não registram esse número.
_PRECO_POR_MILHAO = {
    "gpt-4.1-mini": {"entrada": 0.40, "entrada_cache": 0.10, "saida": 1.60},
    "gpt-4.1": {"entrada": 2.00, "entrada_cache": 0.50, "saida": 8.00},
    "gpt-4o": {"entrada": 2.50, "entrada_cache": 1.25, "saida": 10.00},
    "gpt-4o-mini": {"entrada": 0.15, "entrada_cache": 0.075, "saida": 0.60},
}
_JANELA_CONVERSA = timedelta(minutes=10)
_REMETE = re.compile(
    r"^\s*(e|mas|só|so|então|entao|agora|também|tambem|se|para|pra|com|sem)\b"
    r"|\b(deste|desse|neste|nesse)\b(?!\s+(normativo|documento|itom))"
    r"|\b(disso|disto|nisso|dele|dela|deles|delas|montante)\b",
    re.IGNORECASE,
)
SEM_DOCUMENTO = "Sem documento identificado"
# Um normativo vira candidato a treino quando muita pergunta dele fica sem resposta.
MINIMO_PERGUNTAS_TREINO = 5
LIMIAR_SEM_RESPOSTA = 0.25
ORIGENS = {"normativo": "Normativo", "ons": "Chat ONS", "avaliacao": "Avaliação"}
_LEITORES: dict[Path, _Leitor] = {}
_TRAVA_LEITORES = threading.Lock()


class _Leitor:
    def __init__(self, caminho: Path) -> None:
        self.caminho = caminho
        self.trava = threading.Lock()
        self.eventos: list[dict] = []
        self.posicao = 0
        self.resto = b""
        self.identidade: tuple[int, int] | None = None

    def ler(self) -> list[dict]:
        with self.trava:
            try:
                stat = self.caminho.stat()
            except FileNotFoundError:
                self._zerar(None)
                return []
            identidade = (stat.st_dev, stat.st_ino)
            if identidade != self.identidade or stat.st_size < self.posicao:
                self._zerar(identidade)
            if stat.st_size > self.posicao:
                with self.caminho.open("rb") as origem:
                    origem.seek(self.posicao)
                    novo = origem.read()
                    self.posicao = origem.tell()
                *linhas, self.resto = (self.resto + novo).split(b"\n")
                self.eventos.extend(evento for evento in map(_evento, linhas) if evento is not None)
            ultima = _evento(self.resto)
            return self.eventos + [ultima] if ultima else list(self.eventos)

    def _zerar(self, identidade: tuple[int, int] | None) -> None:
        self.eventos = []
        self.posicao = 0
        self.resto = b""
        self.identidade = identidade


def _evento(linha: bytes) -> dict | None:
    if not linha.strip():
        return None
    try:
        evento = json.loads(linha)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return evento if isinstance(evento, dict) else None


def _ler_eventos(caminho: Path) -> list[dict]:
    with _TRAVA_LEITORES:
        leitor = _LEITORES.setdefault(caminho.resolve(), _Leitor(caminho))
    return leitor.ler()


def migrar_logs(data_dir: Path) -> bool:
    logs = data_dir / "logs"
    origem = logs / "consultas.jsonl"
    with registro._TRAVA:
        if not origem.is_file():
            return False
        texto = origem.read_text(encoding="utf-8")
        if '"trechos"' not in texto and '"avaliacao"' not in texto:
            return False
        manter: list[str] = []
        avaliacao: list[str] = []
        for linha in texto.splitlines():
            evento = _evento(linha.encode("utf-8"))
            if evento is None:
                if linha.strip():
                    manter.append(linha)
                continue
            evento.pop("trechos", None)
            destino = avaliacao if evento.get("origem") == "avaliacao" else manter
            destino.append(json.dumps(evento, ensure_ascii=False))
        copia = logs / "consultas.jsonl.bak"
        if not copia.exists():
            shutil.copyfile(origem, copia)
        _substituir(logs / "avaliacao.jsonl", avaliacao + _linhas(logs / "avaliacao.jsonl"))
        _substituir(origem, manter)
    return True


def _linhas(caminho: Path) -> list[str]:
    if not caminho.is_file():
        return []
    return [linha for linha in caminho.read_text(encoding="utf-8").splitlines() if linha.strip()]


def _substituir(caminho: Path, linhas: list[str]) -> None:
    temporario = caminho.with_name(f".{caminho.name}.tmp")
    temporario.write_text("".join(f"{linha}\n" for linha in linhas), encoding="utf-8")
    os.replace(temporario, caminho)


def itens(data_dir: Path) -> list[dict]:
    consultas = _ler_eventos(data_dir / "logs" / "consultas.jsonl")
    avaliacao = _ler_eventos(data_dir / "logs" / "avaliacao.jsonl")
    retornos: dict[str, dict] = {}
    for retorno in _ler_eventos(data_dir / "logs" / "retornos.jsonl"):
        if retorno.get("consulta"):
            retornos[str(retorno["consulta"])] = {
                "util": bool(retorno.get("util")),
                "comentario": str(retorno.get("comentario") or ""),
                "quando": str(retorno.get("quando") or ""),
            }
    lista = []
    for evento, continuacao in zip(consultas, _continuacoes(consultas)):
        item = _item(evento)
        item["continuacao"] = continuacao
        item["retorno"] = retornos.get(item["id"]) if item["id"] else None
        lista.append(item)
    for evento in avaliacao:
        lista.append({**_item(evento), "continuacao": False, "retorno": None})
    lista.reverse()
    lista.sort(key=lambda item: item["quando"], reverse=True)
    return lista


def _arquivo_marco(data_dir: Path) -> Path:
    return data_dir / "uso" / "marco.json"


def marco(data_dir: Path) -> str:
    """Ponto de partida da contagem de custo ("" = tudo). Nada é apagado: só o que veio antes deixa de somar."""
    dados = ler_json(_arquivo_marco(data_dir))
    return str(dados.get("desde") or "") if isinstance(dados, dict) else ""


def zerar(data_dir: Path) -> str:
    desde = datetime.now().isoformat(timespec="seconds")
    gravar_json(_arquivo_marco(data_dir), {"desde": desde})
    return desde


def somar_tudo(data_dir: Path) -> None:
    _arquivo_marco(data_dir).unlink(missing_ok=True)


def ler_uso(data_dir: Path) -> dict:
    todos = itens(data_dir)
    desde = marco(data_dir)
    contados = [item for item in todos if item["quando"] >= desde] if desde else todos
    return {
        "resumo": _resumo(contados),
        "origens": [
            {"origem": chave, "rotulo": rotulo, **_resumo([item for item in contados if grupo(item) == chave])}
            for chave, rotulo in ORIGENS.items()
        ],
        "dias": _dias(contados),
        "documentos": _documentos(contados),
        "desde": desde,
        "antes_do_marco": len(todos) - len(contados),
        "custo_total_historico": _resumo(todos)["custo_usd"],
        "ranking_documentos": ranking_documentos(data_dir, contados),
    }


def _documento_conhecido(evento: dict, citacoes: list[dict]) -> str:
    principal = evento.get("documento_principal")
    if isinstance(principal, list) and principal:
        return str(principal[0])
    return next((str(citacao["codigo"] or "Sem código") for citacao in citacoes), "")


def _completar_documentos(data_dir: Path, lista: list[dict]) -> None:
    """Dá a cada pergunta o nome padrão do normativo e recupera o de consulta antiga recusada.

    O mesmo normativo aparece com nomes diferentes no registro: o código com revisão e cópia ("ITOM EGP 01 Rev.02
    (1)"), o código sem elas, o nome do arquivo. O índice sabe qual é qual. Consulta antiga recusada não guardou o
    documento; quando a pergunta cita o código dele, recupera por aí."""
    try:
        from app.buscar import escopo
        from app.chunking import codigo_limpo
        from app.indice import carregar

        vigentes = carregar(data_dir).vigentes()[0]
    except Exception:
        return
    nomes: dict[str, str] = {}
    por_arquivo: dict[str, str] = {}
    for trecho in vigentes:
        padrao = codigo_limpo(trecho.codigo) or trecho.arquivo
        por_arquivo.setdefault(trecho.arquivo, padrao)
        for apelido in (trecho.codigo, codigo_limpo(trecho.codigo), trecho.arquivo, Path(trecho.arquivo).stem):
            if apelido:
                nomes.setdefault(apelido, padrao)
    for item in lista:
        if item["documento"]:
            item["documento"] = nomes.get(item["documento"], item["documento"])
            continue
        arquivos, _ = escopo(item["pergunta"], vigentes)
        if arquivos:
            item["documento"] = por_arquivo.get(sorted(arquivos)[0], "")


def ranking_documentos(data_dir: Path, lista: list[dict]) -> list[dict]:
    """Por normativo: quantas perguntas, quanto custaram e quantas ficaram sem resposta. Mostra onde treinar.

    Cada pergunta pertence a um só documento (o primeiro citado; se recusada, o mais provável), então os totais
    somam o custo do chat normativo. A avaliação e o chat ONS ficam de fora."""
    reais = [item for item in lista if grupo(item) == "normativo"]
    _completar_documentos(data_dir, reais)
    grupos: dict[str, dict] = {}
    for item in reais:
        codigo = item["documento"] or SEM_DOCUMENTO
        atual = grupos.setdefault(
            codigo,
            {"codigo": codigo, "perguntas": 0, "custo_usd": 0.0, "cache": 0, "sem_resposta": 0, "parciais": 0, "uteis": 0, "nao_uteis": 0},
        )
        atual["perguntas"] += 1
        atual["custo_usd"] += 0.0 if item["cache"] else (item["custo_usd"] or 0.0)
        atual["cache"] += 1 if item["cache"] else 0
        atual["sem_resposta"] += 0 if item["coberto"] else 1
        atual["parciais"] += 1 if item["status"] == "parcial" else 0
        if item["retorno"]:
            atual["uteis" if item["retorno"]["util"] else "nao_uteis"] += 1
    total = sum(grupo_["custo_usd"] for grupo_ in grupos.values()) or 1.0
    saida = []
    for atual in grupos.values():
        perguntas = atual["perguntas"]
        fracao = atual["sem_resposta"] / perguntas
        saida.append(
            {
                **atual,
                "custo_usd": round(atual["custo_usd"], 8),
                "custo_por_pergunta": round(atual["custo_usd"] / perguntas, 8),
                "parte_do_custo": round(atual["custo_usd"] / total, 4),
                "fracao_sem_resposta": round(fracao, 4),
                "candidato_a_treino": atual["codigo"] != SEM_DOCUMENTO
                and perguntas >= MINIMO_PERGUNTAS_TREINO
                and fracao >= LIMIAR_SEM_RESPOSTA,
            }
        )
    return sorted(saida, key=lambda linha: (linha["custo_usd"], linha["perguntas"]), reverse=True)


def grupo(item: dict) -> str:
    if item["origem"] in {"ons", "avaliacao"}:
        return item["origem"]
    return "normativo"


def _resumo(lista: list[dict]) -> dict:
    cobradas = [item for item in lista if not item["cache"] and item["custo_usd"] is not None]
    return {
        "consultas": len(lista),
        "cobradas": len(cobradas),
        "cache": sum(1 for item in lista if item["cache"]),
        "sem_tabela": sum(1 for item in lista if not item["cache"] and item["custo_usd"] is None),
        "tokens_entrada": sum(item["tokens_prompt"] for item in cobradas),
        "tokens_reaproveitados": sum(item["tokens_cache"] for item in cobradas),
        "tokens_saida": sum(item["tokens_resposta"] for item in cobradas),
        "custo_usd": round(sum(item["custo_usd"] for item in cobradas), 8),
        "uteis": sum(1 for item in lista if item["retorno"] and item["retorno"]["util"]),
        "nao_uteis": sum(1 for item in lista if item["retorno"] and not item["retorno"]["util"]),
        "tabela": {
            "modelo": "gpt-4.1-mini",
            "entrada_usd_por_milhao": _PRECO_POR_MILHAO["gpt-4.1-mini"]["entrada"],
            "entrada_cache_usd_por_milhao": _PRECO_POR_MILHAO["gpt-4.1-mini"]["entrada_cache"],
            "saida_usd_por_milhao": _PRECO_POR_MILHAO["gpt-4.1-mini"]["saida"],
        },
    }


def _dias(lista: list[dict]) -> list[dict]:
    dias: dict[str, dict] = {}
    for item in reversed(lista):
        dia = item["quando"][:10] or "sem data"
        atual = dias.setdefault(
            dia, {"dia": dia, "custo": 0.0, "cobradas": 0, "cache": 0, "origens": dict.fromkeys(ORIGENS, 0.0)}
        )
        if item["cache"]:
            atual["cache"] += 1
        elif item["custo_usd"] is not None:
            atual["cobradas"] += 1
            atual["custo"] += item["custo_usd"]
            atual["origens"][grupo(item)] += item["custo_usd"]
    return list(dias.values())


def _documentos(lista: list[dict]) -> list[dict]:
    custos: dict[str, float] = {}
    for item in lista:
        codigos = list(dict.fromkeys(citacao["codigo"] or "Sem código" for citacao in item["citacoes"]))
        codigos = codigos or ["Sem documento citado"]
        for codigo in codigos:
            custos[codigo] = custos.get(codigo, 0.0) + (item["custo_usd"] or 0.0) / len(codigos)
    return [
        {"codigo": codigo, "custo": round(custo, 8)}
        for codigo, custo in sorted(custos.items(), key=lambda par: par[1], reverse=True)
        if custo > 0
    ]


def perguntas(data_dir: Path, canal: str, limite: int = 16) -> list[str]:
    ons = canal == "ons"
    vistas: set[str] = set()
    lista = []
    for item in itens(data_dir):
        if not item["coberto"] or item["continuacao"] or item["origem"] == "avaliacao":
            continue
        if (item["origem"] == "ons") != ons:
            continue
        pergunta = item["pergunta"].strip()
        chave = pergunta.casefold()
        if len(pergunta) < 8 or chave in vistas:
            continue
        vistas.add(chave)
        lista.append(pergunta)
        if len(lista) >= limite:
            break
    return lista


def consultas(data_dir: Path, limite: int = 10, busca: str = "", nao_uteis: bool = False, documento: str = "") -> dict:
    lista = [item for item in itens(data_dir) if item["origem"] != "avaliacao"]
    if documento:
        _completar_documentos(data_dir, lista)
        lista = [item for item in lista if (item["documento"] or SEM_DOCUMENTO) == documento]
    termo = " ".join(busca.split()).casefold()
    escolhidas = [item for item in lista if not nao_uteis or (item["retorno"] and not item["retorno"]["util"])]
    if termo:
        escolhidas = [item for item in escolhidas if termo in f"{item['pergunta']}\n{item['resposta']}".casefold()]
    elif not nao_uteis:
        escolhidas = escolhidas[:limite]
    resumo = _resumo(lista)
    return {"consultas": escolhidas, "uteis": resumo["uteis"], "nao_uteis": resumo["nao_uteis"]}


def medidas_ons(data_dir: Path, limite: int = 15) -> list[dict]:
    return [item for item in itens(data_dir) if item["origem"] == "ons" and item["duracao_ms"] is not None][:limite]


def _continuacoes(eventos: list[dict]) -> list[bool]:
    # Consultas antigas não gravam "continuacao". Nelas, conta como continuação a pergunta do mesmo autor no
    # mesmo chat até 10 minutos depois da anterior e que remete a ela ("e pra RAO?", "quanto deste montante").
    anterior: dict[tuple[str, bool], datetime] = {}
    saida = []
    for evento in eventos:
        origem = str(evento.get("origem") or "")
        quando = _data(evento.get("quando"))
        chave = (str(evento.get("autor") or ""), origem == "ons")
        if "continuacao" in evento:
            saida.append(bool(evento["continuacao"]))
        elif origem == "avaliacao":
            saida.append(False)
        else:
            ultimo = anterior.get(chave)
            perto = bool(quando and ultimo and timedelta(0) <= quando - ultimo <= _JANELA_CONVERSA)
            saida.append(perto and bool(_REMETE.search(str(evento.get("pergunta") or ""))))
        if quando and origem != "avaliacao":
            anterior[chave] = quando
    return saida


def _data(valor) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor))
    except ValueError:
        return None


def custo_usd(modelo: str, prompt: int, resposta: int, cache: bool = False, prompt_cache: int = 0) -> float | None:
    if cache:
        return 0.0
    preco = _PRECO_POR_MILHAO.get(modelo)
    if preco is None:
        return None
    reaproveitado = min(max(prompt_cache, 0), prompt)
    entrada = (prompt - reaproveitado) * preco["entrada"] + reaproveitado * preco["entrada_cache"]
    return round((entrada + resposta * preco["saida"]) / 1_000_000, 8)


def _item(evento: dict) -> dict:
    prompt = _inteiro(evento.get("tokens_prompt"))
    prompt_cache = _inteiro(evento.get("tokens_cache"))
    resposta = _inteiro(evento.get("tokens_resposta"))
    cache = bool(evento.get("cache"))
    modelo = str(evento.get("modelo") or "")
    custo = custo_usd(modelo, prompt, resposta, cache, prompt_cache)
    # A reescrita da pergunta é paga mesmo quando a resposta sai do cache.
    aux = custo_usd(
        str(evento.get("modelo_aux") or ""), _inteiro(evento.get("tokens_aux_prompt")), _inteiro(evento.get("tokens_aux_resposta"))
    )
    if aux is not None:
        custo = None if custo is None else round(custo + aux, 8)
    citacoes = []
    for citacao in evento.get("citacoes") or []:
        if isinstance(citacao, dict):
            citacoes.append(
                {
                    "codigo": citacao.get("codigo") or "",
                    "revisao": citacao.get("revisao") or "",
                    "secao": citacao.get("secao") or "",
                }
            )
    return {
        "id": str(evento.get("id") or ""),
        "quando": str(evento.get("quando") or ""),
        "autor": str(evento.get("autor") or ""),
        "origem": str(evento.get("origem") or ""),
        "pergunta": str(evento.get("pergunta") or ""),
        "resposta": str(evento.get("resposta") or ""),
        "coberto": bool(evento.get("coberto")),
        "documento": _documento_conhecido(evento, citacoes),
        "status": str(evento.get("status") or ""),
        "alertas": [str(alerta) for alerta in evento.get("alertas") or []],
        "citacoes": citacoes,
        "tokens_prompt": prompt,
        "tokens_cache": prompt_cache,
        "tokens_resposta": resposta,
        "cache": cache,
        "modelo": modelo,
        "custo_usd": custo,
        "duracao_ms": _inteiro(evento.get("duracao_ms")) if "duracao_ms" in evento else None,
        "passos": _inteiro(evento.get("passos")),
        "ms_gpt": _inteiro(evento.get("ms_gpt")),
        "ms_ons": _inteiro(evento.get("ms_ons")),
        "repeticoes": _inteiro(evento.get("repeticoes")),
    }


def _inteiro(valor) -> int:
    try:
        return max(int(valor or 0), 0)
    except (TypeError, ValueError):
        return 0
