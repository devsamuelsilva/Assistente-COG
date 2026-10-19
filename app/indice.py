from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from app.arquivos import gravar_json, ler_json
from app.chunking import Trecho, fatiar, linha_documento, secao_base, titulo_proprio
from app.config import EMBED_MODEL
from app.extrair import anexar_figuras, carregar_acervo, figuras_de_conteudo, listar_arquivos
from app.ficha import gerar_ficha, trecho_da_ficha
from app.perguntas import preencher as preencher_perguntas
from app.visao import transcrever_figuras

VERSAO = 2
_LOTE = 16
_MAXIMO_GUARDADOS = 20_000
_INSTRUCAO_QWEN = "Instruct: Given a question from a power plant operator, retrieve the normative passage that answers it\nQuery:"
_PREFIXOS = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "google/embeddinggemma-300m": ("task: search result | query: ", "title: none | text: "),
    "Qwen/Qwen3-Embedding-0.6B": (_INSTRUCAO_QWEN, ""),
    "Qwen/Qwen3-Embedding-0.6B-Q": (_INSTRUCAO_QWEN, ""),
}


def prefixos(modelo: str) -> tuple[str, str]:
    return _PREFIXOS.get(modelo, ("", ""))


def embutir_pergunta(modelo: str, texto: str) -> np.ndarray:
    consulta, _ = prefixos(modelo)
    return np.asarray(next(iter(_modelo(modelo).embed([consulta + texto]))), dtype=np.float32)


@dataclass
class Indice:
    modelo: str
    fingerprint: str
    trechos: list[Trecho]
    vetores: np.ndarray
    vetores_perguntas: np.ndarray | None = None

    def vigentes(self) -> tuple[list[Trecho], np.ndarray]:
        if not self.trechos:
            return [], np.zeros((0, 0), dtype=np.float32)
        mascara = [trecho.vigente for trecho in self.trechos]
        if not any(mascara):
            return [], np.zeros((0, self.vetores.shape[1]), dtype=np.float32)
        escolhidos = [trecho for trecho, fica in zip(self.trechos, mascara) if fica]
        return escolhidos, self.vetores[np.array(mascara)]

    def perguntas_vigentes(self) -> np.ndarray | None:
        if self.vetores_perguntas is None or not self.trechos:
            return None
        mascara = np.array([trecho.vigente for trecho in self.trechos])
        return self.vetores_perguntas[mascara]


def caminho_indice(data_dir: Path) -> Path:
    return data_dir / "indice"


def assinatura_conteudo(indice: Indice) -> str:
    resumo = hashlib.sha256()
    resumo.update(indice.fingerprint.encode("utf-8"))
    for trecho in indice.trechos:
        resumo.update(trecho.texto.encode("utf-8"))
        resumo.update(b"\n")
    return resumo.hexdigest()


def fingerprint_acervo(acervo: Path) -> str:
    resumo = hashlib.sha256()
    for caminho in listar_arquivos(acervo):
        for arquivo in (caminho, caminho.parent / f"{caminho.name}.meta.json"):
            try:
                stat = arquivo.stat()
            except FileNotFoundError:
                continue
            resumo.update(arquivo.relative_to(acervo).as_posix().encode("utf-8"))
            resumo.update(f"|{stat.st_size}|{stat.st_mtime_ns}\n".encode("ascii"))
    return resumo.hexdigest()


