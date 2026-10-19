from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from app.arquivos import gravar_json, ler_json

from app.chunking import conferir_sumario, fatiar, secao_base, separar_cabecalho
from app.config import ACERVO_DIR, AVISO_OPERADOR, DATA_DIR
from app.extrair import anexar_figuras, carregar_arquivo, figuras_de_conteudo, listar_arquivos
from app import treino
from app.indice import caminho_indice, fingerprint_acervo
from app.rag import responder
from app.visao import transcricoes_guardadas

LIMITE_BYTES = 25 * 1024 * 1024
_POUCO_TEXTO = 450
_MUITAS_PARTES = 8
_TRECHO_CURTO = 150
_NOME = re.compile(r"^[\w][\w .()\-]{0,180}\.(pdf|docx|txt|md)$", re.IGNORECASE)
_DOCUMENTOS: dict[str, tuple] = {}
_ANALISES: dict[tuple, dict] = {}
_INDICES: dict[str, tuple] = {}
_TRAVA_CACHE = threading.Lock()
_INDEXACAO: dict = {
    "rodando": False,
    "pendente": False,
    "forcar": [],
    "erro": "",
    "inicio": "",
    "fim": "",
    "avisos": [],
    "etapa": "",
    "feitos": 0,
    "total": 0,
    "percentual": 0.0,
    "janela": [0, 50],
}
_TRAVA_INDEXACAO = threading.Lock()
# Onde cada etapa principal cai na barra (de 0 a 100). A indexação sozinha vai até 50; o treino, a conferência e a
# amostra ocupam o resto. Sem documento novo para treinar, a barra salta direto ao fim.
_ETAPAS = {
    "indexando": (0, 50),
    "perguntas de treino": (50, 80),
    "reindexando com as perguntas aprendidas": (80, 88),
    "conferindo com paráfrases": (88, 93),
    "amostra com resposta completa": (93, 100),
}
# Etapas de dentro da indexação: que fatia da janela da etapa principal cada uma ocupa.
_SUBETAPAS = {
    "lendo os documentos": (0.00, 0.10),
    "transcrevendo quadros": (0.10, 0.30),
    "visão geral": (0.30, 0.40),
    "perguntas de busca": (0.40, 0.75),
    "vetores": (0.75, 1.00),
}


def _percentual(etapa: str, feitos: int, total: int, janela: list[int]) -> float:
    inicio, fim = janela
    fracao = min(max(feitos / total, 0.0), 1.0) if total else 0.0
    if etapa in _SUBETAPAS:
        de, ate = _SUBETAPAS[etapa]
        fracao = de + (ate - de) * fracao
    return inicio + (fim - inicio) * fracao


def estado_acervo(acervo: Path | None = None, data_dir: Path | None = None) -> dict:
    acervo = acervo or ACERVO_DIR
    data_dir = data_dir or DATA_DIR
    acervo.mkdir(parents=True, exist_ok=True)
    lidos: list[tuple] = []
    for caminho in listar_arquivos(acervo):
        try:
            versao = _versao(caminho)
        except FileNotFoundError:
            continue
        try:
            lidos.append((caminho, versao, _documento(caminho, versao), ""))
        except Exception as erro:
            logging.warning("Não foi possível ler %s: %s", caminho.name, erro)
            lidos.append((caminho, versao, None, str(erro) or erro.__class__.__name__))
    documentos = [documento for _, _, documento, _ in lidos if documento is not None]
    reais = [documento for documento in documentos if not documento.exemplo]
    usados = {documento.arquivo for documento in (reais or documentos)}
    return {
        "documentos": [
            _analise(versao, documento, documento.arquivo in usados, data_dir)
            if documento is not None
            else _ilegivel(caminho, erro)
            for caminho, versao, documento, erro in lidos
        ],
        "indice": _indice(acervo, data_dir),
    }


def _ilegivel(caminho: Path, erro: str) -> dict:
    return {
        "arquivo": caminho.name,
        "codigo": caminho.stem,
        "revisao": "",
        "titulo": caminho.name,
        "vigente": False,
        "exemplo": False,
        "entra_no_indice": False,
        "responde": False,
        "meta": (caminho.parent / f"{caminho.name}.meta.json").exists(),
        "paginas": [],
        "caracteres": 0,
        "trechos": 0,
        "secoes": [],
        "sumario": None,
        "inicio": "",
        "avisos": [f"Não foi possível ler o arquivo ({erro}). Envie de novo ou remova."],
    }


def iniciar_indexacao(
    acervo: Path | None = None, data_dir: Path | None = None, forcar: set[str] | None = None, preparador=None
) -> dict:
    """Indexa e treina. Se já houver uma preparação rodando, marca outra para logo depois, sem perder o pedido."""
    acervo = acervo or ACERVO_DIR
    data_dir = data_dir or DATA_DIR
    with _TRAVA_INDEXACAO:
        if _INDEXACAO["rodando"]:
            _INDEXACAO["pendente"] = True
            _INDEXACAO["forcar"] = sorted({*_INDEXACAO["forcar"], *(forcar or set())})
            return dict(_INDEXACAO)
        _INDEXACAO.update(
            rodando=True,
            pendente=False,
            forcar=[],
            erro="",
            inicio=datetime.now().isoformat(timespec="seconds"),
            fim="",
            avisos=[],
            etapa="",
            feitos=0,
            total=0,
            percentual=0.0,
            janela=[0, 50],
        )
    threading.Thread(target=_preparar, args=(acervo, data_dir, set(forcar or set()), preparador), daemon=True).start()
    return estado_indexacao()


