"""Julga de novo, com a régua atual do juiz, as respostas de uma rodada gravada.

Não refaz as respostas: só o juiz roda outra vez. O resultado vira a rodada <id>-rejulgada.
Os trechos citados vêm do índice atual, então rode antes de reindexar se a quebra mudou.

Uso:
    .venv\\Scripts\\python scripts\\rejulgar.py 20260930-095841 20260930-101227
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.avaliacao import ler_rodada, rejulgar
from app.config import DATA_DIR


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argumentos.add_argument("rodadas", nargs="+")
    for identificador in argumentos.parse_args().rodadas:
        antes = ler_rodada(DATA_DIR, identificador)["resumo"]
        depois = rejulgar(DATA_DIR, identificador)
        resumo = depois["resumo"]
        print(
            f"{identificador}: acertividade {antes['acertividade']}% -> {resumo['acertividade']}% | "
            f"acertos {antes['acertos']} -> {resumo['acertos']} | parciais {antes['parciais']} -> {resumo['parciais']} | "
            f"erros {antes['erros']} -> {resumo['erros']} | gravada como {depois['id']}"
        )


if __name__ == "__main__":
    main()