def indexar(
    acervo: Path,
    data_dir: Path,
    modelo: str = EMBED_MODEL,
    embedder=None,
    transcritor=None,
    redator=None,
    perguntador=None,
    avisos: list[str] | None = None,
    progresso=None,
) -> Indice:
    """Sem embedder de teste, gera também a visão geral e as perguntas de busca pelo GPT."""
    documentos = carregar_acervo(acervo)
    if not documentos:
        raise FileNotFoundError(
            f"Nenhum PDF, DOCX, TXT ou MD em {acervo}. Coloque o normativo nessa pasta."
        )
    avisos = avisos if avisos is not None else []
    avisar = progresso or (lambda etapa, feitos=0, total=0: None)
    trechos: list[Trecho] = []
    for posicao, documento in enumerate(documentos):
        avisar("lendo os documentos", posicao, len(documentos))
        figuras = figuras_de_conteudo(documento.caminho)
        if figuras:
            documento = anexar_figuras(
                documento, transcrever_figuras(data_dir, figuras, gerador=transcritor, progresso=avisar)
            )
        do_documento = fatiar(documento)
        if do_documento and (redator is not None or embedder is None):
            avisar("visão geral", posicao, len(documentos))
            do_documento = _com_ficha(data_dir, documento, do_documento, redator, avisos)
        trechos.extend(do_documento)
    if not trechos:
        raise ValueError("Os arquivos do acervo não têm texto para indexar.")
    if perguntador is not None or embedder is None:
        preencher_perguntas(data_dir, trechos, perguntador, avisos, progresso=avisar)
    _com_aprendidas(data_dir, trechos)
    vetores = _embutir([trecho.texto for trecho in trechos], modelo, embedder, data_dir, avisar)
    indice = Indice(
        modelo=modelo if embedder is None else "teste",
        fingerprint=fingerprint_acervo(acervo),
        trechos=trechos,
        vetores=vetores,
        vetores_perguntas=_vetores_perguntas(trechos, vetores.shape[1], modelo, embedder, data_dir, avisar),
    )
    gravar(indice, data_dir)
    return indice


def caminho_aprendidas(data_dir: Path) -> Path:
    return data_dir / "aprendizado" / "perguntas.json"


def chave_secao(arquivo: str, secao: str) -> str:
    return f"{arquivo}\n{secao_base(secao)}"


def _com_aprendidas(data_dir: Path, trechos: list[Trecho]) -> None:
    """Perguntas que o treino não achava pela busca passam a fazer parte das perguntas de busca da seção."""
    guardadas = ler_json(caminho_aprendidas(data_dir), {})
    if not isinstance(guardadas, dict) or not guardadas:
        return
    for trecho in trechos:
        extras = guardadas.get(chave_secao(trecho.arquivo, trecho.secao))
        if isinstance(extras, list):
            trecho.perguntas = [*trecho.perguntas, *(str(extra) for extra in extras if extra not in trecho.perguntas)]


def _com_ficha(data_dir: Path, documento, trechos: list[Trecho], redator, avisos: list[str]) -> list[Trecho]:
    try:
        ficha = gerar_ficha(data_dir, trechos, redator)
    except Exception as erro:
        avisos.append(
            f"{documento.arquivo}: visão geral não gerada ({str(erro) or erro.__class__.__name__}). "
            "A consulta funciona sem ela; rode a indexação de novo para completar."
        )
        return trechos
    if not ficha:
        return trechos
    titulo = " ".join(str(ficha.get("titulo") or "").split())
    if titulo and not titulo_proprio(documento):
        documento = replace(documento, titulo=titulo)
        trechos = fatiar(documento)
    return [*trechos, trecho_da_ficha(trechos[0], ficha, linha_documento(documento))]


def _vetores_perguntas(
    trechos: list[Trecho], dimensao: int, modelo: str, embedder, data_dir: Path | None = None, progresso=None
) -> np.ndarray | None:
    posicoes = [posicao for posicao, trecho in enumerate(trechos) if trecho.perguntas]
    if not posicoes:
        return None
    textos = ["\n".join(trechos[posicao].perguntas) for posicao in posicoes]
    if embedder is not None:
        calculados = [np.asarray(vetor, dtype=np.float32) for vetor in embedder(textos)]
    else:
        consulta, _ = prefixos(modelo)
        calculados = _com_cache(data_dir, modelo, [consulta + texto for texto in textos], progresso=progresso)
    matriz = np.zeros((len(trechos), dimensao), dtype=np.float32)
    for posicao, vetor in zip(posicoes, calculados):
        matriz[posicao] = vetor
    return matriz


