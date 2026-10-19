from __future__ import annotations

import threading
from typing import NamedTuple

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL

_TIMEOUT = 60.0
_REPETICOES = 5
_TRAVA = threading.Lock()
_CLIENTE = None


def cliente(mensagem_sem_chave: str = "Defina OPENAI_API_KEY no arquivo .env."):
    global _CLIENTE
    if not OPENAI_API_KEY:
        raise RuntimeError(mensagem_sem_chave)
    with _TRAVA:
        if _CLIENTE is None:
            from openai import OpenAI

            argumentos = {"api_key": OPENAI_API_KEY, "timeout": _TIMEOUT, "max_retries": _REPETICOES}
            if OPENAI_BASE_URL:
                argumentos["base_url"] = OPENAI_BASE_URL
            _CLIENTE = OpenAI(**argumentos)
    return _CLIENTE


class Saida(NamedTuple):
    texto: str
    tokens_prompt: int
    tokens_resposta: int
    tokens_cache: int = 0
    fim: str = "stop"
    logprob: float | None = None


def json_simples(mensagens: list[dict], modelo: str) -> Saida:
    """Chamada barata que devolve JSON (reescrita da pergunta etc.), sem logprobs."""
    api = cliente()
    for formato in ({"type": "json_object"}, None):
        argumentos = {"model": modelo, "temperature": 0, "messages": mensagens}
        if formato:
            argumentos["response_format"] = formato
        try:
            resposta = api.chat.completions.create(**argumentos)
        except Exception as erro:
            texto_erro = str(erro).casefold()
            if formato and ("response_format" in texto_erro or "json_object" in texto_erro):
                continue
            raise
        uso = resposta.usage
        escolha = resposta.choices[0]
        return Saida(
            escolha.message.content or "",
            int(uso.prompt_tokens if uso else 0),
            int(uso.completion_tokens if uso else 0),
            tokens_cache(uso),
            escolha.finish_reason or "",
        )
    raise RuntimeError(f"O modelo {modelo} não aceitou a chamada.")


def tokens_cache(uso) -> int:
    detalhes = getattr(uso, "prompt_tokens_details", None)
    return int(getattr(detalhes, "cached_tokens", 0) or 0)


def logprob_medio(escolha) -> float | None:
    conteudo = getattr(getattr(escolha, "logprobs", None), "content", None)
    valores = [float(item.logprob) for item in conteudo or [] if getattr(item, "logprob", None) is not None]
    return round(sum(valores) / len(valores), 6) if valores else None


def turnos(historico: list[dict] | None) -> list[tuple[str, str]]:
    saida: list[tuple[str, str]] = []
    for item in (historico or [])[-4:]:
        if not isinstance(item, dict):
            continue
        pergunta = " ".join(str(item.get("pergunta") or "").split())[:1500]
        resposta = " ".join(str(item.get("resposta") or "").split())[:1500]
        if pergunta:
            saida.append((pergunta, resposta))
    return saida
