from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.buscar import Acerto
from app.chunking import Trecho
from app.gpt import Saida
from app.indice import indexar
from app.rag import Resultado, _segunda_tentativa, responder
from tests.test_rag import _embedder


class PlanejadorTeste(unittest.TestCase):
    """A causa dos "não sei" com a resposta no documento: a pergunta e o normativo usam palavras diferentes."""

    def _montar(self, raiz: Path, documento: str):
        acervo = raiz / "acervo"
        acervo.mkdir()
        (acervo / "it-geral.md").write_text(documento, encoding="utf-8")
        data = raiz / "data"
        return acervo, data, indexar(acervo, data, embedder=_embedder)

    def _gerador(self, vistos: list[str]):
        def gerador(mensagens):
            vistos.append(mensagens[1]["content"])
            dados = {"status": "ausente", "resposta": "Não consta.", "citacoes": []}
            return json.dumps(dados), 5, 2, 0, "stop", None

        return gerador

    def test_plano_vai_para_a_busca_fica_guardado_e_conta_no_custo(self) -> None:
        documento = "# IT-GERAL-001\n\nCódigo: IT-GERAL-001\nRevisão: 0\n\n## 1. Objetivo\n\nPadronizar a comunicação.\n"
        chamadas: list[str] = []

        def planejador(mensagens):
            chamadas.append(mensagens[1]["content"])
            plano = {"pergunta": "qual o objetivo da IT-GERAL-001?", "tipo": "geral", "consultas": ["objetivo do normativo", " "]}
            return json.dumps(plano), 20, 8

        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario), documento)
            args = dict(data_dir=data, acervo=acervo, indice=indice, embedder=_embedder, reescritor=planejador)
            primeira = responder("qual a finalidade da IT-GERAL-001?", gerador=self._gerador([]), **args)
            self.assertEqual(len(chamadas), 1)
            self.assertEqual((primeira.tokens_aux_prompt, primeira.tokens_aux_resposta), (20, 8))
            # sem conversa, a pergunta original é a que vale; o plano só soma consultas e o tipo
            self.assertEqual(primeira.pergunta_busca, "")
            responder("qual a finalidade da IT-GERAL-001?", gerador=self._gerador([]), **args)
            self.assertEqual(len(chamadas), 1)

    def test_tipo_geral_do_plano_traz_a_abertura_mesmo_sem_a_palavra_chave(self) -> None:
        documento = (
            "# IT-GERAL-002\n\nCódigo: IT-GERAL-002\nRevisão: 0\n\n## 1. Objetivo\n\nPadronizar a comunicação.\n\n"
            "## 2. Glossário\n\nSiglas e termos usados na IT-GERAL-002 para a comunicação.\n"
        )
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario), documento)
            plano = {"pergunta": "x", "tipo": "geral", "consultas": []}
            vistos: list[str] = []
            responder(
                "pra que serve a IT-GERAL-002?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador(vistos),
                embedder=_embedder,
                reescritor=lambda _m: (json.dumps(plano), 1, 1),
            )
            self.assertIn("1. Objetivo", vistos[0])

    def test_plano_com_defeito_nao_derruba_a_consulta(self) -> None:
        documento = "# IT-GERAL-004\n\nCódigo: IT-GERAL-004\nRevisão: 0\n\n## 1. Objetivo\n\nPadronizar a comunicação.\n"
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario), documento)
            for defeito in (lambda _m: ("não é json", 1, 1), lambda _m: ("[1, 2]", 1, 1), lambda _m: (_ for _ in ()).throw(RuntimeError("fora do ar"))):
                resultado = responder(
                    f"qual o objetivo? {id(defeito)}",
                    data_dir=data,
                    acervo=acervo,
                    indice=indice,
                    gerador=self._gerador([]),
                    embedder=_embedder,
                    reescritor=defeito,
                )
                self.assertEqual(resultado.status, "ausente")

    def test_segunda_resposta_ausente_mantem_a_primeira_e_soma_o_custo(self) -> None:
        trecho_novo = Trecho("2", "Seção: 2 Outro\nmais texto", "a.pdf", "C", "0", True, "t", "2 Outro", "1")
        trecho_antigo = Trecho("1", "Seção: 1 Prazos\ntexto", "a.pdf", "C", "0", True, "t", "1 Prazos", "1")
        primeiro = Acerto(trecho=trecho_antigo, coseno=1, bm25=1, rrf=1)
        chamadas: list[int] = []

        def gerador(_mensagens):
            chamadas.append(1)
            return json.dumps({"status": "ausente", "resposta": "ainda não", "citacoes": []}), 30, 3, 0, "stop", None

        resultado = Resultado(
            pergunta="p", resposta="Esse ponto não está", coberto=False, citacoes=[], tokens_prompt=10, tokens_resposta=1, status="ausente"
        )
        contexto = ([primeiro, Acerto(trecho=trecho_novo, coseno=1, bm25=1, rrf=1)], set())
        with patch("app.rag.buscar_contexto", return_value=contexto):
            final = _segunda_tentativa(resultado, Saida("{}", 10, 1), "p", [], None, None, None, "", {}, [primeiro], "", gerador)
        self.assertEqual(len(chamadas), 1)
        self.assertEqual(final.status, "ausente")
        self.assertFalse(final.ampliado)
        self.assertEqual((final.tokens_prompt, final.tokens_resposta), (40, 4))

    def test_segunda_resposta_com_fonte_substitui_a_primeira(self) -> None:
        trecho_novo = Trecho("2", "Seção: 2 Prazos\nO prazo é de 5 dias.", "a.pdf", "C", "0", True, "t", "2 Prazos", "1")
        trecho_antigo = Trecho("1", "Seção: 1 Geral\ntexto", "a.pdf", "C", "0", True, "t", "1 Geral", "1")
        primeiro = Acerto(trecho=trecho_antigo, coseno=1, bm25=1, rrf=1)

        def gerador(_mensagens):
            dados = {"status": "coberto", "resposta": "O prazo é de 5 dias.", "citacoes": [{"trecho": 2}]}
            return json.dumps(dados), 30, 3, 0, "stop", None

        resultado = Resultado(pergunta="p", resposta="Esse ponto não está", coberto=False, citacoes=[], tokens_prompt=10, tokens_resposta=1, status="ausente")
        contexto = ([primeiro, Acerto(trecho=trecho_novo, coseno=1, bm25=1, rrf=1)], set())
        with patch("app.rag.buscar_contexto", return_value=contexto):
            final = _segunda_tentativa(resultado, Saida("{}", 10, 1), "p", [], None, None, None, "", {}, [primeiro], "", gerador)
        self.assertEqual(final.status, "coberto")
        self.assertTrue(final.ampliado)
        self.assertEqual(final.citacoes[0]["secao"], "2 Prazos")

    def test_sem_texto_novo_nao_chama_de_novo(self) -> None:
        trecho = Trecho("1", "Seção: 1 Geral\ntexto", "a.pdf", "C", "0", True, "t", "1 Geral", "1")
        acerto = Acerto(trecho=trecho, coseno=1, bm25=1, rrf=1)
        resultado = Resultado(pergunta="p", resposta="x", coberto=False, citacoes=[], status="ausente")
        with patch("app.rag.buscar_contexto", return_value=([acerto], set())):
            final = _segunda_tentativa(
                resultado, Saida("{}", 1, 1), "p", [], None, None, None, "", {}, [acerto], "",
                lambda _m: (_ for _ in ()).throw(AssertionError("nao deveria chamar")),
            )
        self.assertIs(final, resultado)


