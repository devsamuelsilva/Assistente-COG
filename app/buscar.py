from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from app.chunking import PARTE, Trecho, codigo_limpo, secao_base
from app.indice import Indice

_TOKEN = re.compile(r"\w+", re.UNICODE)
_PARTE_CODIGO = re.compile(r"[^\W\d_]+|\d+")
_SEM_REVISAO = re.compile(r"(?i)\s*rev\.?\s*\d+.*$")
_RRF_K = 60
_GLOSSARIO = {
    "wtg": ("aerogerador", "aerogeradores"),
    "su": ("subestação", "unitária"),
    "seccionamento": ("desligamento", "isolação", "seccionadora"),
    "urgências": ("urgência",),
    "urgencias": ("urgência",),
    "finalidade": ("objetivo", "propósito"),
    "objetivo": ("finalidade", "propósito"),
    "propósito": ("objetivo", "finalidade"),
    "proposito": ("objetivo", "finalidade"),
}


_VAZIAS = frozenset(
    """a o as os um uma uns umas de da do das dos em na no nas nos ao aos à às e é ou que qual quais quem
    como onde quando se por para pra pelo pela pelos pelas com sem mais menos muito ser são foi era está estão
    há tem têm ter isso isto este esta esse essa aquele aquela eu você me te lhe meu minha seu sua nosso nossa
    sobre entre até também já não sim oi olá obrigado obrigada bom boa dia tudo bem faz fazer faço""".split()
)
_BASE_COSENO = 0.28
_BASE_BM25 = 8.0
_MODELOS_BM25: dict[tuple[int, ...], tuple[list[Trecho], BM25Okapi | None]] = {}
_TRAVA_BM25 = threading.Lock()

_LIMITE_SECAO = 40_000
_LIMITE_DOCUMENTO = 200_000


@dataclass
class Acerto:
    trecho: Trecho
    coseno: float
    bm25: float
    rrf: float
    rerank: float | None = None
    bm25_base: float = 0.0
    termos: int = 0


def buscar(
    indice: Indice,
    pergunta: str,
    consulta: np.ndarray,
    top_k: int = 6,
    reordenador=None,
    candidatos: int = 30,
    modo: str = "trechos",
    arquivos: set[str] | None = None,
) -> list[Acerto]:
    trechos, vetores = indice.vigentes()
    if not trechos:
        return []
    pergunta = expandir(pergunta)
    cosenos = _cosenos(vetores, consulta)
    perguntas = indice.perguntas_vigentes()
    if perguntas is not None and perguntas.shape[1] == np.asarray(consulta).reshape(-1).shape[0]:
        cosenos = np.maximum(cosenos, _cosenos(perguntas, consulta))
    bm25 = _bm25(trechos, pergunta)
    bm25_base = _bm25(trechos, pergunta, sem_vazias=True)
    permitidos = np.array(
        [not trecho.secao.startswith("Sumário") and (not arquivos or trecho.arquivo in arquivos) for trecho in trechos]
    )
    largura = max(top_k, candidatos) if reordenador else top_k
    ordem_vetor = [posicao for posicao in np.argsort(-cosenos) if permitidos[posicao]][:largura]
    ordem_texto = [posicao for posicao in np.argsort(-bm25) if permitidos[posicao]][:largura]
    pontuacao: dict[int, float] = {}
    for posto, indice_trecho in enumerate(ordem_vetor):
        pontuacao[int(indice_trecho)] = pontuacao.get(int(indice_trecho), 0.0) + 1.0 / (_RRF_K + posto + 1)
    for posto, indice_trecho in enumerate(ordem_texto):
        if bm25[indice_trecho] <= 0:
            continue
        pontuacao[int(indice_trecho)] = pontuacao.get(int(indice_trecho), 0.0) + 1.0 / (_RRF_K + posto + 1)
    ordenados = sorted(pontuacao, key=pontuacao.get, reverse=True)[:largura]
    notas: dict[int, float] = {}
    if reordenador and ordenados:
        valores = reordenador(pergunta, [trechos[indice_trecho].texto for indice_trecho in ordenados])
        notas = dict(zip(ordenados, valores))
        ordenados = sorted(ordenados, key=lambda indice_trecho: notas[indice_trecho], reverse=True)
    ordenados = ordenados[:top_k]
    if modo == "secao":
        ordenados = _secoes_inteiras(trechos, ordenados, _LIMITE_SECAO)
    elif modo == "documento":
        ordenados = _documento_inteiro(trechos, ordenados, _LIMITE_DOCUMENTO)
    else:
        ordenados = _com_continuacao(trechos, ordenados, limite=max(top_k, 10))
    return [
        Acerto(
            trecho=trechos[indice_trecho],
            coseno=float(cosenos[indice_trecho]),
            bm25=float(bm25[indice_trecho]),
            rrf=float(pontuacao.get(indice_trecho, 0.0)),
            rerank=notas.get(indice_trecho),
            bm25_base=float(bm25_base[indice_trecho]),
            termos=_termos_presentes(trechos, indice_trecho, pergunta),
        )
        for indice_trecho in ordenados
    ]


def _termos_presentes(trechos: list[Trecho], indice_trecho: int, pergunta: str) -> int:
    modelo = _modelo_bm25(trechos)
    if modelo is None:
        return 0
    presentes = modelo.doc_freqs[indice_trecho]
    return sum(1 for termo in set(_tokens(pergunta)) - _VAZIAS if termo in presentes)


