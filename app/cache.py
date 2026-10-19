from __future__ import annotations

import hashlib
from pathlib import Path

from app.arquivos import gravar_json, ler_json


def chave(pergunta: str, fingerprint: str, conversa: str = "") -> str:
    normalizada = " ".join(pergunta.casefold().split())
    contexto = " ".join(conversa.casefold().split())
    return hashlib.sha256(f"{fingerprint}\n{normalizada}\n{contexto}".encode("utf-8")).hexdigest()


def ler(data_dir: Path, identificador: str) -> dict | None:
    guardado = ler_json(data_dir / "cache" / f"{identificador}.json")
    return guardado if isinstance(guardado, dict) else None


def gravar(data_dir: Path, identificador: str, payload: dict) -> None:
    gravar_json(data_dir / "cache" / f"{identificador}.json", payload)
