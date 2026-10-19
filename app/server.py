from __future__ import annotations

import json
import logging
import threading
from collections import deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from pydantic import BaseModel, Field

from app import avaliacao, treino, uso, visao
from app.config import ACERVO_DIR, DATA_DIR, HOST, PORT, RAIZ, RERANKER_MODEL, TEAMS_OUTGOING_HMAC_SECRET
from app.indice import aquecer_modelo, caminho_indice
from app.ons import ErroOns, responder_ons
from app.reranker import aquecer as aquecer_reranker
from app.painel import (
    LIMITE_BYTES,
    consultar,
    estado_acervo,
    estado_indexacao,
    gravar_meta,
    iniciar_indexacao,
    remover_arquivo,
    salvar_envio,
)
from app.registro import registrar_retorno
from app.teams import assinatura_valida, limpar_pergunta, processar_mencao

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_ATIVIDADES: deque[str] = deque(maxlen=200)


@asynccontextmanager
async def _vida(app: FastAPI):
    try:
        if uso.migrar_logs(DATA_DIR):
            logging.info("Log de consultas enxugado. A cópia anterior ficou em data/logs/consultas.jsonl.bak.")
    except OSError:
        logging.exception("Não foi possível enxugar o log de consultas.")
    threading.Thread(target=_aquecer_acervo, daemon=True).start()
    try:
        aquecer_modelo()
        aquecer_reranker(RERANKER_MODEL)
    except Exception:
        logging.exception("Não foi possível carregar os modelos locais de embeddings e reordenação.")
    yield


def _aquecer_acervo() -> None:
    try:
        estado_acervo()
    except Exception:
        logging.exception("Não foi possível analisar o acervo na subida.")


app = FastAPI(title="Assistente do COG", lifespan=_vida)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(OpenAIError)
async def _erro_openai(_request: Request, erro: OpenAIError) -> JSONResponse:
    logging.warning("Falha na OpenAI: %s", erro)
    if {getattr(erro, "code", None), getattr(erro, "type", None)} & {"insufficient_quota", "credit_balance_exhausted"}:
        detalhe = (
            "Os créditos da conta da OpenAI acabaram. Adicione créditos em platform.openai.com "
            "(Settings > Billing) e pergunte de novo."
        )
    else:
        detalhe = f"O GPT não respondeu ({erro.__class__.__name__}). Tente de novo em instantes."
    return JSONResponse({"detail": detalhe}, status_code=502)


class TurnoIn(BaseModel):
    pergunta: str = ""
    resposta: str = ""


class PerguntaIn(BaseModel):
    pergunta: str = Field(min_length=1, max_length=4000)
    historico: list[TurnoIn] = Field(default_factory=list, max_length=8)


class RodarIn(BaseModel):
    modo: str | None = None
    reranker: str | None = Field(default=None, max_length=200)


class RevisaoIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    veredito: str
    resposta_corrigida: str = Field(default="", max_length=4000)
    observacao: str = Field(default="", max_length=1000)


class RetornoIn(BaseModel):
    consulta: str = Field(min_length=1, max_length=40)
    util: bool
    comentario: str = Field(default="", max_length=1000)


class MetaIn(BaseModel):
    arquivo: str
    codigo: str
    revisao: str = ""
    titulo: str
    vigente: bool = True


class ArquivoIn(BaseModel):
    arquivo: str


class RevisaoVisaoIn(BaseModel):
    texto: str | None = None


@app.get("/api/acervo")
def api_acervo() -> dict:
    return estado_acervo()


@app.post("/api/acervo")
def api_enviar(arquivo: UploadFile = File(...)) -> dict:
    conteudo = arquivo.file.read(LIMITE_BYTES + 1)
    try:
        nome = salvar_envio(ACERVO_DIR, arquivo.filename or "", conteudo)
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    # Só guarda e analisa. A indexação e o treino começam quando o usuário confirma código, revisão e título
    # (PUT /api/acervo/meta), para o documento entrar no índice já com o nome certo.
    return {**estado_acervo(), "enviado": nome}


