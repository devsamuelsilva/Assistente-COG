from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.server as server
from app import painel
from app.indice import indexar
from tests.test_rag import _embedder

RAIZ = Path(__file__).resolve().parents[1]


class PercentualTeste(unittest.TestCase):
    def test_etapas_principais_ocupam_faixas_que_se_encaixam(self) -> None:
        faixas = sorted(painel._ETAPAS.values())
        self.assertEqual(faixas[0][0], 0)
        self.assertEqual(faixas[-1][1], 100)
        for anterior, seguinte in zip(faixas, faixas[1:]):
            self.assertEqual(anterior[1], seguinte[0])

    def test_subetapas_da_indexacao_cobrem_a_janela_toda_sem_sobrepor(self) -> None:
        fatias = sorted(painel._SUBETAPAS.values())
        self.assertEqual(fatias[0][0], 0.0)
        self.assertEqual(fatias[-1][1], 1.0)
        for anterior, seguinte in zip(fatias, fatias[1:]):
            self.assertAlmostEqual(anterior[1], seguinte[0])

    def test_percentual_dentro_da_janela(self) -> None:
        self.assertEqual(painel._percentual("indexando", 0, 0, [0, 50]), 0)
        self.assertAlmostEqual(painel._percentual("vetores", 0, 10, [0, 50]), 37.5)
        self.assertAlmostEqual(painel._percentual("vetores", 10, 10, [0, 50]), 50)
        # a segunda indexação (com as perguntas aprendidas) usa a janela dela
        self.assertAlmostEqual(painel._percentual("vetores", 5, 10, [80, 88]), 80 + 8 * 0.875)
        self.assertEqual(painel._percentual("perguntas de treino", 1, 4, [50, 80]), 57.5)
        # total zero ou feitos além do total não estouram a faixa
        self.assertEqual(painel._percentual("amostra com resposta completa", 9, 3, [93, 100]), 100)

    def test_a_barra_nunca_anda_para_tras_e_termina_em_100(self) -> None:
        passos: list[float] = []

        def preparador(data_dir, acervo, forcar=None, avisos=None, progresso=None):
            for etapa, feitos, total in [
                ("indexando", 0, 0),
                ("vetores", 8, 10),
                ("vetores", 2, 10),  # volta: segunda leva de vetores (das perguntas)
                ("perguntas de treino", 0, 2),
                ("perguntas de treino", 1, 2),
            ]:
                progresso(etapa, feitos, total)
                passos.append(painel.estado_indexacao()["percentual"])

        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            (raiz / "acervo").mkdir()
            painel.iniciar_indexacao(raiz / "acervo", raiz / "data", preparador=preparador)
            for _ in range(200):
                if not painel.estado_indexacao()["rodando"]:
                    break
                import time

                time.sleep(0.02)
        self.assertEqual(passos, sorted(passos))
        self.assertEqual(painel.estado_indexacao()["percentual"], 100.0)
        self.assertFalse(painel.estado_indexacao()["rodando"])


class IndexarAvisaTeste(unittest.TestCase):
    def test_indexar_informa_as_etapas_com_contagem(self) -> None:
        eventos: list[tuple[str, int, int]] = []
        with tempfile.TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            indexar(
                acervo,
                raiz / "data",
                embedder=_embedder,
                perguntador=lambda texto: '{"perguntas": ["pergunta de teste?"]}',
                redator=lambda texto: '{"titulo": "t", "objetivo": "o", "pontos": []}',
                progresso=lambda etapa, feitos=0, total=0: eventos.append((etapa, feitos, total)),
            )
        etapas = [etapa for etapa, _, _ in eventos]
        self.assertIn("lendo os documentos", etapas)
        self.assertIn("perguntas de busca", etapas)
        busca = [(f, t) for etapa, f, t in eventos if etapa == "perguntas de busca"]
        self.assertEqual(busca[-1][0], busca[-1][1])  # a última contagem fecha o total
        self.assertTrue(set(etapas) <= set(painel._SUBETAPAS))


class EnvioTeste(unittest.TestCase):
    def test_anexar_so_guarda_e_a_indexacao_espera_a_confirmacao_dos_dados(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            conteudo = (RAIZ / "tests" / "dados" / "exemplo-normativo.md").read_bytes()
            with (
                patch.object(server, "ACERVO_DIR", pasta),
                patch.object(server, "iniciar_indexacao") as iniciar,
                patch.object(server, "estado_acervo", return_value={"documentos": [], "indice": {}}),
            ):
                cliente = TestClient(server.app, client=("127.0.0.1", 50000))
                resposta = cliente.post("/api/acervo", files={"arquivo": ("it-novo.md", conteudo)})
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.json()["enviado"], "it-novo.md")
                self.assertTrue((pasta / "it-novo.md").exists())
                iniciar.assert_not_called()
                # confirmar os dados é o que dispara o processamento
                with patch.object(server, "gravar_meta") as gravar:
                    cliente.put(
                        "/api/acervo/meta",
                        json={"arquivo": "it-novo.md", "codigo": "IT-9", "revisao": "0", "titulo": "Novo", "vigente": True},
                    )
                    gravar.assert_called_once()
                iniciar.assert_called_once()


class AcessoPelaRedeTeste(unittest.TestCase):
    def test_pela_rede_so_o_chat_responde(self) -> None:
        indice = {"existe": True, "desatualizado": False, "trechos": 3, "vigentes": 3}
        with (
            patch.object(server, "estado_indice", return_value=indice),
            patch.object(server, "estado_acervo", return_value={"documentos": [], "indice": indice}),
        ):
            rede = TestClient(server.app, client=("10.85.11.70", 50000))
            inicio = rede.get("/api/inicio")
            self.assertEqual(inicio.status_code, 200)
            self.assertEqual(inicio.json(), {"indice": indice, "administracao": False})
            self.assertEqual(rede.get("/api/acervo").status_code, 403)
            self.assertEqual(rede.request("DELETE", "/api/acervo", json={"arquivo": "a.pdf"}).status_code, 403)
            self.assertEqual(rede.post("/api/indexar").status_code, 403)
            self.assertEqual(rede.get("/api/uso").status_code, 403)
            self.assertEqual(rede.post("/api/chat", json={"pergunta": ""}).status_code, 422)

            local = TestClient(server.app, client=("127.0.0.1", 50000))
            self.assertTrue(local.get("/api/inicio").json()["administracao"])
            self.assertEqual(local.get("/api/acervo").status_code, 200)


if __name__ == "__main__":
    unittest.main()
