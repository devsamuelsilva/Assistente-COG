"""Modelo de confiança: regressão logística treinada com as rodadas julgadas.

Estima a chance de uma resposta coberta estar correta a partir dos sinais da busca e do GPT. Só liga quando há
exemplos suficientes e a validação cruzada mostra que ele separa acertos de erros.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import numpy as np

from app.arquivos import gravar_json, ler_json

CAMPOS = (
    "coseno_max",
    "bm25_max",
    "termos",
    "cit_coseno",
    "cit_posicao",
    "citacoes",
    "documentos",
    "doc_citado",
    "pergunta_palavras",
    "resposta_caracteres",
    "logprob",
)
MINIMO_EXEMPLOS = 200
AUC_MINIMA = 0.70
PRECISAO_ALTA = 0.95
_DOBRAS = 5
_REGULARIZACAO = 1.0
_ITERACOES = 30
_MINIMO_NO_CORTE = 20
_TRAVA = threading.Lock()
_CACHE: dict[str, tuple[int, dict]] = {}


def caminho(data_dir: Path) -> Path:
    return data_dir / "modelo" / "confianca.json"


def exemplos(data_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """Respostas cobertas das rodadas com sinais; rótulo 1 para acerto. Repetições da mesma resposta contam uma vez."""
    vistos: set[str] = set()
    linhas: list[list[float]] = []
    rotulos: list[float] = []
    for arquivo in sorted((data_dir / "avaliacao" / "rodadas").glob("*.json")):
        rodada = ler_json(arquivo)
        if not isinstance(rodada, dict):
            continue
        for item in rodada.get("itens") or []:
            sinais = item.get("sinais")
            if not item.get("coberto") or not isinstance(sinais, dict) or item.get("status") == "falha":
                continue
            chave = hashlib.sha256(f"{item.get('pergunta')}\n{item.get('resposta')}".encode("utf-8")).hexdigest()
            if chave in vistos:
                continue
            vistos.add(chave)
            linhas.append([_numero(sinais.get(campo)) for campo in CAMPOS])
            rotulos.append(1.0 if item.get("status") == "acerto" else 0.0)
    if not linhas:
        return np.zeros((0, len(CAMPOS))), np.zeros(0)
    return np.array(linhas, dtype=np.float64), np.array(rotulos, dtype=np.float64)


def treinar(data_dir: Path) -> dict:
    x, y = exemplos(data_dir)
    estado = {"ligado": False, "exemplos": int(len(y)), "acertos": int(y.sum()) if len(y) else 0, "campos": list(CAMPOS)}
    if len(y) < MINIMO_EXEMPLOS:
        estado["motivo"] = f"Precisa de {MINIMO_EXEMPLOS} respostas julgadas com sinais; há {len(y)}."
    elif y.min() == y.max():
        estado["motivo"] = "As respostas julgadas são todas do mesmo tipo; não há o que separar."
    else:
        previstas = _validacao_cruzada(x, y)
        auc = auc_roc(y, previstas)
        alto, baixo = _cortes(y, previstas)
        modelo = _ajustar(x, y)
        estado.update(modelo, auc=round(auc, 4), corte_alto=alto, corte_baixo=baixo)
        if auc >= AUC_MINIMA:
            estado["ligado"] = True
        else:
            estado["motivo"] = f"A validação deu AUC {auc:.2f}; o mínimo para ligar é {AUC_MINIMA:.2f}."
    gravar_json(caminho(data_dir), estado)
    return estado


def estado(data_dir: Path) -> dict:
    arquivo = caminho(data_dir)
    try:
        marca = arquivo.stat().st_mtime_ns
    except FileNotFoundError:
        return {"ligado": False, "exemplos": 0, "motivo": "Ainda não treinado."}
    with _TRAVA:
        guardado = _CACHE.get(str(arquivo))
        if guardado and guardado[0] == marca:
            return guardado[1]
    dados = ler_json(arquivo, {}) or {}
    with _TRAVA:
        _CACHE[str(arquivo)] = (marca, dados)
    return dados


def avaliar(data_dir: Path, sinais: dict) -> dict:
    modelo = estado(data_dir)
    if not modelo.get("ligado") or not sinais:
        return {}
    linha = np.array([[_numero(sinais.get(campo)) for campo in CAMPOS]], dtype=np.float64)
    probabilidade = float(_prever(modelo, linha)[0])
    if modelo.get("corte_alto") is not None and probabilidade >= modelo["corte_alto"]:
        nivel = "alta"
    elif probabilidade < (modelo.get("corte_baixo") or 0.0):
        nivel = "baixa"
    else:
        nivel = "media"
    return {"nivel": nivel, "probabilidade": round(probabilidade, 3)}


def auc_roc(rotulos: np.ndarray, notas: np.ndarray) -> float:
    positivos = rotulos == 1
    quantos_pos, quantos_neg = int(positivos.sum()), int((~positivos).sum())
    if not quantos_pos or not quantos_neg:
        return 0.5
    ordem = np.argsort(notas, kind="mergesort")
    postos = np.empty(len(notas))
    notas_ordenadas = notas[ordem]
    inicio = 0
    while inicio < len(notas):
        fim = inicio
        while fim + 1 < len(notas) and notas_ordenadas[fim + 1] == notas_ordenadas[inicio]:
            fim += 1
        postos[ordem[inicio : fim + 1]] = (inicio + fim) / 2 + 1
        inicio = fim + 1
    soma = postos[positivos].sum() - quantos_pos * (quantos_pos + 1) / 2
    return float(soma / (quantos_pos * quantos_neg))


def _ajustar(x: np.ndarray, y: np.ndarray) -> dict:
    validos = ~np.isnan(x)
    medias = np.where(validos.any(axis=0), np.nansum(x, axis=0) / np.maximum(validos.sum(axis=0), 1), 0.0)
    preenchido = np.where(validos, x, medias)
    desvios = preenchido.std(axis=0)
    desvios = np.where(desvios == 0, 1.0, desvios)
    matriz = np.hstack([np.ones((len(y), 1)), (preenchido - medias) / desvios])
    pesos = np.zeros(matriz.shape[1])
    penalidade = np.full(matriz.shape[1], _REGULARIZACAO)
    penalidade[0] = 0.0
    for _ in range(_ITERACOES):
        probabilidades = _sigmoide(matriz @ pesos)
        gradiente = matriz.T @ (probabilidades - y) + penalidade * pesos
        hessiana = (matriz * (probabilidades * (1 - probabilidades))[:, None]).T @ matriz + np.diag(penalidade + 1e-9)
        passo = np.linalg.solve(hessiana, gradiente)
        pesos -= passo
        if np.abs(passo).max() < 1e-6:
            break
    return {"medias": medias.tolist(), "desvios": desvios.tolist(), "pesos": pesos.tolist()}


def _prever(modelo: dict, x: np.ndarray) -> np.ndarray:
    medias = np.array(modelo["medias"])
    preenchido = np.where(np.isnan(x), medias, x)
    matriz = np.hstack([np.ones((len(x), 1)), (preenchido - medias) / np.array(modelo["desvios"])])
    return _sigmoide(matriz @ np.array(modelo["pesos"]))


def _validacao_cruzada(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    dobras = np.random.default_rng(0).permutation(len(y)) % _DOBRAS
    previstas = np.zeros(len(y))
    for dobra in range(_DOBRAS):
        teste = dobras == dobra
        treino = ~teste
        if y[treino].min() == y[treino].max():
            previstas[teste] = y[treino].mean()
            continue
        previstas[teste] = _prever(_ajustar(x[treino], y[treino]), x[teste])
    return previstas


def _cortes(y: np.ndarray, previstas: np.ndarray) -> tuple[float | None, float]:
    alto = None
    for corte in np.unique(previstas):
        acima = previstas >= corte
        if acima.sum() < _MINIMO_NO_CORTE:
            break
        if y[acima].mean() >= PRECISAO_ALTA:
            alto = round(float(corte), 4)
            break
    baixo = 0.0
    for corte in np.unique(previstas)[::-1]:
        abaixo = previstas < corte
        if abaixo.sum() >= _MINIMO_NO_CORTE and y[abaixo].mean() <= 0.5:
            baixo = round(float(corte), 4)
            break
    return alto, baixo


def _sigmoide(valores: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(valores, -30, 30)))


def _numero(valor) -> float:
    if isinstance(valor, bool):
        return 1.0 if valor else 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return float("nan")