def _secoes_inteiras(trechos: list[Trecho], escolhidos: list[int], limite: int) -> list[int]:
    resultado: list[int] = []
    vistos: set[int] = set()
    tamanho = 0
    for indice in escolhidos:
        if indice in vistos:
            continue
        chave = (trechos[indice].arquivo, secao_base(trechos[indice].secao))
        secao = [
            outro
            for outro, trecho in enumerate(trechos)
            if outro not in vistos and (trecho.arquivo, secao_base(trecho.secao)) == chave
        ]
        custo = sum(len(trechos[outro].texto) for outro in secao)
        if resultado and tamanho + custo > limite:
            if indice not in vistos:
                resultado.append(indice)
                vistos.add(indice)
                tamanho += len(trechos[indice].texto)
            continue
        resultado.extend(secao)
        vistos.update(secao)
        tamanho += custo
    return resultado


def _documento_inteiro(trechos: list[Trecho], escolhidos: list[int], limite: int) -> list[int]:
    if not escolhidos:
        return []
    arquivo = trechos[escolhidos[0]].arquivo
    resultado: list[int] = []
    tamanho = 0
    for indice, trecho in enumerate(trechos):
        if trecho.arquivo != arquivo:
            continue
        if tamanho + len(trecho.texto) > limite:
            break
        resultado.append(indice)
        tamanho += len(trecho.texto)
    extras = [indice for indice in escolhidos if trechos[indice].arquivo != arquivo]
    return resultado + extras


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
    figuras = [indice for indice in _figuras_relacionadas(trechos, resultado) if indice not in vistos]
    return resultado + figuras[: max(limite - len(resultado), 2)]


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
    encontrado = PARTE.search(secao)
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


def escopo(pergunta: str, trechos: list[Trecho]) -> tuple[set[str], str]:
    """Arquivos cujo código aparece na pergunta e a pergunta sem esse código.

    Vale o código inteiro ("ITOM AGD 01") ou parte dele com o número no fim ("AGD 01", "AGD01", "ITOM 01").
    O número tem de vir escrito igual ("01" e não "1"). Parte que serve a mais de um documento traz todos.
    """
    codigos: dict[str, list[str]] = {}
    for trecho in trechos:
        if trecho.arquivo not in codigos:
            codigos[trecho.arquivo] = [parte.casefold() for parte in _PARTE_CODIGO.findall(_SEM_REVISAO.sub("", codigo_limpo(trecho.codigo)))]
    partes = list(_PARTE_CODIGO.finditer(pergunta))
    palavras = [parte.group(0).casefold() for parte in partes]
    arquivos: set[str] = set()
    usadas: set[int] = set()
    for arquivo, codigo in codigos.items():
        if len(codigo) < 2 or not codigo[-1].isdigit():
            continue
        for inicio in range(len(palavras)):
            for fim in range(min(len(palavras), inicio + len(codigo)), inicio + 1, -1):
                janela = palavras[inicio:fim]
                if janela[-1] == codigo[-1] and _subsequencia(janela, codigo):
                    arquivos.add(arquivo)
                    usadas.update(range(inicio, fim))
                    break
    if not arquivos:
        return set(), pergunta
    resto = pergunta
    for posicao in sorted(usadas, reverse=True):
        resto = resto[: partes[posicao].start()] + resto[partes[posicao].end() :]
    resto = " ".join(palavra for palavra in resto.split() if any(letra.isalnum() for letra in palavra))
    if not set(_tokens(resto)) - _VAZIAS:
        resto = pergunta
    return arquivos, resto


def _subsequencia(janela: list[str], codigo: list[str]) -> bool:
    restante = iter(codigo)
    return all(any(parte == candidata for candidata in restante) for parte in janela)


def tem_base(acertos: list[Acerto]) -> bool:
    if not acertos:
        return False
    if not any(acerto.termos for acerto in acertos):
        return False
    return max(acerto.coseno for acerto in acertos) >= _BASE_COSENO or max(acerto.bm25_base for acerto in acertos) >= _BASE_BM25


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


def _bm25(trechos: list[Trecho], pergunta: str, sem_vazias: bool = False) -> np.ndarray:
    modelo = _modelo_bm25(trechos)
    termos = _tokens(pergunta)
    if sem_vazias:
        termos = [token for token in termos if token not in _VAZIAS]
    if modelo is None or not termos:
        return np.zeros(len(trechos), dtype=np.float32)
    return np.asarray(modelo.get_scores(termos), dtype=np.float32)


def _modelo_bm25(trechos: list[Trecho]) -> BM25Okapi | None:
    chave = tuple(id(trecho) for trecho in trechos)
    with _TRAVA_BM25:
        guardado = _MODELOS_BM25.get(chave)
    if guardado is not None:
        return guardado[1]
    corpus = [_tokens(" ".join([trecho.texto, *trecho.perguntas])) or ["vazio"] for trecho in trechos]
    modelo = None if all(tokens == ["vazio"] for tokens in corpus) else BM25Okapi(corpus)
    with _TRAVA_BM25:
        if len(_MODELOS_BM25) >= 4:
            _MODELOS_BM25.pop(next(iter(_MODELOS_BM25)))
        _MODELOS_BM25[chave] = (list(trechos), modelo)
    return modelo


def _tokens(texto: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(texto) if len(token) >= 2 or any(c.isdigit() for c in token)]
