from __future__ import annotations

import argparse
import sys

from app.config import ACERVO_DIR, DATA_DIR, HOST, PORT
from app.indice import indexar
from app.rag import formatar, responder


def main(argv: list[str] | None = None) -> int:
    for saida in (sys.stdout, sys.stderr):
        reconfigure = getattr(saida, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass
    parser = argparse.ArgumentParser(prog="python -m app", description="Assistente local do COG")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("indexar", help="Lê a pasta acervo e grava o índice em disco")
    perguntar = sub.add_parser("perguntar", help="Consulta o índice pelo terminal")
    perguntar.add_argument("pergunta", nargs="+")
    sub.add_parser("servir", help="Sobe a API local do ambiente web e do Teams")
    args = parser.parse_args(argv)

    if args.comando == "indexar":
        indice = indexar(ACERVO_DIR, DATA_DIR)
        vigentes = sum(1 for trecho in indice.trechos if trecho.vigente)
        print(f"Índice gravado em {DATA_DIR / 'indice'}")
        print(f"Trechos: {len(indice.trechos)} ({vigentes} vigentes)")
        return 0
    if args.comando == "perguntar":
        try:
            resultado = responder(" ".join(args.pergunta), autor="terminal", origem="terminal")
        except RuntimeError as erro:
            print(erro)
            return 1
        print(formatar(resultado))
        print(
            f"\nTokens: prompt={resultado.tokens_prompt} resposta={resultado.tokens_resposta} cache={resultado.cache}"
        )
        return 0
    if args.comando == "servir":
        import uvicorn

        uvicorn.run("app.server:app", host=HOST, port=PORT, reload=False)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
