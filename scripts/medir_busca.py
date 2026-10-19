"""Mede a busca contra o gabarito da avaliação, sem chamar o GPT.

Para cada pergunta com seção esperada, confere se essa seção chega ao contexto
e se é a primeira colocada. Os vetores de cada modelo ficam em data/medicao/.

Uso:
    .venv\\Scripts\\python scripts\\medir_busca.py --modelos sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 intfloat/multilingual-e5-large
    .venv\\Scripts\\python scripts\\medir_busca.py --reranker jinaai/jina-reranker-v2-base-multilingual
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from app.avaliacao import _com_secoes_acima, _secao_confere, gabarito
from app.buscar import buscar, escopo, expandir
from app.config import CANDIDATOS, DATA_DIR, EMBED_MODEL, TOP_K
from app.indice import Indice, _embutir, carregar, embutir_pergunta
from app.reranker import reordenar


def vetores(indice: Indice, modelo: str) -> np.ndarray:
    if modelo == indice.modelo:
        return indice.vetores
    textos = [trecho.texto for trecho in indice.trechos]
    assinatura = hashlib.sha256("\n".join([modelo, *textos]).encode("utf-8")).hexdigest()[:16]
    arquivo = DATA_DIR / "medicao" / f"{assinatura}.npy"
    if arquivo.is_file():
        return np.load(arquivo)
    inicio = time.perf_counter()
    calculados = _embutir(textos, modelo, None)
    print(f"  vetores de {modelo} calculados em {time.perf_counter() - inicio:.0f}s")
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    np.save(arquivo, calculados)
    return calculados


def medir(indice: Indice, itens: list[dict], modelo: str, reranker: str, modo: str, candidatos: int = CANDIDATOS) -> dict:
    perguntas = indice.vetores_perguntas if modelo == indice.modelo else None
    local = Indice(
        modelo=modelo,
        fingerprint=indice.fingerprint,
        trechos=indice.trechos,
        vetores=vetores(indice, modelo),
        vetores_perguntas=perguntas,
    )
    reordenador = (lambda consulta, textos: reordenar(reranker, consulta, textos)) if reranker else None
    no_contexto = primeiro = 0
    faltas: list[str] = []
    inicio = time.perf_counter()
    vigentes = local.vigentes()[0]
    for item in itens:
        arquivos, resto = escopo(item["pergunta"], vigentes)
        pergunta = expandir(resto)
        consulta = embutir_pergunta(modelo, pergunta)
        acertos = buscar(
            local, pergunta, consulta, top_k=TOP_K, reordenador=reordenador, candidatos=candidatos, modo=modo, arquivos=arquivos
        )
        secoes = _com_secoes_acima(local, [{"arquivo": acerto.trecho.arquivo, "secao": acerto.trecho.secao} for acerto in acertos])
        if _secao_confere(item["secao"], secoes):
            no_contexto += 1
        else:
            faltas.append(str(item["id"]))
        classificados = sorted(acertos, key=lambda acerto: -(acerto.rerank if acerto.rerank is not None else acerto.rrf))
        if classificados and _secao_confere(
            item["secao"], _com_secoes_acima(local, [{"arquivo": classificados[0].trecho.arquivo, "secao": classificados[0].trecho.secao}])
        ):
            primeiro += 1
    total = len(itens)
    return {
        "no_contexto": no_contexto,
        "primeiro": primeiro,
        "total": total,
        "segundos": (time.perf_counter() - inicio) / max(total, 1),
        "faltas": faltas,
    }


def main() -> None:
    argumentos = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    argumentos.add_argument("--modelos", nargs="+", default=[EMBED_MODEL])
    argumentos.add_argument("--reranker", default="")
    argumentos.add_argument("--modo", default="trechos", choices=["trechos", "secao", "documento"])
    argumentos.add_argument("--candidatos", type=int, default=CANDIDATOS, help="candidatos lidos pelo reranker")
    argumentos.add_argument("--faltas", action="store_true", help="lista os ids que não trouxeram a seção")
    opcoes = argumentos.parse_args()

    indice = carregar(DATA_DIR, modelo_esperado=EMBED_MODEL)
    itens = [item for item in gabarito(DATA_DIR) if item["secao"] and not item["deve_recusar"]]
    if not itens:
        raise SystemExit("Importe um gabarito com seções esperadas na aba Avaliação.")
    reranker = f"{opcoes.reranker} sobre {opcoes.candidatos} candidatos" if opcoes.reranker else "nenhum"
    print(f"{len(itens)} perguntas com seção esperada | modo {opcoes.modo} | reranker {reranker}")
    for modelo in opcoes.modelos:
        resultado = medir(indice, itens, modelo, opcoes.reranker, opcoes.modo, opcoes.candidatos)
        total = resultado["total"]
        print(
            f"{modelo}: seção no contexto {resultado['no_contexto']}/{total} "
            f"({100 * resultado['no_contexto'] / total:.1f}%) | em 1º {resultado['primeiro']}/{total} "
            f"({100 * resultado['primeiro'] / total:.1f}%) | {resultado['segundos']:.2f}s por pergunta"
        )
        if opcoes.faltas:
            print("  faltas:", ", ".join(resultado["faltas"]))


if __name__ == "__main__":
    main()