def estado_indexacao() -> dict:
    with _TRAVA_INDEXACAO:
        return dict(_INDEXACAO)


def _preparar(acervo: Path, data_dir: Path, forcar: set[str], preparador) -> None:
    while True:
        erro = ""
        avisos: list[str] = []

        def avisar(etapa: str, feitos: int = 0, total: int = 0) -> None:
            with _TRAVA_INDEXACAO:
                if etapa in _ETAPAS:
                    _INDEXACAO["janela"] = list(_ETAPAS[etapa])
                novo = _percentual(etapa, feitos, total, _INDEXACAO["janela"])
                # A barra nunca anda para trás dentro de um ciclo.
                _INDEXACAO.update(
                    etapa=etapa,
                    feitos=int(feitos),
                    total=total,
                    percentual=round(max(_INDEXACAO["percentual"], novo), 1),
                )

        try:
            (preparador or treino.preparar)(data_dir, acervo, forcar=forcar, avisos=avisos, progresso=avisar)
        except (FileNotFoundError, ValueError, RuntimeError) as falha:
            erro = str(falha)
        except Exception as falha:
            logging.exception("Falha ao preparar o acervo.")
            erro = f"Falha ao indexar ({falha.__class__.__name__})."
        with _TRAVA_CACHE:
            _ANALISES.clear()
        with _TRAVA_INDEXACAO:
            _INDEXACAO.update(
                erro=erro, fim=datetime.now().isoformat(timespec="seconds"), avisos=avisos, etapa="", percentual=100.0
            )
            if not _INDEXACAO["pendente"]:
                _INDEXACAO["rodando"] = False
                return
            forcar = set(_INDEXACAO["forcar"])
            _INDEXACAO.update(
                pendente=False,
                forcar=[],
                inicio=datetime.now().isoformat(timespec="seconds"),
                fim="",
                percentual=0.0,
                janela=[0, 50],
            )


def _versao(caminho: Path) -> tuple:
    stat = caminho.stat()
    lateral = caminho.parent / f"{caminho.name}.meta.json"
    meta = lateral.stat() if lateral.is_file() else None
    return (
        str(caminho),
        stat.st_size,
        stat.st_mtime_ns,
        meta.st_size if meta else -1,
        meta.st_mtime_ns if meta else -1,
    )


def _documento(caminho: Path, versao: tuple):
    with _TRAVA_CACHE:
        guardado = _DOCUMENTOS.get(versao[0])
        if guardado and guardado[0] == versao:
            return guardado[1]
    documento = carregar_arquivo(caminho)
    with _TRAVA_CACHE:
        _DOCUMENTOS[versao[0]] = (versao, documento)
    return documento


def _analise(versao: tuple, documento, entra_no_indice: bool, data_dir: Path) -> dict:
    chave = (*versao, entra_no_indice, str(data_dir))
    with _TRAVA_CACHE:
        if chave in _ANALISES:
            return _ANALISES[chave]
    analise = _analisar(documento, entra_no_indice, data_dir)
    with _TRAVA_CACHE:
        _ANALISES[chave] = analise
    return analise


def salvar_envio(acervo: Path, nome: str, conteudo: bytes) -> str:
    if len(conteudo) > LIMITE_BYTES:
        raise ValueError("Arquivo acima de 25 MB.")
    if not conteudo:
        raise ValueError("O arquivo está vazio.")
    arquivo = nome_seguro(nome)
    acervo.mkdir(parents=True, exist_ok=True)
    destino = (acervo / arquivo).resolve()
    if destino.parent != acervo.resolve():
        raise ValueError("Nome de arquivo inválido.")
    sufixo = destino.suffix.lower()
    if sufixo == ".pdf" and not conteudo.startswith(b"%PDF-"):
        raise ValueError(f"{arquivo} não é um PDF válido.")
    if sufixo == ".docx" and not conteudo.startswith(b"PK"):
        raise ValueError(f"{arquivo} não é um DOCX válido.")
    if sufixo in {".txt", ".md"} and b"\x00" in conteudo:
        raise ValueError(f"{arquivo} não é um arquivo de texto.")
    temporario = acervo / f".envio-{os.getpid()}-{threading.get_ident()}{sufixo}"
    try:
        temporario.write_bytes(conteudo)
        try:
            carregar_arquivo(temporario)
        except Exception as erro:
            raise ValueError(f"Não foi possível ler {arquivo}: {erro}") from erro
        os.replace(temporario, destino)
    finally:
        temporario.unlink(missing_ok=True)
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
    gravar_json(lateral, payload)


