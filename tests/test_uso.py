from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.registro import registrar, registrar_retorno
from app.uso import consultas, itens, ler_uso, medidas_ons, migrar_logs, perguntas


class UsoTeste(unittest.TestCase):
    def test_cache_nao_entra_no_custo_e_a_lista_vem_da_mais_nova(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            logs = pasta / "logs"
            logs.mkdir()
            linhas = [
                {
                    "quando": "2026-09-29T11:00:00",
                    "pergunta": "primeira",
                    "resposta": "sim",
                    "tokens_prompt": 1000,
                    "tokens_resposta": 100,
                    "cache": False,
                    "modelo": "gpt-4.1-mini",
                    "origem": "web",
                    "coberto": True,
                },
                {
                    "quando": "2026-09-29T11:01:00",
                    "pergunta": "primeira",
                    "resposta": "sim",
                    "tokens_prompt": 1000,
                    "tokens_resposta": 100,
                    "cache": True,
                    "modelo": "gpt-4.1-mini",
                    "origem": "web",
                    "coberto": True,
                },
                {
                    "quando": "2026-09-29T11:02:00",
                    "pergunta": "outra",
                    "resposta": "nao",
                    "tokens_prompt": 50,
                    "tokens_resposta": 10,
                    "cache": False,
                    "modelo": "modelo-desconhecido",
                    "origem": "terminal",
                    "coberto": False,
                },
            ]
            (logs / "consultas.jsonl").write_text(
                "\n".join(json.dumps(linha) for linha in linhas) + "\nlinha-ruim\n",
                encoding="utf-8",
            )
            uso = ler_uso(pasta)
            lista = itens(pasta)
            resumo = uso["resumo"]
            self.assertEqual(resumo["consultas"], 3)
            self.assertEqual(resumo["cobradas"], 1)
            self.assertEqual(resumo["cache"], 1)
            self.assertEqual(resumo["sem_tabela"], 1)
            self.assertEqual(resumo["tokens_entrada"], 1000)
            self.assertEqual(resumo["tokens_saida"], 100)
            self.assertAlmostEqual(resumo["custo_usd"], (1000 * 0.40 + 100 * 1.60) / 1_000_000)
            self.assertEqual(lista[0]["quando"], "2026-09-29T11:02:00")
            self.assertIsNone(lista[0]["custo_usd"])
            self.assertEqual(lista[1]["custo_usd"], 0.0)

    def test_entrada_reaproveitada_pela_openai_custa_menos(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            registrar(
                pasta,
                {
                    "pergunta": "carga?",
                    "resposta": "x",
                    "origem": "ons",
                    "tokens_prompt": 10_000,
                    "tokens_cache": 8_000,
                    "tokens_resposta": 100,
                    "cache": False,
                    "modelo": "gpt-4.1-mini",
                    "duracao_ms": 12_345,
                    "passos": 3,
                },
            )
            uso = ler_uso(pasta)
            lista = itens(pasta)
        item = lista[0]
        self.assertAlmostEqual(item["custo_usd"], (2_000 * 0.40 + 8_000 * 0.10 + 100 * 1.60) / 1_000_000)
        self.assertEqual((item["duracao_ms"], item["passos"]), (12_345, 3))
        self.assertEqual(uso["resumo"]["tokens_reaproveitados"], 8_000)

    def test_continuacao_gravada_vale_e_nas_antigas_conta_a_janela_de_10_minutos(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            logs = pasta / "logs"
            logs.mkdir()
            linhas = [
                {"quando": "2026-09-30T10:00:00", "autor": "web", "origem": "ons", "pergunta": "abre ONS"},
                {"quando": "2026-09-30T10:02:00", "autor": "web", "origem": "web", "pergunta": "abre operacional"},
                {"quando": "2026-09-30T10:03:00", "autor": "web", "origem": "avaliacao", "pergunta": "rodada"},
                {"quando": "2026-09-30T10:05:00", "autor": "web", "origem": "ons", "pergunta": "e de Papagaios?"},
                {"quando": "2026-09-30T10:07:00", "autor": "web", "origem": "ons", "pergunta": "Qual o corte por região?"},
                {"quando": "2026-09-30T10:30:00", "autor": "web", "origem": "ons", "pergunta": "quanto disso em Papagaios"},
                {"quando": "2026-09-30T10:31:00", "autor": "web", "origem": "ons", "pergunta": "gravada", "continuacao": False},
                {"quando": "2026-09-30T11:30:00", "autor": "web", "origem": "web", "pergunta": "seguinte", "continuacao": True},
            ]
            (logs / "consultas.jsonl").write_text("\n".join(json.dumps(linha) for linha in linhas), encoding="utf-8")
            lista = itens(pasta)
        por_pergunta = {item["pergunta"]: item["continuacao"] for item in lista}
        self.assertEqual(
            por_pergunta,
            {
                "abre ONS": False,
                "abre operacional": False,
                "rodada": False,
                "e de Papagaios?": True,
                "Qual o corte por região?": False,
                "quanto disso em Papagaios": False,
                "gravada": False,
                "seguinte": True,
            },
        )

    def test_retorno_do_operador_fica_na_consulta_e_o_ultimo_vale(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            util = registrar(pasta, {"pergunta": "prazo?", "resposta": "15 dias", "origem": "web"})
            ruim = registrar(pasta, {"pergunta": "quem?", "resposta": "o COG", "origem": "web"})
            registrar(pasta, {"pergunta": "sem voto", "resposta": "x", "origem": "web"})
            registrar_retorno(pasta, util, True)
            registrar_retorno(pasta, ruim, True)
            registrar_retorno(pasta, ruim, False, "  Faltou   o item 18. ")
            uso = ler_uso(pasta)
            lista = itens(pasta)
            por_id = {item["id"]: item for item in lista}
            self.assertTrue(por_id[util]["retorno"]["util"])
            self.assertEqual(por_id[ruim]["retorno"], {**por_id[ruim]["retorno"], "util": False, "comentario": "Faltou o item 18."})
            self.assertEqual(sum(1 for item in lista if item["retorno"] is None), 1)
            self.assertEqual((uso["resumo"]["uteis"], uso["resumo"]["nao_uteis"]), (1, 1))

    def test_avaliacao_vai_para_arquivo_proprio_e_a_tela_separa_por_origem(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            base = {"resposta": "x", "tokens_prompt": 1000, "tokens_resposta": 0, "modelo": "gpt-4.1-mini", "coberto": True}
            registrar(pasta, {**base, "pergunta": "prazo do relatório?", "origem": "web", "citacoes": [{"codigo": "RO-1"}]})
            registrar(pasta, {**base, "pergunta": "carga do nordeste?", "origem": "ons"})
            registrar(pasta, {**base, "pergunta": "pergunta do gabarito", "origem": "avaliacao"})
            uso = ler_uso(pasta)
            consultas_log = (pasta / "logs" / "consultas.jsonl").read_text(encoding="utf-8")
            avaliacao_log = (pasta / "logs" / "avaliacao.jsonl").read_text(encoding="utf-8")
            nuvem = perguntas(pasta, "operacional")
            nuvem_ons = perguntas(pasta, "ons")
            historico = consultas(pasta)
            ons = medidas_ons(pasta)

        self.assertNotIn("gabarito", consultas_log)
        self.assertIn("gabarito", avaliacao_log)
        self.assertEqual(uso["resumo"]["consultas"], 3)
        self.assertEqual({origem["origem"]: origem["consultas"] for origem in uso["origens"]}, {"normativo": 1, "ons": 1, "avaliacao": 1})
        self.assertAlmostEqual(uso["dias"][0]["custo"], 3 * 1000 * 0.40 / 1_000_000)
        self.assertAlmostEqual({doc["codigo"]: doc["custo"] for doc in uso["documentos"]}["RO-1"], 1000 * 0.40 / 1_000_000)
        self.assertEqual(nuvem, ["prazo do relatório?"])
        self.assertEqual(nuvem_ons, ["carga do nordeste?"])
        self.assertEqual(len(historico["consultas"]), 2)
        self.assertEqual(ons, [])

    def test_migracao_tira_trechos_e_leva_a_avaliacao_para_o_outro_arquivo(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            logs = pasta / "logs"
            logs.mkdir()
            linhas = [
                {"quando": "2026-09-30T10:00:00", "pergunta": "a", "origem": "web", "trechos": [{"texto": "longo"}]},
                {"quando": "2026-09-30T10:01:00", "pergunta": "b", "origem": "avaliacao", "trechos": []},
            ]
            (logs / "consultas.jsonl").write_text("\n".join(json.dumps(linha) for linha in linhas) + "\n", encoding="utf-8")
            self.assertTrue(migrar_logs(pasta))
            self.assertFalse(migrar_logs(pasta))
            consultas_log = (logs / "consultas.jsonl").read_text(encoding="utf-8")
            avaliacao_log = (logs / "avaliacao.jsonl").read_text(encoding="utf-8")
            copia = (logs / "consultas.jsonl.bak").exists()

        self.assertNotIn("trechos", consultas_log + avaliacao_log)
        self.assertIn('"a"', consultas_log)
        self.assertIn('"b"', avaliacao_log)
        self.assertTrue(copia)

    def test_leitura_incremental_ve_o_que_foi_acrescentado_e_o_arquivo_trocado(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            registrar(pasta, {"pergunta": "primeira", "origem": "web"})
            self.assertEqual(len(itens(pasta)), 1)
            registrar(pasta, {"pergunta": "segunda", "origem": "web"})
            self.assertEqual(len(itens(pasta)), 2)
            caminho = pasta / "logs" / "consultas.jsonl"
            caminho.write_text(json.dumps({"pergunta": "trocado", "origem": "web"}) + "\n", encoding="utf-8")
            self.assertEqual([item["pergunta"] for item in itens(pasta)], ["trocado"])
