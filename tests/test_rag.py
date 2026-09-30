from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path

import numpy as np

from app.indice import carregar, indexar
from app.rag import formatar, responder

RAIZ = Path(__file__).resolve().parents[1]


def _vetor(texto: str) -> np.ndarray:
    digest = hashlib.sha256(texto.casefold().encode("utf-8")).digest()
    base = np.array([b / 255 for b in digest[:8]], dtype=np.float32)
    if "42" in texto or "valor de teste" in texto.casefold():
        return np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    return base


def _embedder(textos: list[str]) -> list[np.ndarray]:
    return [_vetor(texto) for texto in textos]


class RagTeste(unittest.TestCase):
    def test_resposta_cita_secao_e_recusa_citacao_inventada(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "acervo" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            self.assertGreaterEqual(len(indice.trechos), 3)
            recarregado = carregar(data)
            self.assertEqual(len(recarregado.trechos), len(indice.trechos))

            def gerador(mensagens: list[dict]) -> tuple[str, int, int]:
                pedido = mensagens[1]["content"]
                self.assertIn("42 unidades de exemplo", pedido)
                self.assertNotIn("OPENAI", pedido)
                return (
                    json.dumps(
                        {
                            "coberto": True,
                            "resposta": "O valor de teste é 42 unidades de exemplo.",
                            "citacoes": [
                                {
                                    "arquivo": "exemplo-normativo.md",
                                    "codigo": "IT-EXEMPLO-001",
                                    "revisao": "0",
                                    "secao": "2. Valor de teste",
                                },
                                {"arquivo": "outro.md", "secao": "9. Inventada"},
                            ],
                        }
                    ),
                    11,
                    7,
                )

            resultado = responder(
                "qual o valor de teste?",
                autor="operador",
                origem="teste",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=gerador,
                embedder=_embedder,
            )
            self.assertTrue(resultado.coberto)
            self.assertEqual(len(resultado.citacoes), 1)
            self.assertEqual(resultado.citacoes[0]["secao"], "2. Valor de teste")
            texto = formatar(resultado, autor="operador")
            self.assertIn("42 unidades de exemplo", texto)
            self.assertIn("IT-EXEMPLO-001, revisão 0, seção 2. Valor de teste", texto)
            self.assertIn("responsabilidade do operador", texto)
            self.assertNotIn("Inventada", texto)

            segunda = responder(
                "qual o valor de teste?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=lambda _mensagens: (_ for _ in ()).throw(AssertionError("cache nao usado")),
                embedder=_embedder,
            )
            self.assertTrue(segunda.cache)
            self.assertEqual(segunda.tokens_prompt, 11)

    def test_complemento_usa_a_conversa_e_nao_repete_o_cache(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "acervo" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            vistos: list[str] = []

            def gerador(mensagens: list[dict]) -> tuple[str, int, int]:
                vistos.append(mensagens[1]["content"])
                return json.dumps({"coberto": False, "resposta": "parcial", "citacoes": []}), 4, 2

            historico = [{"pergunta": "qual o valor de teste?", "resposta": "42 unidades de exemplo."}]
            responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=gerador,
                embedder=_embedder,
                historico=historico,
            )
            self.assertEqual(len(vistos), 1)
            self.assertIn("Conversa deste chat:", vistos[0])
            self.assertIn("qual o valor de teste?", vistos[0])
            self.assertIn("Pergunta atual:\ne na revisão seguinte?", vistos[0])

            repetida = responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=lambda _mensagens: (_ for _ in ()).throw(AssertionError("cache nao usado")),
                embedder=_embedder,
                historico=historico,
            )
            self.assertTrue(repetida.cache)

            def gerador_novo(mensagens: list[dict]) -> tuple[str, int, int]:
                self.assertNotIn("qual o valor de teste?", mensagens[1]["content"])
                return json.dumps({"coberto": False, "resposta": "sozinha", "citacoes": []}), 4, 2

            isolada = responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=gerador_novo,
                embedder=_embedder,
            )
            self.assertFalse(isolada.cache)
            self.assertEqual(isolada.resposta, "Esse ponto não está no normativo indexado neste PC.")


if __name__ == "__main__":
    unittest.main()
