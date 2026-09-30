from __future__ import annotations

import hashlib
import json
from pathlib import Path


def chave(pergunta: str, fingerprint: str, conversa: str = "") -> str:
    normalizada = " ".join(pergunta.casefold().split())
    contexto = " ".join(conversa.casefold().split())
    return hashlib.sha256(f"{fingerprint}\n{normalizada}\n{contexto}".encode("utf-8")).hexdigest()


def ler(data_dir: Path, identificador: str) -> dict | None:
    arquivo = data_dir / "cache" / f"{identificador}.json"
    if not arquivo.exists():
        return None
    return json.loads(arquivo.read_text(encoding="utf-8"))


def gravar(data_dir: Path, identificador: str, payload: dict) -> None:
    pasta = data_dir / "cache"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{identificador}.json"
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