def remover_arquivo(acervo: Path, arquivo: str) -> None:
    caminho = resolver(acervo, arquivo)
    caminho.unlink()
    lateral = caminho.parent / f"{caminho.name}.meta.json"
    if lateral.is_file():
        lateral.unlink()
    with _TRAVA_CACHE:
        _DOCUMENTOS.pop(str(caminho), None)


def consultar(pergunta: str, autor: str = "web", historico: list[dict] | None = None) -> dict:
    resultado = responder(pergunta, autor=autor, origem="web", historico=historico)
    return {
        "pergunta": resultado.pergunta,
        "resposta": resultado.resposta,
        "coberto": resultado.coberto,
        "status": resultado.status,
        "alertas": resultado.alertas,
        "citacoes": resultado.citacoes,
        "cache": resultado.cache,
        "desatualizado": resultado.desatualizado,
        "consulta": resultado.consulta,
        "confianca": resultado.confianca,
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


def _analisar(documento, entra_no_indice: bool, data_dir: Path) -> dict:
    figuras = figuras_de_conteudo(documento.caminho)
    if figuras:
        documento = anexar_figuras(documento, transcricoes_guardadas(data_dir, figuras))
    trechos = fatiar(documento)
    conferencia = conferir_sumario(trechos).get(documento.arquivo)
    secoes: list[dict] = []
    for trecho in trechos:
        nome = secao_base(trecho.secao)
        if secoes and secoes[-1]["nome"] == nome:
            secoes[-1]["trechos"] += 1
            continue
        secoes.append({"nome": nome, "pagina": trecho.pagina or "1", "trechos": 1})
    paginas = [{"numero": numero, "caracteres": len(texto.strip())} for numero, texto in documento.paginas]
    lateral = documento.caminho.parent / f"{documento.caminho.name}.meta.json"
    avisos = _avisos(documento, entra_no_indice, paginas, lateral.exists(), trechos)
    if lateral.exists() and not isinstance(ler_json(lateral, None, "utf-8-sig"), dict):
        avisos.append("O arquivo de metadados está ilegível. Valem os dados lidos do documento; salve em Editar dados.")
    if conferencia and conferencia["ausentes"]:
        avisos.append(aviso_sumario(conferencia))
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
        "sumario": conferencia,
        "inicio": documento.texto.strip()[:360],
        "avisos": avisos,
    }


def aviso_sumario(conferencia: dict) -> str:
    return (
        f"O sumário lista {conferencia['no_sumario']} seções e "
        + ", ".join(conferencia["ausentes"])
        + " não virou seção no índice. O título pode ter ficado colado na seção anterior."
    )


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
    return avisos + _avisos_quebra(trechos)


def _avisos_quebra(trechos) -> list[str]:
    avisos: list[str] = []
    partes: dict[str, int] = {}
    for trecho in trechos:
        partes[secao_base(trecho.secao)] = partes.get(secao_base(trecho.secao), 0) + 1
    longas = [f"{nome} ({quantidade} partes)" for nome, quantidade in partes.items() if quantidade >= _MUITAS_PARTES and nome != "Sumário"]
    if longas:
        avisos.append(
            "Seção longa: " + "; ".join(longas) + ". Se for glossário, está certo. Se não, o título da seção seguinte "
            "pode não ter sido reconhecido e o texto dela ficou dentro desta."
        )
    suspeitas = [nome for nome in partes if "|" in nome or len(nome) > 90 or nome.endswith(".")]
    if suspeitas:
        avisos.append(
            "Nome de seção estranho: " + "; ".join(suspeitas) + ". Pode ser uma linha do texto lida como título."
        )
    curtos = sum(1 for trecho in trechos if len(separar_cabecalho(trecho.texto)[1]) < _TRECHO_CURTO)
    if curtos >= 3:
        avisos.append(
            f"{curtos} trechos com menos de {_TRECHO_CURTO} caracteres. Pode ser linha de tabela ou título solto partido do texto."
        )
    return avisos


def _indice(acervo: Path, data_dir: Path) -> dict:
    arquivo = caminho_indice(data_dir) / "trechos.json"
    try:
        stat = arquivo.stat()
    except FileNotFoundError:
        return {"existe": False, "desatualizado": True, "trechos": 0, "vigentes": 0}
    versao = (stat.st_size, stat.st_mtime_ns)
    with _TRAVA_CACHE:
        guardado = _INDICES.get(str(arquivo))
    if guardado and guardado[0] == versao:
        resumo = guardado[1]
    else:
        payload = ler_json(arquivo)
        if not isinstance(payload, dict):
            return {"existe": False, "desatualizado": True, "trechos": 0, "vigentes": 0}
        trechos = payload.get("trechos") or []
        resumo = {
            "fingerprint": payload.get("fingerprint"),
            "trechos": len(trechos),
            "vigentes": sum(1 for trecho in trechos if trecho.get("vigente")),
        }
        with _TRAVA_CACHE:
            _INDICES[str(arquivo)] = (versao, resumo)
    return {
        "existe": True,
        "desatualizado": resumo["fingerprint"] != fingerprint_acervo(acervo),
        "trechos": resumo["trechos"],
        "vigentes": resumo["vigentes"],
    }