def gravar(indice: Indice, data_dir: Path) -> None:
    # Os vetores vão para um arquivo de nome novo e só depois o trechos.json passa a apontar para ele: quem lê
    # no meio da troca vê o par antigo inteiro ou o novo inteiro. O par anterior fica para uma leitura em curso.
    destino = caminho_indice(data_dir)
    destino.mkdir(parents=True, exist_ok=True)
    marca = time.time_ns()
    while (destino / f"vetores-{marca}.npy").exists() or (destino / f"perguntas-{marca}.npy").exists():
        marca += 1
    nome_vetores = _salvar_matriz(destino, f"vetores-{marca}.npy", indice.vetores)
    nome_perguntas = (
        _salvar_matriz(destino, f"perguntas-{marca}.npy", indice.vetores_perguntas)
        if indice.vetores_perguntas is not None
        else None
    )
    anteriores = _nomes_matrizes(destino / "trechos.json")
    payload = {
        "versao": VERSAO,
        "modelo": indice.modelo,
        "fingerprint": indice.fingerprint,
        "vetores": nome_vetores,
        "vetores_perguntas": nome_perguntas,
        "trechos": [asdict(trecho) for trecho in indice.trechos],
    }
    gravar_json(destino / "trechos.json", payload)
    manter = {nome_vetores, nome_perguntas, *anteriores}
    for velho in [*destino.glob("vetores*.npy"), *destino.glob("perguntas*.npy")]:
        if velho.name not in manter:
            velho.unlink(missing_ok=True)


def _salvar_matriz(destino: Path, nome: str, matriz: np.ndarray) -> str:
    temporario = destino / f".{nome}.tmp"
    with temporario.open("wb") as saida:
        np.save(saida, matriz)
    os.replace(temporario, destino / nome)
    return nome


def _nomes_matrizes(arquivo: Path) -> set[str]:
    payload = ler_json(arquivo)
    if not isinstance(payload, dict):
        return set()
    return {str(payload.get("vetores") or "vetores.npy"), str(payload.get("vetores_perguntas") or "")} - {""}


_CACHE_INDICE: dict[tuple, Indice] = {}
_TRAVA_INDICE = threading.Lock()


def carregar(data_dir: Path, modelo_esperado: str = EMBED_MODEL) -> Indice:
    destino = caminho_indice(data_dir)
    arquivo = destino / "trechos.json"
    try:
        stat = arquivo.stat()
    except FileNotFoundError:
        raise FileNotFoundError("Índice ainda não existe. Rode: python -m app indexar") from None
    chave = (str(arquivo.resolve()), stat.st_size, stat.st_mtime_ns, modelo_esperado)
    with _TRAVA_INDICE:
        if chave in _CACHE_INDICE:
            return _CACHE_INDICE[chave]
    indice = _ler_indice(destino, modelo_esperado)
    with _TRAVA_INDICE:
        for velha in [velha for velha in _CACHE_INDICE if velha[0] == chave[0]]:
            del _CACHE_INDICE[velha]
        _CACHE_INDICE[chave] = indice
    return indice


def _ler_indice(destino: Path, modelo_esperado: str) -> Indice:
    payload = ler_json(destino / "trechos.json")
    if not isinstance(payload, dict):
        raise ValueError("Índice ilegível. Rode: python -m app indexar")
    if payload.get("versao") != VERSAO:
        raise ValueError("Índice de uma versão antiga. Rode: python -m app indexar")
    if payload.get("modelo") not in {modelo_esperado, "teste"}:
        raise ValueError(
            "O índice foi gerado com outro modelo de embeddings. Rode: python -m app indexar"
        )
    vetores_path = destino / str(payload.get("vetores") or "vetores.npy")
    if not vetores_path.is_file():
        raise FileNotFoundError("Índice ainda não existe. Rode: python -m app indexar")
    trechos = [Trecho(**item) for item in payload["trechos"]]
    vetores = np.load(vetores_path)
    if len(trechos) != len(vetores):
        raise ValueError("Índice inconsistente. Rode: python -m app indexar")
    vetores_perguntas = None
    if payload.get("vetores_perguntas"):
        caminho_perguntas = destino / str(payload["vetores_perguntas"])
        if caminho_perguntas.is_file():
            vetores_perguntas = np.load(caminho_perguntas)
            if vetores_perguntas.shape != vetores.shape:
                vetores_perguntas = None
    return Indice(
        modelo=payload["modelo"],
        fingerprint=payload["fingerprint"],
        trechos=trechos,
        vetores=vetores,
        vetores_perguntas=vetores_perguntas,
    )


