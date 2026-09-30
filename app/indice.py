from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from app.chunking import Trecho, fatiar
from app.config import EMBED_MODEL
from app.extrair import anexar_figuras, carregar_acervo, figuras_de_conteudo
from app.visao import transcrever_figuras

VERSAO = 1


@dataclass
class Indice:
    modelo: str
    fingerprint: str
    trechos: list[Trecho]
    vetores: np.ndarray

    def vigentes(self) -> tuple[list[Trecho], np.ndarray]:
        if not self.trechos:
            return [], np.zeros((0, 0), dtype=np.float32)
        mascara = [trecho.vigente for trecho in self.trechos]
        if not any(mascara):
            return [], np.zeros((0, self.vetores.shape[1]), dtype=np.float32)
        escolhidos = [trecho for trecho, fica in zip(self.trechos, mascara) if fica]
        return escolhidos, self.vetores[np.array(mascara)]


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
    documentos = carregar_acervo(acervo)
    if not documentos:
        return resumo.hexdigest()
    for documento in documentos:
        stat = documento.caminho.stat()
        resumo.update(documento.arquivo.encode("utf-8"))
        resumo.update(str(stat.st_size).encode("ascii"))
        resumo.update(str(stat.st_mtime_ns).encode("ascii"))
    return resumo.hexdigest()


def indexar(acervo: Path, data_dir: Path, modelo: str = EMBED_MODEL, embedder=None, transcritor=None) -> Indice:
    documentos = carregar_acervo(acervo)
    if not documentos:
        raise FileNotFoundError(
            f"Nenhum PDF, DOCX, TXT ou MD em {acervo}. Coloque o normativo nessa pasta."
        )
    trechos: list[Trecho] = []
    for documento in documentos:
        figuras = figuras_de_conteudo(documento.caminho)
        if figuras:
            documento = anexar_figuras(documento, transcrever_figuras(data_dir, figuras, gerador=transcritor))
        trechos.extend(fatiar(documento))
    if not trechos:
        raise ValueError("Os arquivos do acervo não têm texto para indexar.")
    vetores = _embutir([trecho.texto for trecho in trechos], modelo, embedder)
    indice = Indice(
        modelo=modelo if embedder is None else "teste",
        fingerprint=fingerprint_acervo(acervo),
        trechos=trechos,
        vetores=vetores,
    )
    gravar(indice, data_dir)
    return indice


def gravar(indice: Indice, data_dir: Path) -> None:
    destino = caminho_indice(data_dir)
    destino.mkdir(parents=True, exist_ok=True)
    payload = {
        "versao": VERSAO,
        "modelo": indice.modelo,
        "fingerprint": indice.fingerprint,
        "trechos": [asdict(trecho) for trecho in indice.trechos],
    }
    _substituir_json(destino / "trechos.json", payload)
    temporario = destino / "vetores.tmp.npy"
    np.save(temporario, indice.vetores)
    os.replace(temporario, destino / "vetores.npy")


def carregar(data_dir: Path, modelo_esperado: str = EMBED_MODEL) -> Indice:
    destino = caminho_indice(data_dir)
    arquivo = destino / "trechos.json"
    vetores_path = destino / "vetores.npy"
    if not arquivo.exists() or not vetores_path.exists():
        raise FileNotFoundError("Índice ainda não existe. Rode: python -m app indexar")
    payload = json.loads(arquivo.read_text(encoding="utf-8"))
    if payload.get("versao") != VERSAO:
        raise ValueError("Índice de uma versão antiga. Rode: python -m app indexar")
    if payload.get("modelo") not in {modelo_esperado, "teste"}:
        raise ValueError(
            "O índice foi gerado com outro modelo de embeddings. Rode: python -m app indexar"
        )
    trechos = [Trecho(**item) for item in payload["trechos"]]
    vetores = np.load(vetores_path)
    if len(trechos) != len(vetores):
        raise ValueError("Índice inconsistente. Rode: python -m app indexar")
    return Indice(
        modelo=payload["modelo"],
        fingerprint=payload["fingerprint"],
        trechos=trechos,
        vetores=vetores,
    )


def _embutir(textos: list[str], modelo: str, embedder) -> np.ndarray:
    if embedder is not None:
        vetores = [np.asarray(vetor, dtype=np.float32) for vetor in embedder(textos)]
    else:
        vetores = list(_modelo(modelo).embed(textos))
        vetores = [np.asarray(vetor, dtype=np.float32) for vetor in vetores]
    if len(vetores) != len(textos):
        raise RuntimeError("O modelo local devolveu uma quantidade inesperada de vetores.")
    return np.vstack(vetores)


_CACHE_MODELO: dict[str, object] = {}


def _modelo(nome: str):
    if nome not in _CACHE_MODELO:
        from fastembed import TextEmbedding

        cache = os.environ.get("FASTEMBED_CACHE_PATH")
        _CACHE_MODELO[nome] = TextEmbedding(model_name=nome, cache_dir=cache)
    return _CACHE_MODELO[nome]


def aquecer_modelo(nome: str = EMBED_MODEL) -> None:
    _modelo(nome)


def _substituir_json(caminho: Path, payload: dict) -> None:
    temporario = caminho.with_suffix(".json.tmp")
    temporario.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, caminho)
