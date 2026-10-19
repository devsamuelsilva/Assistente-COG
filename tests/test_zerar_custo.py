from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app import uso


def _linha(quando: str, origem: str = "web") -> dict:
    return {
        "quando": quando,
        "pergunta": f"pergunta {quando}",
        "resposta": "sim",
        "tokens_prompt": 1_000_000,
        "tokens_resposta": 0,
        "cache": False,
        "modelo": "gpt-4.1-mini",
        "origem": origem,
        "coberto": True,
    }


class ZerarCustoTeste(unittest.TestCase):
    def _montar(self, pasta: Path) -> None:
        logs = pasta / "logs"
        logs.mkdir()
        eventos = [_linha("2020-01-01T10:00:00"), _linha("2020-01-02T10:00:00", "avaliacao"), _linha("2999-01-01T10:00:00")]
        (logs / "consultas.jsonl").write_text("\n".join(json.dumps(evento) for evento in eventos) + "\n", encoding="utf-8")

    def test_zerar_so_conta_o_que_vem_depois_e_nao_apaga_nada(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._montar(pasta)
            antes = uso.ler_uso(pasta)
            self.assertEqual(antes["desde"], "")
            self.assertEqual(antes["resumo"]["consultas"], 3)
            self.assertAlmostEqual(antes["resumo"]["custo_usd"], 1.2, places=4)

            desde = uso.zerar(pasta)
            depois = uso.ler_uso(pasta)
            self.assertEqual(depois["desde"], desde)
            # só a consulta datada no futuro conta; as duas antigas ficam fora dos totais
            self.assertEqual(depois["resumo"]["consultas"], 1)
            self.assertAlmostEqual(depois["resumo"]["custo_usd"], 0.4, places=4)
            self.assertEqual(depois["antes_do_marco"], 2)
            self.assertAlmostEqual(depois["custo_total_historico"], 1.2, places=4)
            self.assertAlmostEqual(sum(origem["custo_usd"] for origem in depois["origens"]), 0.4, places=4)
            self.assertAlmostEqual(sum(dia["custo"] for dia in depois["dias"]), 0.4, places=4)
            # o registro e o custo de cada pergunta continuam
            self.assertEqual(len(uso.itens(pasta)), 3)
            self.assertEqual(len(uso.consultas(pasta, 10)["consultas"]), 2)  # a avaliação nunca aparece no histórico
            self.assertTrue(all(item["custo_usd"] == 0.4 for item in uso.itens(pasta)))

    def test_voltar_a_somar_tudo(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._montar(pasta)
            uso.zerar(pasta)
            uso.somar_tudo(pasta)
            uso.somar_tudo(pasta)  # repetir não dá erro
            self.assertEqual(uso.marco(pasta), "")
            self.assertEqual(uso.ler_uso(pasta)["resumo"]["consultas"], 3)

    def test_marco_ilegivel_equivale_a_nao_ter_marco(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._montar(pasta)
            (pasta / "uso").mkdir()
            (pasta / "uso" / "marco.json").write_text("não é json", encoding="utf-8")
            self.assertEqual(uso.marco(pasta), "")
            self.assertEqual(uso.ler_uso(pasta)["resumo"]["consultas"], 3)


if __name__ == "__main__":
    unittest.main()
