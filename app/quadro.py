from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.chunking import Trecho, secao_base

_CELULA = re.compile(r"^Linha:\s*(.+?)\s*\|\s*Coluna:\s*(.+?)\s*\|\s*Valor:\s*(.+?)\s*$")
_PALAVRA = re.compile(r"\w+", re.UNICODE)
_VAZIAS = {"para", "das", "dos", "que", "uma", "por", "nos", "nas", "pelo", "pela"}
_RADICAL = 5
_MINIMO = 0.5
_LIMITE = 24


@dataclass(frozen=True)
class Celula:
    linha: str
    coluna: str
    valor: str


@dataclass
class Quadro:
    arquivo: str
    secao: str
    pagina: str
    celulas: list[Celula] = field(default_factory=list)


def quadros(trechos: list[Trecho]) -> list[Quadro]:
    encontrados: dict[tuple[str, str], Quadro] = {}
    vistas: dict[tuple[str, str], set[Celula]] = {}
    for trecho in trechos:
        chave = (trecho.arquivo, secao_base(trecho.secao))
        for linha in trecho.texto.splitlines():
            casou = _CELULA.match(linha.strip())
            if not casou:
                continue
            celula = Celula(*casou.groups())
            quadro = encontrados.setdefault(chave, Quadro(trecho.arquivo, chave[1], trecho.pagina))
            if celula in vistas.setdefault(chave, set()):
                continue
            vistas[chave].add(celula)
            quadro.celulas.append(celula)
    return list(encontrados.values())


def consultar(quadro: Quadro, pergunta: str) -> list[Celula]:
    linhas = _escolher([celula.linha for celula in quadro.celulas], pergunta)
    colunas = _escolher([celula.coluna for celula in quadro.celulas], pergunta)
    if not linhas and not colunas:
        return []
    escolhidas = [
        celula
        for celula in quadro.celulas
        if (not linhas or celula.linha in linhas) and (not colunas or celula.coluna in colunas)
    ]
    return escolhidas if len(escolhidas) <= _LIMITE else []


def consulta_exata(trechos_do_indice: list[Trecho], trechos_do_contexto: list[Trecho], pergunta: str) -> str:
    no_contexto = {(trecho.arquivo, secao_base(trecho.secao)) for trecho in trechos_do_contexto}
    blocos: list[str] = []
    for quadro in quadros(trechos_do_indice):
        if (quadro.arquivo, quadro.secao) not in no_contexto:
            continue
        celulas = consultar(quadro, pergunta)
        if not celulas:
            continue
        linhas = [f"Linha: {c.linha} | Coluna: {c.coluna} | Valor: {c.valor}" for c in celulas]
        blocos.append(f"arquivo={quadro.arquivo} secao={quadro.secao}\n" + "\n".join(linhas))
    return "\n\n".join(blocos)


def _escolher(rotulos: list[str], pergunta: str) -> set[str]:
    termos = {rotulo: _radicais(rotulo.split("(")[0]) for rotulo in dict.fromkeys(rotulos)}
    if len(termos) < 2:
        return set()
    comuns = set.intersection(*termos.values())
    presentes = _radicais(pergunta)
    notas: dict[str, float] = {}
    for rotulo, radicais in termos.items():
        distintos = radicais - comuns
        if distintos:
            notas[rotulo] = len(distintos & presentes) / len(distintos)
    melhor = max(notas.values(), default=0.0)
    if melhor < _MINIMO:
        return set()
    return {rotulo for rotulo, nota in notas.items() if nota == melhor}


def _radicais(texto: str) -> set[str]:
    sem_acento = unicodedata.normalize("NFKD", texto.casefold())
    sem_acento = "".join(letra for letra in sem_acento if not unicodedata.combining(letra))
    return {
        palavra[:_RADICAL]
        for palavra in _PALAVRA.findall(sem_acento)
        if len(palavra) >= 3 and palavra not in _VAZIAS
    }
