from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

_TRAVA = threading.Lock()


def registrar(data_dir: Path, evento: dict) -> None:
    pasta = data_dir / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    evento = {"quando": datetime.now().isoformat(timespec="seconds"), **evento}
    linha = json.dumps(evento, ensure_ascii=False) + "\n"
    with _TRAVA, (pasta / "consultas.jsonl").open("a", encoding="utf-8") as arquivo:
        arquivo.write(linha)
