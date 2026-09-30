from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

EXTENSOES = {".pdf", ".docx", ".txt", ".md"}

_CODIGO = re.compile(r"(?im)^c[oó]digo\s*:\s*(.+?)\s*$")
_REVISAO = re.compile(r"(?im)^revis[aã]o\s*:\s*(.+?)\s*$")
_STATUS = re.compile(r"(?im)^status\s*:\s*(.+?)\s*$")
_TITULO = re.compile(r"(?m)^#\s+(.+?)\s*$")
_STATUS_INATIVO = {
    "substituido",
    "substituído",
    "revogado",
    "obsoleto",
    "cancelado",
    "nao vigente",
    "não vigente",
}


_FIGURA = re.compile(r"(quadro|tabela|figura)\s+a\s+seguir", re.IGNORECASE)
_LOGOTIPO_MINIMO = 3


@dataclass
class Figura:
    arquivo: str
    pagina: int
    hash: str
    dados: bytes
    tipo: str


@dataclass
class Documento:
    arquivo: str
    caminho: Path
    texto: str
    paginas: list[tuple[int, str]]
    codigo: str
    revisao: str
    vigente: bool
    titulo: str

    @property
    def exemplo(self) -> bool:
        return self.codigo.upper().startswith("IT-EXEMPLO") or self.caminho.stem == "exemplo-normativo"


def listar_arquivos(acervo: Path) -> list[Path]:
    if not acervo.exists():
        return []
    arquivos: list[Path] = []
    for caminho in sorted(acervo.rglob("*")):
        if not caminho.is_file() or caminho.name.startswith("."):
            continue
        if caminho.name.endswith(".meta.json"):
            continue
        if caminho.suffix.lower() not in EXTENSOES:
            continue
        arquivos.append(caminho)
    return arquivos


def carregar_acervo(acervo: Path) -> list[Documento]:
    documentos = [carregar_arquivo(caminho) for caminho in listar_arquivos(acervo)]
    reais = [documento for documento in documentos if not documento.exemplo]
    return reais or documentos


def carregar_arquivo(caminho: Path) -> Documento:
    paginas = _ler_paginas(caminho)
    texto = "\n\n".join(trecho.strip() for _, trecho in paginas if trecho.strip())
    meta = _metadados(caminho, texto)
    return Documento(
        arquivo=caminho.name,
        caminho=caminho,
        texto=texto,
        paginas=paginas,
        codigo=meta["codigo"],
        revisao=meta["revisao"],
        vigente=meta["vigente"],
        titulo=meta["titulo"],
    )


def _ler_paginas(caminho: Path) -> list[tuple[int, str]]:
    sufixo = caminho.suffix.lower()
    if sufixo == ".pdf":
        return _ler_pdf(caminho)
    if sufixo == ".docx":
        return _ler_docx(caminho)
    return [(1, caminho.read_text(encoding="utf-8-sig"))]


def figuras_de_conteudo(caminho: Path) -> list[Figura]:
    if caminho.suffix.lower() != ".pdf" or not caminho.is_file():
        return []
    from pypdf import PdfReader

    leitor = PdfReader(str(caminho))
    brutas: list[tuple[int, str, bytes, str]] = []
    for numero, pagina in enumerate(leitor.pages, start=1):
        for imagem in pagina.images:
            dados = bytes(imagem.data or b"")
            if not dados:
                continue
            resumo = hashlib.sha256(dados).hexdigest()
            brutas.append((numero, resumo, dados, _tipo_imagem(dados)))
    contagem: dict[str, int] = {}
    for _, resumo, _, _ in brutas:
        contagem[resumo] = contagem.get(resumo, 0) + 1
    return [
        Figura(caminho.name, numero, resumo, dados, tipo)
        for numero, resumo, dados, tipo in brutas
        if contagem[resumo] < _LOGOTIPO_MINIMO
    ]


