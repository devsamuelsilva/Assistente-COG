from __future__ import annotations

import json
import logging
from collections import deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import avaliacao
from app.config import ACERVO_DIR, DATA_DIR, HOST, PORT, RAIZ, TEAMS_OUTGOING_HMAC_SECRET
from app.indice import aquecer_modelo, caminho_indice, indexar
from app.painel import consultar, estado_acervo, gravar_meta, remover_arquivo, salvar_envio
from app.uso import ler_uso
from app.teams import assinatura_valida, limpar_pergunta, processar_mencao

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_ATIVIDADES: deque[str] = deque(maxlen=200)


@asynccontextmanager
async def _vida(app: FastAPI):
    try:
        aquecer_modelo()
    except Exception:
        logging.exception("Não foi possível carregar o modelo local de embeddings.")
    yield


app = FastAPI(title="Assistente do COG", lifespan=_vida)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TurnoIn(BaseModel):
    pergunta: str = ""
    resposta: str = ""


class PerguntaIn(BaseModel):
    pergunta: str = Field(min_length=1, max_length=4000)
    historico: list[TurnoIn] = Field(default_factory=list, max_length=8)


class MetaIn(BaseModel):
    arquivo: str
    codigo: str
    revisao: str = ""
    titulo: str
    vigente: bool = True


class ArquivoIn(BaseModel):
    arquivo: str


@app.get("/api/acervo")
def api_acervo() -> dict:
    return estado_acervo()


@app.post("/api/acervo")
async def api_enviar(arquivo: UploadFile = File(...)) -> dict:
    conteudo = await arquivo.read()
    try:
        salvar_envio(ACERVO_DIR, arquivo.filename or "", conteudo)
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return estado_acervo()


@app.put("/api/acervo/meta")
def api_meta(corpo: MetaIn) -> dict:
    try:
        gravar_meta(ACERVO_DIR, corpo.arquivo, corpo.codigo, corpo.revisao, corpo.titulo, corpo.vigente)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return estado_acervo()


@app.delete("/api/acervo")
def api_remover(corpo: ArquivoIn) -> dict:
    try:
        remover_arquivo(ACERVO_DIR, corpo.arquivo)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=404, detail=str(erro)) from erro
    except ValueError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return estado_acervo()


@app.post("/api/indexar")
def api_indexar() -> dict:
    try:
        indexar(ACERVO_DIR, DATA_DIR)
    except (FileNotFoundError, ValueError) as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return estado_acervo()


@app.get("/api/uso")
def api_uso() -> dict:
    return ler_uso(DATA_DIR)


@app.post("/api/chat")
def api_chat(corpo: PerguntaIn) -> dict:
    try:
        turnos = [{"pergunta": item.pergunta, "resposta": item.resposta} for item in corpo.historico]
        return consultar(corpo.pergunta, historico=turnos)
    except FileNotFoundError as erro:
        raise HTTPException(status_code=409, detail=str(erro)) from erro
    except RuntimeError as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro


@app.get("/api/avaliacao")
def api_avaliacao() -> dict:
    return avaliacao.estado(DATA_DIR)


@app.post("/api/avaliacao/gabarito")
async def api_gabarito(arquivo: UploadFile = File(...)) -> dict:
    conteudo = await arquivo.read()
    if len(conteudo) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Gabarito acima de 5 MB.")
    try:
        avaliacao.importar(DATA_DIR, conteudo)
    except (ValueError, UnicodeDecodeError) as erro:
        raise HTTPException(status_code=400, detail=str(erro)) from erro
    return avaliacao.estado(DATA_DIR)


@app.post("/api/avaliacao/rodar")
def api_rodar() -> dict:
    try:
        avaliacao.iniciar(DATA_DIR, ACERVO_DIR)
    except ValueError as erro:
        raise HTTPException(status_code=409, detail=str(erro)) from erro
    return avaliacao.estado(DATA_DIR)


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