class TreinoGeralTeste(unittest.TestCase):
    def test_treino_cria_e_confere_perguntas_gerais_do_documento(self) -> None:
        from app import treino

        documento = (
            "# IT-TREINO-009 — Instrução\n\nCódigo: IT-TREINO-009\nRevisão: 0\n\n## 1. Objetivo\n\n"
            + ("Padronizar a comunicação operacional entre as equipes do centro. " * 6)
            + "\n\n## 2. Procedimento\n\n"
            + ("O operador registra o horário e comunica o centro regional. " * 6)
            + "\n"
        )

        def gerar(sistema: str, texto: str):
            if sistema == treino._PEDIDO_GERAL:
                perguntas = [{"pergunta": "para que serve?", "resposta": "Padronizar a comunicação."}]
                return json.dumps({"perguntas": perguntas}), 10, 5
            if sistema == treino._PEDIDO_RECUSA:
                return json.dumps({"perguntas": ["qual a cor da torre?"]}), 10, 5
            return json.dumps({"perguntas": []}), 10, 5

        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            (acervo / "it-treino.md").write_text(documento, encoding="utf-8")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            dados = treino.treinar_documento(data, indice, "it-treino.md", gerador=gerar, embedder=_embedder)
            gerais = [item for item in dados["perguntas"] if item["categoria"] == "geral"]
            self.assertEqual(len(gerais), 1)
            self.assertTrue(gerais[0]["pergunta"].startswith("IT-TREINO-009 - "))
            self.assertIn("1. Objetivo", gerais[0]["secoes_ok"])
            self.assertTrue(gerais[0]["busca_ok"])


if __name__ == "__main__":
    unittest.main()