def _embutir(textos: list[str], modelo: str, embedder, data_dir: Path | None = None, progresso=None) -> np.ndarray:
    if embedder is not None:
        vetores = [np.asarray(vetor, dtype=np.float32) for vetor in embedder(textos)]
    else:
        _, prefixo = prefixos(modelo)
        vetores = _com_cache(data_dir, modelo, [prefixo + texto for texto in textos], progresso=progresso)
    if len(vetores) != len(textos):
        raise RuntimeError("O modelo local devolveu uma quantidade inesperada de vetores.")
    return np.vstack(vetores)


def _com_cache(data_dir: Path | None, modelo: str, textos: list[str], calcular=None, progresso=None) -> list[np.ndarray]:
    """Vetores guardados por hash do texto: reindexar só calcula os textos que mudaram."""
    calcular = calcular or (lambda faltantes: _em_lotes(modelo, faltantes, progresso))
    if data_dir is None:
        return calcular(textos)
    arquivo = data_dir / "vetores" / f"{hashlib.sha256(modelo.encode('utf-8')).hexdigest()[:16]}.npz"
    guardados = _ler_vetores(arquivo)
    chaves = [hashlib.sha256(texto.encode("utf-8")).hexdigest() for texto in textos]
    faltantes = dict(zip(chaves, textos))
    for chave in guardados.keys() & faltantes.keys():
        del faltantes[chave]
    if faltantes:
        novos = calcular(list(faltantes.values()))
        guardados.update(zip(faltantes.keys(), (np.asarray(vetor, dtype=np.float32) for vetor in novos)))
        usados = set(chaves)
        if len(guardados) > max(_MAXIMO_GUARDADOS, 4 * len(usados)):
            guardados = {chave: vetor for chave, vetor in guardados.items() if chave in usados}
        _gravar_vetores(arquivo, guardados)
    return [guardados[chave] for chave in chaves]


def _ler_vetores(arquivo: Path) -> dict[str, np.ndarray]:
    try:
        with np.load(arquivo) as dados:
            return dict(zip(dados["chaves"].tolist(), dados["vetores"]))
    except (OSError, KeyError, ValueError):
        return {}


def _gravar_vetores(arquivo: Path, guardados: dict[str, np.ndarray]) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_name(f".{arquivo.name}.{os.getpid()}.tmp")
    with temporario.open("wb") as saida:
        np.savez(saida, chaves=np.array(list(guardados.keys())), vetores=np.vstack(list(guardados.values())))
    os.replace(temporario, arquivo)


def _em_lotes(modelo: str, textos: list[str], progresso=None) -> list[np.ndarray]:
    # Cada lote é completado até o tamanho do texto mais longo dele: sem ordenar, um texto longo deixa o lote
    # inteiro tão caro quanto ele.
    ordem = sorted(range(len(textos)), key=lambda posicao: len(textos[posicao]))
    calculados = _modelo(modelo).embed([textos[posicao] for posicao in ordem], batch_size=_LOTE)
    vetores: list[np.ndarray] = [np.empty(0, dtype=np.float32)] * len(textos)
    for feitos, (posicao, vetor) in enumerate(zip(ordem, calculados)):
        vetores[posicao] = np.asarray(vetor, dtype=np.float32)
        if progresso is not None and feitos % _LOTE == 0:
            progresso("vetores", feitos, len(textos))
    return vetores


_CACHE_MODELO: dict[str, object] = {}


def _modelo(nome: str):
    if nome not in _CACHE_MODELO:
        from fastembed import TextEmbedding

        cache = os.environ.get("FASTEMBED_CACHE_PATH")
        _CACHE_MODELO[nome] = TextEmbedding(model_name=nome, cache_dir=cache)
    return _CACHE_MODELO[nome]


def aquecer_modelo(nome: str = EMBED_MODEL) -> None:
    _modelo(nome)


