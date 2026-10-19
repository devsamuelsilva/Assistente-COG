from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

_TRAVA = threading.Lock()


def registrar(data_dir: Path, evento: dict, arquivo: str | None = None) -> str:
    arquivo = arquivo or ("avaliacao.jsonl" if evento.get("origem") == "avaliacao" else "consultas.jsonl")
    pasta = data_dir / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    identificador = evento.get("id") or uuid.uuid4().hex[:16]
    evento = {"quando": datetime.now().isoformat(timespec="seconds"), "id": identificador, **evento}
    linha = json.dumps(evento, ensure_ascii=False) + "\n"
    with _TRAVA, (pasta / arquivo).open("a", encoding="utf-8") as destino:
        destino.write(linha)
    return identificador


def registrar_retorno(data_dir: Path, consulta: str, util: bool, comentario: str = "") -> None:
    registrar(
        data_dir,
        {"consulta": consulta, "util": bool(util), "comentario": " ".join(comentario.split())[:1000]},
        arquivo="retornos.jsonl",
    )
