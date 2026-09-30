from __future__ import annotations

import json
from pathlib import Path

# Tabela pública padrão, USD por 1 milhão de tokens. Sem desconto de cache da OpenAI.
_PRECO_POR_MILHAO = {
    "gpt-4.1-mini": {"entrada": 0.40, "saida": 1.60},
}


def ler_uso(data_dir: Path) -> dict:
    eventos = _ler_eventos(data_dir / "logs" / "consultas.jsonl")
    consultas = []
    custo_total = 0.0
    tokens_entrada = 0
    tokens_saida = 0
    cobradas = 0
    em_cache = 0
    sem_tabela = 0
    for evento in reversed(eventos):
        item = _item(evento)
        if item["cache"]:
            em_cache += 1
        elif item["custo_usd"] is None:
            sem_tabela += 1
        else:
            cobradas += 1
            custo_total += item["custo_usd"]
            tokens_entrada += item["tokens_prompt"]
            tokens_saida += item["tokens_resposta"]
        consultas.append(item)
    return {
        "resumo": {
            "consultas": len(consultas),
            "cobradas": cobradas,
            "cache": em_cache,
            "sem_tabela": sem_tabela,
            "tokens_entrada": tokens_entrada,
            "tokens_saida": tokens_saida,
            "custo_usd": round(custo_total, 8),
            "tabela": {
                "modelo": "gpt-4.1-mini",
                "entrada_usd_por_milhao": _PRECO_POR_MILHAO["gpt-4.1-mini"]["entrada"],
                "saida_usd_por_milhao": _PRECO_POR_MILHAO["gpt-4.1-mini"]["saida"],
            },
        },
        "consultas": consultas,
    }


def _ler_eventos(caminho: Path) -> list[dict]:
    if not caminho.is_file():
        return []
    eventos = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        try:
            evento = json.loads(linha)
        except json.JSONDecodeError:
            continue
        if isinstance(evento, dict):
            eventos.append(evento)
    return eventos


def custo_usd(modelo: str, prompt: int, resposta: int, cache: bool = False) -> float | None:
    if cache:
        return 0.0
    preco = _PRECO_POR_MILHAO.get(modelo)
    if preco is None:
        return None
    return round((prompt * preco["entrada"] + resposta * preco["saida"]) / 1_000_000, 8)


def _item(evento: dict) -> dict:
    prompt = _inteiro(evento.get("tokens_prompt"))
    resposta = _inteiro(evento.get("tokens_resposta"))
    cache = bool(evento.get("cache"))
    modelo = str(evento.get("modelo") or "")
    custo = custo_usd(modelo, prompt, resposta, cache)
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
        "quando": str(evento.get("quando") or ""),
        "autor": str(evento.get("autor") or ""),
        "origem": str(evento.get("origem") or ""),
        "pergunta": str(evento.get("pergunta") or ""),
        "resposta": str(evento.get("resposta") or ""),
        "coberto": bool(evento.get("coberto")),
        "citacoes": citacoes,
        "tokens_prompt": prompt,
        "tokens_resposta": resposta,
        "cache": cache,
        "modelo": modelo,
        "custo_usd": custo,
    }


def _inteiro(valor) -> int:
    try:
        return max(int(valor or 0), 0)
    except (TypeError, ValueError):
        return 0
