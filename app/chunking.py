from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.extrair import Documento

_TITULO_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TITULO_PALAVRA = re.compile(
    r"^(se[cç][aã]o|cap[ií]tulo|anexo|item)\s+(\d+(?:\.\d+)*|(?-i:[IVXLC]+)\b)(?:\s*[-–.:])?\s*(.*)$",
    re.IGNORECASE,
)
_TITULO_NUMERO = re.compile(
    r"^(\d+(?:\.\d+)*)\s*[-–.)]?(?:\s*\|)?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ].{0,80})$"
)
_ITEM = re.compile(r"^(\d+\.\d+(?:\.\d+)*)\.?\s+(\S.+)$")
_PONTILHADO = re.compile(r"\.{5,}\s*\d*\s*$")
_LINHA_SUMARIO = re.compile(r"^(\d+(?:\.\d+)*)\.?\s*\|?\s*[A-ZÁÉÍÓÚÂÊÔÃÕÇ]")
_NUMERO_SECAO = re.compile(r"^(\d+(?:\.\d+)*)\s")
_LETRA_PARTIDA = re.compile(r"(?<![\w.])([^\W\d_]) \| ([^\W\d_])")
_COPIA_NO_CODIGO = re.compile(r"\s*\(\d+\)$")
_CABECALHO = ("Documento: ", "Seção: ", "Dentro de: ")
_FIM_DE_FRASE = ".:;"
PARTE = re.compile(r" \(parte (\d+)\)$")
_TAMANHO = 2000
_REPETICAO = 0.6
_SOBREPOSICAO = 200


def secao_base(secao: str) -> str:
    return PARTE.sub("", secao)


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
    perguntas: list[str] = field(default_factory=list)


