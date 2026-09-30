from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from app.chunking import Trecho
from app.indice import Indice

_TOKEN = re.compile(r"\w+", re.UNICODE)
_RRF_K = 60
_GLOSSARIO = {
    "wtg": ("aerogerador", "aerogeradores"),
    "su": ("subestação", "unitária"),
    "seccionamento": ("desligamento", "isolação", "seccionadora"),
    "urgências": ("urgência",),
    "urgencias": ("urgência",),
}


@dataclass
class Acerto:
    trecho: Trecho
    coseno: float
    bm25: float
    rrf: float


def buscar(indice: Indice, pergunta: str, consulta: np.ndarray, top_k: int = 6) -> list[Acerto]:
    trechos, vetores = indice.vigentes()
    if not trechos:
        return []
    pergunta = expandir(pergunta)
    cosenos = _cosenos(vetores, consulta)
    bm25 = _bm25(trechos, pergunta)
    ordem_vetor = np.argsort(-cosenos)[:top_k]
    ordem_texto = np.argsort(-bm25)[:top_k]
    pontuacao: dict[int, float] = {}
    for posto, indice_trecho in enumerate(ordem_vetor):
        pontuacao[int(indice_trecho)] = pontuacao.get(int(indice_trecho), 0.0) + 1.0 / (_RRF_K + posto + 1)
    for posto, indice_trecho in enumerate(ordem_texto):
        if bm25[indice_trecho] <= 0:
            continue
        pontuacao[int(indice_trecho)] = pontuacao.get(int(indice_trecho), 0.0) + 1.0 / (_RRF_K + posto + 1)
    ordenados = sorted(pontuacao, key=pontuacao.get, reverse=True)[:top_k]
    ordenados = _com_continuacao(trechos, ordenados, limite=max(top_k, 10))
    return [
        Acerto(
            trecho=trechos[indice_trecho],
            coseno=float(cosenos[indice_trecho]),
            bm25=float(bm25[indice_trecho]),
            rrf=float(pontuacao.get(indice_trecho, 0.0)),
        )
        for indice_trecho in ordenados
    ]


def _com_continuacao(trechos: list[Trecho], escolhidos: list[int], limite: int) -> list[int]:
    resultado: list[int] = []
    vistos: set[int] = set()
    for indice in escolhidos:
        if indice in vistos or len(resultado) >= limite:
            continue
        resultado.append(indice)
        vistos.add(indice)
        proximo = indice + 1
        while proximo < len(trechos) and len(resultado) < limite and proximo not in vistos:
            anterior = trechos[proximo - 1]
            atual = trechos[proximo]
            if anterior.arquivo != atual.arquivo or not _parte_seguida(anterior.secao, atual.secao):
                break
            resultado.append(proximo)
            vistos.add(proximo)
            proximo += 1
    for indice in reversed(_figuras_relacionadas(trechos, resultado)):
        if indice in vistos:
            continue
        resultado.insert(0, indice)
        vistos.add(indice)
    return resultado[:limite]


def _figuras_relacionadas(trechos: list[Trecho], escolhidos: list[int]) -> list[int]:
    bases_vistas: set[tuple[str, str]] = set()
    figuras: list[int] = []
    for indice in escolhidos:
        trecho = trechos[indice]
        base, _ = _parte(trecho.secao)
        if not base:
            continue
        chave = (trecho.arquivo, base)
        if chave in bases_vistas:
            continue
        bases_vistas.add(chave)
        inicio = next(
            (
                outro
                for outro, candidato in enumerate(trechos)
                if candidato.arquivo == trecho.arquivo
                and _parte(candidato.secao)[0] == base
                and "Transcrição da figura" in candidato.texto
            ),
            None,
        )
        if inicio is None:
            continue
        atual = inicio
        bloco = 0
        while bloco < 8:
            figuras.append(atual)
            bloco += 1
            if "Fim da transcrição da figura." in trechos[atual].texto:
                break
            proximo = atual + 1
            if (
                proximo >= len(trechos)
                or trechos[proximo].arquivo != trechos[atual].arquivo
                or not _parte_seguida(trechos[atual].secao, trechos[proximo].secao)
            ):
                break
            atual = proximo
    return figuras


def _parte_seguida(antes: str, depois: str) -> bool:
    base_antes, numero_antes = _parte(antes)
    base_depois, numero_depois = _parte(depois)
    if not base_antes or base_antes != base_depois:
        return False
    return numero_depois == numero_antes + 1


def _parte(secao: str) -> tuple[str, int]:
    encontrado = re.search(r" \(parte (\d+)\)$", secao)
    if not encontrado:
        return "", 0
    return secao[: encontrado.start()], int(encontrado.group(1))


def expandir(pergunta: str) -> str:
    presentes = {token.casefold() for token in _TOKEN.findall(pergunta)}
    extras: list[str] = []
    for token in list(presentes):
        for sinonimo in _GLOSSARIO.get(token, ()):
            chave = sinonimo.casefold()
            if chave in presentes:
                continue
            extras.append(sinonimo)
            presentes.add(chave)
    if not extras:
        return pergunta
    return f"{pergunta} {' '.join(extras)}"


def tem_base(acertos: list[Acerto]) -> bool:
    if not acertos:
        return False
    return max(acerto.coseno for acerto in acertos) >= 0.15 or max(acerto.bm25 for acerto in acertos) > 0


def _cosenos(matriz: np.ndarray, consulta: np.ndarray) -> np.ndarray:
    consulta = np.asarray(consulta, dtype=np.float32).reshape(-1)
    normas = np.linalg.norm(matriz, axis=1)
    norma_consulta = float(np.linalg.norm(consulta))
    if norma_consulta == 0:
        return np.zeros(len(matriz), dtype=np.float32)
    denom = normas * norma_consulta
    produto = matriz @ consulta
    with np.errstate(divide="ignore", invalid="ignore"):
        cosenos = np.divide(produto, denom, out=np.zeros_like(produto), where=denom > 0)
    return cosenos


def _bm25(trechos: list[Trecho], pergunta: str) -> np.ndarray:
    corpus = [_tokens(trecho.texto) or ["vazio"] for trecho in trechos]
    if all(tokens == ["vazio"] for tokens in corpus):
        return np.zeros(len(trechos), dtype=np.float32)
    modelo = BM25Okapi(corpus)
    return np.asarray(modelo.get_scores(_tokens(pergunta)), dtype=np.float32)


def _tokens(texto: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(texto) if len(token) >= 2 or any(c.isdigit() for c in token)]