@app.put("/api/acervo/meta")
def api_meta(corpo: MetaIn) -> dict:
    try:
        gravar_meta(ACERVO_DIR, corpo.arquivo, corpo.codigo, corpo.revisao, corpo.titulo, corpo.vigente)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    iniciar_indexacao()
    return estado_acervo()


@app.delete("/api/acervo")
def api_remover(corpo: ArquivoIn) -> dict:
    try:
        remover_arquivo(ACERVO_DIR, corpo.arquivo)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    iniciar_indexacao()
    return estado_acervo()


@app.get("/api/visao")
def api_visao() -> list[dict]:
    return visao.listar(DATA_DIR)


@app.put("/api/visao/{hash_figura}")
def api_revisar_visao(hash_figura: str, corpo: RevisaoVisaoIn) -> dict:
    try:
        return visao.revisar(DATA_DIR, hash_figura, corpo.texto)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro


@app.post("/api/indexar")
def api_indexar() -> dict:
    return iniciar_indexacao()


@app.get("/api/indexar")
def api_indexacao() -> dict:
    return estado_indexacao()


@app.get("/api/treino")
def api_treino() -> dict:
    return {**treino.estado(DATA_DIR), "preparacao": estado_indexacao()}


@app.post("/api/treino")
def api_treinar(corpo: ArquivoIn) -> dict:
    if corpo.arquivo not in {documento["arquivo"] for documento in estado_acervo()["documentos"]}:
        raise HTTPException(status_code=404, detail="Arquivo não está no acervo.")
    iniciar_indexacao(forcar={corpo.arquivo})
    return {**treino.estado(DATA_DIR), "preparacao": estado_indexacao()}


@app.get("/api/uso")
def api_uso() -> dict:
    return uso.ler_uso(DATA_DIR)


@app.post("/api/uso/zerar")
def api_uso_zerar() -> dict:
    uso.zerar(DATA_DIR)
    return uso.ler_uso(DATA_DIR)


@app.delete("/api/uso/zerar")
def api_uso_somar_tudo() -> dict:
    uso.somar_tudo(DATA_DIR)
    return uso.ler_uso(DATA_DIR)


@app.get("/api/uso/perguntas")
def api_uso_perguntas(canal: str = Query("operacional", pattern="^(operacional|ons)$")) -> dict:
    return {"perguntas": uso.perguntas(DATA_DIR, canal)}


@app.get("/api/uso/consultas")
def api_uso_consultas(
    limite: int = Query(10, ge=1, le=500),
    busca: str = Query("", max_length=200),
    nao_uteis: bool = False,
    documento: str = Query("", max_length=200),
) -> dict:
    return uso.consultas(DATA_DIR, limite, busca, nao_uteis, documento)


@app.get("/api/uso/ons")
def api_uso_ons() -> dict:
    return {"consultas": uso.medidas_ons(DATA_DIR)}


@app.post("/api/chat")
def api_chat(corpo: PerguntaIn) -> dict:
    try:
        turnos = [{"pergunta": item.pergunta, "resposta": item.resposta} for item in corpo.historico]
        return consultar(corpo.pergunta, historico=turnos)
    except (FileNotFoundError, ValueError) as erro:
        raise HTTPException(status_code=409, detail=str(erro)) from erro
    except RuntimeError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro


@app.post("/api/ons/chat")
def api_ons_chat(corpo: PerguntaIn) -> dict:
    turnos = [{"pergunta": item.pergunta, "resposta": item.resposta} for item in corpo.historico]
    try:
        return responder_ons(corpo.pergunta, historico=turnos)
    except ErroOns as erro:
        raise HTTPException(status_code=502, detail=str(erro)) from erro
    except (RuntimeError, ValueError) as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro


