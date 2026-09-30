from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.uso import ler_uso


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
            resumo = uso["resumo"]
            self.assertEqual(resumo["consultas"], 3)
            self.assertEqual(resumo["cobradas"], 1)
            self.assertEqual(resumo["cache"], 1)
            self.assertEqual(resumo["sem_tabela"], 1)
            self.assertEqual(resumo["tokens_entrada"], 1000)
            self.assertEqual(resumo["tokens_saida"], 100)
            self.assertAlmostEqual(resumo["custo_usd"], (1000 * 0.40 + 100 * 1.60) / 1_000_000)
            self.assertEqual(uso["consultas"][0]["quando"], "2026-09-29T11:02:00")
            self.assertIsNone(uso["consultas"][0]["custo_usd"])
            self.assertEqual(uso["consultas"][1]["custo_usd"], 0.0)
