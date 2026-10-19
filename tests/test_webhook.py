from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from contextlib import nullcontext

import httpx
from fastapi.testclient import TestClient

import app.server as servidor
from app.teams import assinatura_valida


class WebhookTeste(unittest.TestCase):
    def test_rejeita_sem_hmac_e_aceita_com_assinatura(self) -> None:
        segredo = base64.b64encode(b"chave-do-canal").decode("ascii")
        original = servidor.TEAMS_OUTGOING_HMAC_SECRET
        processo = servidor.processar_mencao
        chamadas: list[tuple[str, str]] = []
        servidor.TEAMS_OUTGOING_HMAC_SECRET = segredo
        servidor.processar_mencao = lambda pergunta, autor: chamadas.append((pergunta, autor))
        try:
            with nullcontext(TestClient(servidor.app)) as cliente:
                sem_assinatura = cliente.post("/teams/outgoing", json={"text": "qual o valor?"})
                self.assertEqual(sem_assinatura.status_code, 401)

                corpo = json.dumps(
                    {
                        "id": "atividade-1",
                        "text": "<at>Assistente COG</at> qual o valor de teste?",
                        "from": {"name": "Operador"},
                    }
                ).encode("utf-8")
                digest = hmac.new(b"chave-do-canal", corpo, hashlib.sha256).digest()
                assinatura = "HMAC " + base64.b64encode(digest).decode("ascii")
                self.assertTrue(assinatura_valida(segredo, corpo, assinatura))
                resposta = cliente.post(
                    "/teams/outgoing",
                    content=corpo,
                    headers={"Authorization": assinatura, "Content-Type": "application/json"},
                )
                self.assertEqual(resposta.status_code, 200)
                self.assertIn("Consultando o normativo", resposta.json()["text"])
                self.assertEqual(chamadas, [("qual o valor de teste?", "Operador")])

                repetida = cliente.post(
                    "/teams/outgoing",
                    content=corpo,
                    headers={"Authorization": assinatura, "Content-Type": "application/json"},
                )
                self.assertIn("já recebida", repetida.json()["text"])
                self.assertEqual(len(chamadas), 1)
        finally:
            servidor.TEAMS_OUTGOING_HMAC_SECRET = original
            servidor.processar_mencao = processo
            servidor._ATIVIDADES.clear()

    def test_falha_da_openai_vira_502(self) -> None:
        from openai import APIConnectionError

        def consultar(_pergunta: str, historico: list[dict]) -> dict:
            raise APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))

        original = servidor.consultar
        servidor.consultar = consultar
        try:
            with nullcontext(TestClient(servidor.app)) as cliente:
                resposta = cliente.post("/api/chat", json={"pergunta": "qual o valor?"})
            self.assertEqual(resposta.status_code, 502)
            self.assertIn("APIConnectionError", resposta.json()["detail"])
        finally:
            servidor.consultar = original

    def test_credito_esgotado_da_openai_diz_o_motivo(self) -> None:
        from openai import RateLimitError

        def consultar(_pergunta: str, historico: list[dict]) -> dict:
            pedido = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            corpo = {"message": "You have no credits remaining.", "type": "insufficient_quota", "code": "credit_balance_exhausted"}
            raise RateLimitError("sem crédito", response=httpx.Response(429, request=pedido), body=corpo)

        original = servidor.consultar
        servidor.consultar = consultar
        try:
            with nullcontext(TestClient(servidor.app)) as cliente:
                resposta = cliente.post("/api/chat", json={"pergunta": "qual o valor?"})
            self.assertEqual(resposta.status_code, 502)
            self.assertIn("créditos da conta da OpenAI acabaram", resposta.json()["detail"])
        finally:
            servidor.consultar = original


if __name__ == "__main__":
    unittest.main()