@app.get("/api/avaliacao")
def api_avaliacao() -> dict:
    return avaliacao.estado(DATA_DIR)


@app.get("/api/avaliacao/progresso")
def api_progresso() -> dict:
    return {"progresso": avaliacao.ler_progresso()}


@app.post("/api/avaliacao/gabarito")
def api_gabarito(arquivo: UploadFile = File(...)) -> dict:
    conteudo = arquivo.file.read(5 * 1024 * 1024 + 1)
    if len(conteudo) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Gabarito acima de 5 MB.")
    try:
        resultado = avaliacao.importar(DATA_DIR, conteudo)
    except (ValueError, UnicodeDecodeError) as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    resultado.pop("itens")
    return {**avaliacao.estado(DATA_DIR), "importacao": resultado}


@app.post("/api/avaliacao/rodar")
def api_rodar(corpo: RodarIn | None = None) -> dict:
    corpo = corpo or RodarIn()
    try:
        avaliacao.iniciar(DATA_DIR, ACERVO_DIR, modo=corpo.modo, reranker=corpo.reranker)
    except ValueError as erro:
        raise HTTPException(status_code=409, detail=str(erro)) from erro
    return avaliacao.estado(DATA_DIR)


@app.get("/api/avaliacao/revisao")
def api_revisao() -> dict:
    return avaliacao.revisao(DATA_DIR)


@app.put("/api/avaliacao/revisao")
def api_revisar(corpo: RevisaoIn) -> dict:
    try:
        return avaliacao.revisar(DATA_DIR, corpo.id, corpo.veredito, corpo.resposta_corrigida, corpo.observacao)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro


@app.delete("/api/avaliacao/revisao/{identificador}")
def api_desfazer_revisao(identificador: str) -> dict:
    return avaliacao.desfazer_revisao(DATA_DIR, identificador)


@app.post("/api/retorno")
def api_retorno(corpo: RetornoIn) -> dict:
    registrar_retorno(DATA_DIR, corpo.consulta, corpo.util, corpo.comentario)
    return {"ok": True}


@app.get("/api/avaliacao/rodadas/{identificador}")
def api_rodada(identificador: str) -> dict:
    try:
        return avaliacao.ler_rodada(DATA_DIR, identificador)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro


@app.get("/saude")
def saude() -> dict:
    indice = caminho_indice(DATA_DIR)
    return {
        "status": "ok",
        "indice": (indice / "trechos.json").exists(),
        "escutando": f"http://{HOST}:{PORT}",
    }


@app.post("/teams/outgoing")
async def outgoing(request: Request, background: BackgroundTasks):
    corpo = await request.body()
    autorizacao = request.headers.get("authorization", "")
    if not assinatura_valida(TEAMS_OUTGOING_HMAC_SECRET, corpo, autorizacao):
        logging.warning("Chamada do Teams recusada: assinatura HMAC ausente ou inválida.")
        return JSONResponse({"type": "message", "text": "Assinatura inválida."}, status_code=401)
    try:
        payload = json.loads(corpo.decode("utf-8"))
    except json.JSONDecodeError:
        return JSONResponse({"type": "message", "text": "Corpo inválido."}, status_code=400)

    atividade = str(payload.get("id") or "")
    if atividade and _repetida(atividade):
        return {"type": "message", "text": "Consulta já recebida."}

    pergunta = limpar_pergunta(str(payload.get("text") or ""))
    if not pergunta:
        return {"type": "message", "text": "Envie a pergunta junto com a menção."}

    autor = str((payload.get("from") or {}).get("name") or "desconhecido")
    background.add_task(processar_mencao, pergunta, autor)
    return {
        "type": "message",
        "text": "Consultando o normativo. A resposta entra neste canal em seguida.",
    }


def _repetida(atividade: str) -> bool:
    if atividade in _ATIVIDADES:
        return True
    _ATIVIDADES.append(atividade)
    return False


_DIST = RAIZ / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="web")
