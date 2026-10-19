from __future__ import annotations

import os
import threading

_CACHE: dict[str, object] = {}
_TRAVA = threading.Lock()


def reordenar(modelo: str, pergunta: str, textos: list[str]) -> list[float]:
    if not textos:
        return []
    with _TRAVA:
        return [float(nota) for nota in _modelo(modelo).rerank(pergunta, textos)]


def aquecer(modelo: str) -> None:
    if modelo:
        with _TRAVA:
            _modelo(modelo)


def _modelo(nome: str):
    if nome not in _CACHE:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        _CACHE[nome] = TextCrossEncoder(model_name=nome, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
    return _CACHE[nome]
