from __future__ import annotations

import re
from dataclasses import dataclass

from app.extrair import Documento

_TITULO_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TITULO_PALAVRA = re.compile(
    r"^(se[cç][aã]o|cap[ií]tulo|anexo|item)\s+(\d+(?:\.\d+)*)?(?:\s*[-–.:])?\s*(.*)$",
    re.IGNORECASE,
)
_TITULO_NUMERO = re.compile(
    r"^(\d+(?:\.\d+)*)\s*[-–.)]?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ].{0,80})$"
)
_ITEM = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\.?\s+(\S.+)$")
_PONTILHADO = re.compile(r"\.{5,}\s*\d*\s*$")
_TAMANHO = 2000
_SOBREPOSICAO = 200


@dataclass
class Trecho:
    id: str
    texto: str
    arquivo: str
    codigo: str
    revisao: str
    vigente: bool
    titulo: str
    secao: str
    pagina: str


def fatiar(documento: Documento) -> list[Trecho]:
    blocos = _blocos(documento)
    trechos: list[Trecho] = []
    for secao, pagina, texto in blocos:
        partes = _janelas(texto)
        for indice, parte in enumerate(partes, start=1):
            nome_secao = secao if len(partes) == 1 else f"{secao} (parte {indice})"
            corpo = f"Seção: {nome_secao}\n{parte.strip()}"
            trechos.append(
                Trecho(
                    id=f"{documento.arquivo}:{len(trechos) + 1}",
                    texto=corpo,
                    arquivo=documento.arquivo,
                    codigo=documento.codigo,
                    revisao=documento.revisao,
                    vigente=documento.vigente,
                    titulo=documento.titulo,
                    secao=nome_secao,
                    pagina=pagina,
                )
            )
    return trechos


def _blocos(documento: Documento) -> list[tuple[str, str, str]]:
    blocos: list[tuple[str, str, list[str]]] = []
    secao_atual = "Documento"
    pagina_atual = ""
    linhas: list[str] = []
    repetidas = _linhas_repetidas(documento.paginas)
    dentro_figura = False

    def fechar() -> None:
        texto = "\n".join(linhas).strip()
        if texto:
            blocos.append((secao_atual, pagina_atual, [texto]))

    for numero, pagina in documento.paginas:
        sumario = _eh_sumario(pagina)
        if sumario and secao_atual != "Sumário":
            fechar()
            secao_atual = "Sumário"
            pagina_atual = str(numero)
            linhas = []
        for linha in pagina.splitlines():
            abertura = re.match(r"^<<<FIGURA pagina=(\d+)>>>$", linha.strip())
            if abertura:
                dentro_figura = True
                if not pagina_atual:
                    pagina_atual = str(numero)
                linhas.append(f"Transcrição da figura da página {abertura.group(1)}:")
                continue
            if linha.strip().startswith("<<<FIM FIGURA>>>"):
                dentro_figura = False
                linhas.append("Fim da transcrição da figura.")
                linha = linha.strip()[len("<<<FIM FIGURA>>>") :]
                if not linha.strip():
                    continue
            if " ".join(linha.split()) in repetidas:
                continue
            titulo = None if dentro_figura or sumario else _titulo(linha)
            if titulo:
                fechar()
                secao_atual = titulo
                pagina_atual = str(numero)
                linhas = [linha]
                continue
            if linha.strip() and not pagina_atual:
                pagina_atual = str(numero)
            linhas.append(linha)
    fechar()
    if not blocos and documento.texto.strip():
        return [("Documento", "1", documento.texto.strip())]
    return [(secao, pagina, "\n".join(partes)) for secao, pagina, partes in blocos]


def _titulo(linha: str) -> str | None:
    limpa = linha.strip()
    if not limpa:
        return None
    markdown = _TITULO_MD.match(limpa)
    if markdown:
        return markdown.group(2).strip()
    item = _ITEM.match(limpa)
    if item:
        rotulo = f"{item.group(1)} {item.group(2).strip()}"
        if len(rotulo) > 110:
            rotulo = rotulo[:107].rstrip() + "..."
        return rotulo
    palavra = _TITULO_PALAVRA.match(limpa)
    if palavra and len(limpa) <= 100:
        numero = palavra.group(2) or ""
        resto = (palavra.group(3) or "").strip()
        prefixo = palavra.group(1).capitalize()
        return " ".join(parte for parte in (prefixo, numero, resto) if parte)
    numero = _TITULO_NUMERO.match(limpa)
    if numero and not limpa.endswith(".") and len(limpa) <= 90:
        return f"{numero.group(1)} {numero.group(2).strip()}"
    return None


def _eh_sumario(pagina: str) -> bool:
    return sum(1 for linha in pagina.splitlines() if _PONTILHADO.search(linha)) >= 5


def _linhas_repetidas(paginas: list[tuple[int, str]]) -> set[str]:
    if len(paginas) < 2:
        return set()
    contagem: dict[str, int] = {}
    for _, texto in paginas:
        vistas: set[str] = set()
        for linha in texto.splitlines():
            chave = " ".join(linha.split())
            if len(chave) < 12 or chave in vistas:
                continue
            vistas.add(chave)
            contagem[chave] = contagem.get(chave, 0) + 1
    minimo = 2 if len(paginas) == 2 else 3
    return {chave for chave, vezes in contagem.items() if vezes >= minimo}


def _janelas(texto: str) -> list[str]:
    texto = texto.strip()
    if len(texto) <= _TAMANHO:
        return [texto] if texto else []
    partes: list[str] = []
    inicio = 0
    while inicio < len(texto):
        fim = min(inicio + _TAMANHO, len(texto))
        if fim < len(texto):
            quebra = texto.rfind("\n", inicio + _TAMANHO // 2, fim)
            if quebra == -1:
                quebra = texto.rfind(" ", inicio + _TAMANHO // 2, fim)
            if quebra != -1:
                fim = quebra
        partes.append(texto[inicio:fim].strip())
        if fim >= len(texto):
            break
        inicio = max(fim - _SOBREPOSICAO, inicio + 1)
    return [parte for parte in partes if parte]