def anexar_figuras(documento: Documento, transcricoes: dict[int, str]) -> Documento:
    if not transcricoes:
        return documento
    paginas: list[tuple[int, str]] = []
    for numero, texto in documento.paginas:
        figura = " ".join(str(transcricoes.get(numero) or "").split())
        if not figura or figura == "SEM_CONTEUDO":
            paginas.append((numero, texto))
            continue
        paginas.append((numero, _inserir_figura(numero, texto, str(transcricoes[numero]).strip())))
    texto = "\n\n".join(trecho.strip() for _, trecho in paginas if trecho.strip())
    return replace(documento, paginas=paginas, texto=texto)


def _inserir_figura(numero: int, texto: str, figura: str) -> str:
    bloco = f"<<<FIGURA pagina={numero}>>>\n{figura}\n<<<FIM FIGURA>>>"
    encontrado = _FIGURA.search(texto)
    if not encontrado:
        return f"{texto}\n{bloco}".strip()
    fim_da_linha = texto.find("\n", encontrado.end())
    if fim_da_linha == -1:
        return f"{texto}\n{bloco}"
    return texto[:fim_da_linha] + "\n" + bloco + texto[fim_da_linha:]


def _tipo_imagem(dados: bytes) -> str:
    if dados.startswith(b"\x89PNG"):
        return "image/png"
    if dados.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if dados.startswith(b"GIF8"):
        return "image/gif"
    if dados.startswith(b"RIFF") and dados[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _ler_pdf(caminho: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    leitor = PdfReader(str(caminho))
    paginas: list[tuple[int, str]] = []
    for indice, pagina in enumerate(leitor.pages, start=1):
        try:
            bruto = pagina.extract_text(extraction_mode="layout") or ""
        except TypeError:
            bruto = pagina.extract_text() or ""
        paginas.append((indice, _compactar_layout(bruto)))
    return paginas or [(1, "")]


def _compactar_layout(texto: str) -> str:
    linhas: list[str] = []
    for linha in texto.splitlines():
        if not linha.strip():
            continue
        marcada = re.sub(r" {3,}", " | ", linha.strip())
        marcada = re.sub(r" +", " ", marcada)
        marcada = re.sub(r"(?: \| ){2,}", " | ", marcada)
        linhas.append(marcada.strip(" |"))
    return "\n".join(linhas)


def _ler_docx(caminho: Path) -> list[tuple[int, str]]:
    import docx

    documento = docx.Document(str(caminho))
    partes: list[str] = []
    for paragrafo in documento.paragraphs:
        texto = paragrafo.text.strip()
        if not texto:
            continue
        estilo = paragrafo.style.name if paragrafo.style is not None else ""
        if estilo.lower().startswith("heading"):
            digitos = re.findall(r"\d+", estilo)
            nivel = min(int(digitos[0]), 6) if digitos else 1
            partes.append(f"{'#' * nivel} {texto}")
        else:
            partes.append(texto)
    return [(1, "\n\n".join(partes))]


def _metadados(caminho: Path, texto: str) -> dict[str, str | bool]:
    lateral = caminho.parent / f"{caminho.name}.meta.json"
    dados: dict = {}
    if lateral.exists():
        dados = json.loads(lateral.read_text(encoding="utf-8-sig"))
    codigo = str(dados.get("codigo") or _grupo(_CODIGO, texto) or caminho.stem).strip()
    revisao = str(dados.get("revisao") or _grupo(_REVISAO, texto) or "").strip()
    titulo = str(dados.get("titulo") or _grupo(_TITULO, texto) or caminho.stem).strip()
    if "vigente" in dados:
        vigente = bool(dados["vigente"])
    else:
        status = (_grupo(_STATUS, texto) or "vigente").strip().casefold()
        vigente = status not in _STATUS_INATIVO
    return {"codigo": codigo, "revisao": revisao, "titulo": titulo, "vigente": vigente}


def _grupo(expressao: re.Pattern[str], texto: str) -> str:
    encontrado = expressao.search(texto)
    return encontrado.group(1).strip() if encontrado else ""