def fatiar(documento: Documento) -> list[Trecho]:
    blocos = _blocos(documento)
    documento_linha = linha_documento(documento)
    trechos: list[Trecho] = []
    for secao, pagina, texto, caminho in blocos:
        partes = _janelas(texto)
        for indice, parte in enumerate(partes, start=1):
            nome_secao = secao if len(partes) == 1 else f"{secao} (parte {indice})"
            cabecalho = [documento_linha, f"Seção: {nome_secao}"]
            if caminho:
                cabecalho.append("Dentro de: " + " > ".join(caminho))
            trechos.append(
                Trecho(
                    id=f"{documento.arquivo}:{len(trechos) + 1}",
                    texto="\n".join(cabecalho) + "\n" + parte.strip(),
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


def separar_cabecalho(texto: str) -> tuple[str, str]:
    linhas = texto.split("\n")
    fim = 0
    while fim < len(linhas) and linhas[fim].startswith(_CABECALHO):
        fim += 1
    return "\n".join(linhas[:fim]), "\n".join(linhas[fim:])


def codigo_limpo(codigo: str) -> str:
    return _COPIA_NO_CODIGO.sub("", codigo.strip())


def titulo_proprio(documento: Documento) -> str:
    """Título do normativo, ou vazio quando o título salvo é só o código ou o nome do arquivo."""
    titulo = documento.titulo.strip()
    repetidos = {_normal(codigo_limpo(documento.codigo)), _normal(documento.caminho.stem), _normal(codigo_limpo(documento.caminho.stem))}
    if not titulo or _normal(codigo_limpo(titulo)) in repetidos:
        return ""
    return titulo


def linha_documento(documento: Documento) -> str:
    codigo = codigo_limpo(documento.codigo)
    titulo = titulo_proprio(documento)
    return f"Documento: {codigo} – {titulo}" if titulo else f"Documento: {codigo}"


def conferir_sumario(trechos: list[Trecho]) -> dict[str, dict]:
    sumarios: dict[str, set[str]] = {}
    indexadas: dict[str, set[str]] = {}
    for trecho in trechos:
        if trecho.secao.startswith("Sumário"):
            numeros = sumarios.setdefault(trecho.arquivo, set())
            for linha in separar_cabecalho(trecho.texto)[1].splitlines():
                encontrado = _LINHA_SUMARIO.match(linha.strip())
                if encontrado:
                    numeros.add(encontrado.group(1))
            continue
        presentes = indexadas.setdefault(trecho.arquivo, set())
        cabecalho = separar_cabecalho(trecho.texto)[0]
        acima = next((linha[len("Dentro de: ") :] for linha in cabecalho.splitlines() if linha.startswith("Dentro de: ")), "")
        for secao in [trecho.secao, *acima.split(" > ")]:
            encontrado = _NUMERO_SECAO.match(secao)
            if encontrado:
                presentes.add(encontrado.group(1))
    conferencia: dict[str, dict] = {}
    for arquivo, numeros in sumarios.items():
        if not numeros:
            continue
        presentes = indexadas.get(arquivo, set())
        ausentes = {
            numero
            for numero in numeros
            if numero not in presentes and not any(outro.startswith(numero + ".") for outro in presentes)
        }
        conferencia[arquivo] = {
            "no_sumario": len(numeros),
            "ausentes": sorted(ausentes, key=lambda numero: [int(parte) for parte in numero.split(".")]),
        }
    return conferencia


def _blocos(documento: Documento) -> list[tuple[str, str, str, list[str]]]:
    blocos: list[tuple[str, str, str, list[str]]] = []
    secao_atual = "Documento"
    caminho_atual: list[str] = []
    aberto_por_titulo = False
    pagina_atual = ""
    linhas: list[str] = []
    repetidas = _linhas_repetidas(documento.paginas)
    no_sumario = _numeros_do_sumario(documento.paginas)
    titulos: dict[str, str] = {}
    topo = ""
    dentro_figura = False

    def fechar() -> None:
        texto = "\n".join(linhas).strip()
        if not texto:
            return
        if aberto_por_titulo and sum(1 for linha in linhas if linha.strip()) <= 1:
            return
        blocos.append((secao_atual, pagina_atual, texto, caminho_atual))

    for numero_pagina, pagina in documento.paginas:
        sumario = _eh_sumario(pagina)
        if sumario and secao_atual != "Sumário":
            fechar()
            secao_atual = "Sumário"
            caminho_atual = []
            aberto_por_titulo = False
            pagina_atual = str(numero_pagina)
            linhas = []
        for linha in pagina.splitlines():
            abertura = re.match(r"^<<<FIGURA pagina=(\d+)>>>$", linha.strip())
            if abertura:
                dentro_figura = True
                if not pagina_atual:
                    pagina_atual = str(numero_pagina)
                linhas.append(f"Transcrição da figura da página {abertura.group(1)}:")
                continue
            if linha.strip().startswith("<<<FIM FIGURA>>>"):
                dentro_figura = False
                linhas.append("Fim da transcrição da figura.")
                linha = linha.strip()[len("<<<FIM FIGURA>>>") :]
                if not linha.strip():
                    continue
            chave = " ".join(linha.split())
            if chave in repetidas or _molde(chave) in repetidas:
                continue
            titulo = None if dentro_figura or sumario else _titulo(linha, no_sumario)
            if titulo:
                fechar()
                numero = _numero(titulo)
                caminho_atual = _caminho(numero, titulos)
                if numero:
                    titulos[numero] = titulo
                    if "." not in numero:
                        topo = titulo
                elif topo and titulo.casefold().startswith("anexo") and "anexo" in topo.casefold():
                    caminho_atual = [topo]
                secao_atual = titulo
                aberto_por_titulo = True
                pagina_atual = str(numero_pagina)
                linhas = [linha]
                continue
            if linha.strip() and not pagina_atual:
                pagina_atual = str(numero_pagina)
            linhas.append(linha)
    fechar()
    if not blocos and documento.texto.strip():
        return [("Documento", "1", documento.texto.strip(), [])]
    return blocos


def _numero(titulo: str) -> str:
    encontrado = _NUMERO_SECAO.match(titulo)
    return encontrado.group(1) if encontrado else ""


def _caminho(numero: str, titulos: dict[str, str]) -> list[str]:
    if not numero:
        return []
    partes = numero.split(".")
    prefixos = [".".join(partes[:tamanho]) for tamanho in range(1, len(partes))]
    return [titulos[prefixo] for prefixo in prefixos if prefixo in titulos]


def _numeros_do_sumario(paginas: list[tuple[int, str]]) -> set[str]:
    numeros: set[str] = set()
    for _, pagina in paginas:
        if not _eh_sumario(pagina):
            continue
        for linha in pagina.splitlines():
            encontrado = _LINHA_SUMARIO.match(linha.strip())
            if encontrado:
                numeros.add(encontrado.group(1))
    return numeros


def _no_sumario(numero: str, no_sumario: set[str]) -> bool:
    if not no_sumario:
        return True
    partes = numero.split(".")
    return any(".".join(partes[:tamanho]) in no_sumario for tamanho in range(1, len(partes) + 1))


def _titulo(linha: str, no_sumario: set[str] | None = None) -> str | None:
    limpa = linha.strip()
    if not limpa:
        return None
    no_sumario = no_sumario or set()
    markdown = _TITULO_MD.match(limpa)
    if markdown:
        return _rotulo(markdown.group(2))
    item = _ITEM.match(limpa)
    if item:
        if not _no_sumario(item.group(1), no_sumario):
            return None
        rotulo = _rotulo(f"{item.group(1)} {item.group(2)}")
        if len(rotulo) > 110:
            rotulo = rotulo[:107].rstrip() + "..."
        return rotulo
    palavra = _TITULO_PALAVRA.match(limpa)
    if palavra and len(limpa) <= 100:
        numero = palavra.group(2) or ""
        resto = (palavra.group(3) or "").strip()
        prefixo = palavra.group(1).capitalize()
        return _rotulo(" ".join(parte for parte in (prefixo, numero, resto) if parte))
    numero = _TITULO_NUMERO.match(limpa)
    if numero and not limpa.endswith(".") and len(limpa) <= 90 and _no_sumario(numero.group(1), no_sumario):
        return _rotulo(f"{numero.group(1)} {numero.group(2)}")
    return None


def _rotulo(texto: str) -> str:
    texto = _LETRA_PARTIDA.sub(r"\1\2", texto)
    texto = re.sub(r"\s*\|\s*", " ", texto)
    return " ".join(texto.split())


def _eh_sumario(pagina: str) -> bool:
    return sum(1 for linha in pagina.splitlines() if _PONTILHADO.search(linha)) >= 5


def _linhas_repetidas(paginas: list[tuple[int, str]]) -> set[str]:
    """Linhas de cabeçalho e rodapé: a mesma linha longa em 3 páginas, ou o mesmo molde em 60% delas.

    O molde troca números por "#", para "7 de 11" e "8 de 11" contarem como a mesma linha.
    """
    if len(paginas) < 2:
        return set()
    contagem: dict[str, int] = {}
    moldes: dict[str, int] = {}
    for _, texto in paginas:
        chaves = {" ".join(linha.split()) for linha in texto.splitlines()} - {""}
        for chave in chaves:
            if len(chave) >= 12:
                contagem[chave] = contagem.get(chave, 0) + 1
        for molde in {_molde(chave) for chave in chaves}:
            moldes[molde] = moldes.get(molde, 0) + 1
    minimo = 2 if len(paginas) == 2 else 3
    repetidas = {chave for chave, vezes in contagem.items() if vezes >= minimo}
    if len(paginas) >= 3:
        repetidas |= {molde for molde, vezes in moldes.items() if vezes >= _REPETICAO * len(paginas)}
    return repetidas


def _molde(linha: str) -> str:
    return re.sub(r"\d+", "#", linha)


def _janelas(texto: str) -> list[str]:
    texto = texto.strip()
    if len(texto) <= _TAMANHO:
        return [texto] if texto else []
    partes: list[str] = []
    inicio = 0
    while inicio < len(texto):
        fim = min(inicio + _TAMANHO, len(texto))
        if fim < len(texto):
            quebra = _fim_de_frase(texto, inicio + _TAMANHO // 2, fim)
            if quebra == -1:
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


def _fim_de_frase(texto: str, inicio: int, fim: int) -> int:
    quebra = texto.rfind("\n", inicio, fim)
    while quebra != -1:
        anterior = quebra - 1
        while anterior > inicio and texto[anterior] in " \t":
            anterior -= 1
        if texto[anterior] in _FIM_DE_FRASE:
            return quebra
        quebra = texto.rfind("\n", inicio, quebra)
    return -1


def _normal(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.casefold().split())
