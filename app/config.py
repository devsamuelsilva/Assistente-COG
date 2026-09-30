from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")


def _caminho(nome: str, padrao: str) -> Path:
    bruto = os.getenv(nome, padrao).strip()
    caminho = Path(bruto)
    if not caminho.is_absolute():
        caminho = RAIZ / caminho
    return caminho


def _inteiro(nome: str, padrao: int) -> int:
    bruto = os.getenv(nome, str(padrao)).strip()
    try:
        return int(bruto)
    except ValueError:
        return padrao


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() or None
# String vazia no ambiente não é "ausente": o SDK usa esse valor e deixa a URL sem protocolo.
if OPENAI_BASE_URL is None:
    os.environ.pop("OPENAI_BASE_URL", None)
TEAMS_OUTGOING_HMAC_SECRET = os.getenv("TEAMS_OUTGOING_HMAC_SECRET", "").strip()
TEAMS_INCOMING_WEBHOOK_URL = os.getenv("TEAMS_INCOMING_WEBHOOK_URL", "").strip()
TEAMS_POST_FORMAT = os.getenv("TEAMS_POST_FORMAT", "adaptive").strip().lower() or "adaptive"
ACERVO_DIR = _caminho("ACERVO_DIR", "acervo")
DATA_DIR = _caminho("DATA_DIR", "data")
HOST = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _inteiro("PORT", 8080)
EMBED_MODEL = (
    os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").strip()
    or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)
TOP_K = _inteiro("TOP_K", 6)
os.environ.setdefault("FASTEMBED_CACHE_PATH", str(DATA_DIR / "modelos"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
AVISO_OPERADOR = (
    "Apoio à consulta. A execução continua sob a responsabilidade do operador "
    "e do normativo oficial."
)
