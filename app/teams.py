from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import re
import urllib.error
import urllib.request

from app.config import AVISO_OPERADOR, TEAMS_INCOMING_WEBHOOK_URL, TEAMS_POST_FORMAT
from app.rag import formatar, responder

_MENCAO = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def assinatura_valida(segredo: str, corpo: bytes, autorizacao: str) -> bool:
    if not segredo or not autorizacao:
        return False
    partes = autorizacao.strip().split(" ", 1)
    if len(partes) != 2 or partes[0] != "HMAC":
        return False
    fornecida = partes[1].strip()
    try:
        chave = base64.b64decode(segredo + "=" * (-len(segredo) % 4))
    except Exception:
        return False
    if not chave:
        return False
    digest = hmac.new(chave, corpo, hashlib.sha256).digest()
    esperada = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(esperada, fornecida)


def limpar_pergunta(texto: str) -> str:
    sem_mencao = _MENCAO.sub(" ", texto or "")
    sem_tag = _TAG.sub(" ", sem_mencao)
    return " ".join(html.unescape(sem_tag).split())


def processar_mencao(pergunta: str, autor: str) -> None:
    try:
        resultado = responder(pergunta, autor=autor, origem="teams")
        texto = formatar(resultado, autor=autor)
    except RuntimeError as erro:
        logging.error("%s", erro)
        texto = f"{erro}\n\n{AVISO_OPERADOR}"
    except Exception:
        logging.exception("Falha ao consultar o normativo")
        texto = f"Não consegui consultar o normativo agora. Tente de novo em instantes.\n\n{AVISO_OPERADOR}"
    if not TEAMS_INCOMING_WEBHOOK_URL:
        logging.warning("TEAMS_INCOMING_WEBHOOK_URL vazio. A resposta ficou só no log local.")
        return
    try:
        publicar(TEAMS_INCOMING_WEBHOOK_URL, texto, TEAMS_POST_FORMAT)
    except Exception:
        logging.exception("Falha ao publicar a resposta no canal do Teams")


def publicar(url: str, texto: str, formato: str = "adaptive") -> int:
    corpo = json.dumps(cartao(texto, formato), ensure_ascii=False).encode("utf-8")
    pedido = urllib.request.Request(
        url,
        data=corpo,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(pedido, timeout=30) as resposta:
            return int(resposta.status)
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"O Teams recusou a publicação ({erro.code}). {detalhe}") from erro


def cartao(texto: str, formato: str) -> dict:
    if formato == "messagecard":
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": "Assistente do COG",
            "themeColor": "0078D4",
            "title": "Assistente do COG",
            "text": texto,
        }
    if formato == "texto":
        return {"text": texto}
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": "Assistente do COG", "weight": "Bolder", "wrap": True},
                        {"type": "TextBlock", "text": texto, "wrap": True},
                    ],
                },
            }
        ],
    }
