from __future__ import annotations

import base64
import hashlib
import hmac
import unittest

from app.teams import assinatura_valida, cartao, limpar_pergunta


class TeamsTeste(unittest.TestCase):
    def test_hmac_confere_com_o_corpo(self) -> None:
        segredo = base64.b64encode(b"chave-do-canal").decode("ascii")
        corpo = b'{"text":"qual o valor?"}'
        digest = hmac.new(b"chave-do-canal", corpo, hashlib.sha256).digest()
        assinatura = "HMAC " + base64.b64encode(digest).decode("ascii")
        self.assertTrue(assinatura_valida(segredo, corpo, assinatura))
        self.assertFalse(assinatura_valida(segredo, corpo, "HMAC errada"))
        self.assertFalse(assinatura_valida("", corpo, assinatura))

    def test_remove_mencao_do_teams(self) -> None:
        texto = "<at>Assistente COG</at> qual o valor de teste?"
        self.assertEqual(limpar_pergunta(texto), "qual o valor de teste?")

    def test_cartao_adaptativo_e_messagecard(self) -> None:
        adaptativo = cartao("resposta", "adaptive")
        self.assertEqual(adaptativo["attachments"][0]["content"]["body"][1]["text"], "resposta")
        self.assertEqual(cartao("resposta", "texto"), {"text": "resposta"})
        self.assertEqual(cartao("resposta", "messagecard")["text"], "resposta")


if __name__ == "__main__":
    unittest.main()
