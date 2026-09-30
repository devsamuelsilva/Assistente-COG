from __future__ import annotations

import json
import re
from pathlib import Path

from app.chunking import fatiar
from app.config import ACERVO_DIR, AVISO_OPERADOR, DATA_DIR
from app.extrair import EXTENSOES, carregar_arquivo, listar_arquivos
from app.indice import caminho_indice, fingerprint_acervo
from app.rag import responder

_LIMITE_BYTES = 25 * 1024 * 1024
_POUCO_TEXTO = 450
_NOME = re.compile(r"^[\w][\w .()\-]{0,180}\.(pdf|docx|txt|md)$", re.IGNORECASE)


def estado_acervo(acervo: Path | None = None, data_dir: Path | None = None) -> dict:
    acervo = acervo or ACERVO_DIR
    data_dir = data_dir or DATA_DIR
    acervo.mkdir(parents=True, exist_ok=True)
    documentos = [carregar_arquivo(caminho) for caminho in listar_arquivos(acervo)]
    reais = [documento for documento in documentos if not documento.exemplo]
    usados = {documento.arquivo for documento in (reais or documentos)}
    return {
        "documentos": [_analisar(documento, documento.arquivo in usados) for documento in documentos],
        "indice": _indice(acervo, data_dir),
    }


def salvar_envio(acervo: Path, nome: str, conteudo: bytes) -> str:
    if len(conteudo) > _LIMITE_BYTES:
        raise ValueError("Arquivo acima de 25 MB.")
    if not conteudo:
        raise ValueError("O arquivo está vazio.")
    arquivo = nome_seguro(nome)
    acervo.mkdir(parents=True, exist_ok=True)
    destino = (acervo / arquivo).resolve()
    if destino.parent != acervo.resolve():
        raise ValueError("Nome de arquivo inválido.")
    destino.write_bytes(conteudo)
    return arquivo


def gravar_meta(acervo: Path, arquivo: str, codigo: str, revisao: str, titulo: str, vigente: bool) -> None:
    caminho = resolver(acervo, arquivo)
    codigo = codigo.strip()
    titulo = titulo.strip()
    if not codigo or not titulo:
        raise ValueError("Informe o código e o título do normativo.")
    lateral = caminho.parent / f"{caminho.name}.meta.json"
    payload = {
        "codigo": codigo,
        "revisao": revisao.strip(),
        "titulo": titulo,
        "vigente": bool(vigente),
    }
    lateral.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def remover_arquivo(acervo: Path, arquivo: str) -> None:
    caminho = resolver(acervo, arquivo)
    caminho.unlink()
    lateral = caminho.parent / f"{caminho.name}.meta.json"
    if lateral.is_file():
        lateral.unlink()


def consultar(pergunta: str, autor: str = "web", historico: list[dict] | None = None) -> dict:
    resultado = responder(pergunta, autor=autor, origem="web", historico=historico)
    return {
        "pergunta": resultado.pergunta,
        "resposta": resultado.resposta,
        "coberto": resultado.coberto,
        "citacoes": resultado.citacoes,
        "cache": resultado.cache,
        "desatualizado": resultado.desatualizado,
        "aviso": AVISO_OPERADOR,
    }


def resolver(acervo: Path, arquivo: str) -> Path:
    nome = nome_seguro(arquivo)
    caminho = (acervo / nome).resolve()
    if caminho.parent != acervo.resolve() or not caminho.is_file():
        raise FileNotFoundError("Arquivo não encontrado no acervo.")
    return caminho


def nome_seguro(nome: str) -> str:
    bruto = str(nome).strip()
    if not bruto or "/" in bruto or "\\" in bruto or bruto.startswith("."):
        raise ValueError("Envie um PDF, DOCX, TXT ou MD com um nome simples.")
    base = Path(bruto).name
    if not _NOME.match(base):
        raise ValueError("Envie um PDF, DOCX, TXT ou MD com um nome simples.")
    return base


def _analisar(documento, entra_no_indice: bool) -> dict:
    trechos = fatiar(documento)
    secoes: list[dict] = []
    for trecho in trechos:
        nome = re.sub(r" \(parte \d+\)$", "", trecho.secao)
        if secoes and secoes[-1]["nome"] == nome:
            secoes[-1]["trechos"] += 1
            continue
        secoes.append({"nome": nome, "pagina": trecho.pagina or "1", "trechos": 1})
    paginas = [{"numero": numero, "caracteres": len(texto.strip())} for numero, texto in documento.paginas]
    lateral = documento.caminho.parent / f"{documento.caminho.name}.meta.json"
    avisos = _avisos(documento, entra_no_indice, paginas, lateral.exists(), trechos)
    return {
        "arquivo": documento.arquivo,
        "codigo": documento.codigo,
        "revisao": documento.revisao,
        "titulo": documento.titulo,
        "vigente": documento.vigente,
        "exemplo": documento.exemplo,
        "entra_no_indice": entra_no_indice,
        "responde": entra_no_indice and documento.vigente,
        "meta": lateral.exists(),
        "paginas": paginas,
        "caracteres": len(documento.texto),
        "trechos": len(trechos),
        "secoes": secoes,
        "inicio": documento.texto.strip()[:360],
        "avisos": avisos,
    }


def _avisos(documento, entra_no_indice: bool, paginas: list[dict], tem_meta: bool, trechos) -> list[str]:
    avisos: list[str] = []
    if documento.exemplo and not entra_no_indice:
        avisos.append("Arquivo de exemplo. Fica de fora enquanto houver normativo real na pasta.")
    if not documento.vigente:
        avisos.append("Fora de vigência. Este texto não entra nas respostas.")
    if not documento.texto.strip() or not trechos:
        avisos.append("Nenhum texto extraído. A consulta não vai encontrar este arquivo.")
    fracas = [str(pagina["numero"]) for pagina in paginas if pagina["caracteres"] < _POUCO_TEXTO]
    if fracas and documento.texto.strip():
        avisos.append(
            "Página "
            + ", ".join(fracas)
            + " com pouco texto. Figura ou fluxograma pode não entrar na consulta."
        )
    if not tem_meta:
        avisos.append("Sem metadados ao lado do arquivo. Confira código, revisão e título antes de indexar.")
    return avisos


def _indice(acervo: Path, data_dir: Path) -> dict:
    arquivo = caminho_indice(data_dir) / "trechos.json"
    if not arquivo.exists():
        return {"existe": False, "desatualizado": True, "trechos": 0, "vigentes": 0}
    payload = json.loads(arquivo.read_text(encoding="utf-8"))
    trechos = payload.get("trechos") or []
    return {
        "existe": True,
        "desatualizado": payload.get("fingerprint") != fingerprint_acervo(acervo),
        "trechos": len(trechos),
        "vigentes": sum(1 for trecho in trechos if trecho.get("vigente")),
    }
