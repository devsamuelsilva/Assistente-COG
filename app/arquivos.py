from __future__ import annotations

import json
import os
import threading
from pathlib import Path


def gravar_bytes(caminho: Path, conteudo: bytes) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_name(f".{caminho.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporario.write_bytes(conteudo)
        os.replace(temporario, caminho)
    finally:
        temporario.unlink(missing_ok=True)


def gravar_texto(caminho: Path, texto: str) -> None:
    gravar_bytes(caminho, texto.encode("utf-8"))


def gravar_json(caminho: Path, payload) -> None:
    gravar_texto(caminho, json.dumps(payload, ensure_ascii=False, indent=2))


def ler_json(caminho: Path, padrao=None, codificacao: str = "utf-8"):
    try:
        return json.loads(caminho.read_text(encoding=codificacao))
    except (OSError, ValueError):
        return padrao
