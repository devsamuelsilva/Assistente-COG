from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app import uso
from app.rag import Resultado, _documentos_da_consulta


def _evento(quando: str, documento: str | None, coberto: bool = True, **extra) -> dict:
    evento = {
        "quando": quando,
        "pergunta": f"pergunta {quando}",
        "resposta": "sim" if coberto else "Esse ponto não está",
        "tokens_prompt": 1_000_000,
        "tokens_resposta": 0,
        "cache": False,
        "modelo": "gpt-4.1-mini",
        "origem": "web",
        "coberto": coberto,
        "citacoes": [{"codigo": documento, "secao": "1"}] if documento and coberto else [],
    }
    if documento:
        evento["documento_principal"] = [documento]
    return {**evento, **extra}


class RankingTeste(unittest.TestCase):
    def _gravar(self, pasta: Path, eventos: list[dict]) -> None:
        (pasta / "logs").mkdir(exist_ok=True)
        (pasta / "logs" / "consultas.jsonl").write_text("\n".join(json.dumps(e) for e in eventos) + "\n", encoding="utf-8")

    def test_ranking_atribui_cada_pergunta_a_um_documento_e_soma_o_custo(self) -> None:
        eventos = [_evento(f"2026-10-0{n}T10:00:00", "ITOM 01") for n in range(1, 4)]
        eventos += [_evento("2026-10-05T10:00:00", "NO 01", cache=True)]
        eventos += [_evento(f"2026-10-1{n}T10:00:00", "ITOM 05", coberto=False) for n in range(0, 6)]
        eventos += [_evento("2026-10-20T10:00:00", None, coberto=False)]
        eventos += [_evento("2026-10-21T10:00:00", "ITOM 01", origem="avaliacao")]
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._gravar(pasta, eventos)
            ranking = uso.ler_uso(pasta)["ranking_documentos"]
            por = {linha["codigo"]: linha for linha in ranking}
            self.assertEqual(por["ITOM 01"]["perguntas"], 3)  # a avaliação não entra
            self.assertAlmostEqual(por["ITOM 01"]["custo_usd"], 1.2, places=4)
            self.assertEqual(por["NO 01"]["cache"], 1)
            self.assertEqual(por["NO 01"]["custo_usd"], 0.0)
            self.assertEqual(por[uso.SEM_DOCUMENTO]["perguntas"], 1)
            # os totais batem com o resumo do chat normativo (cada pergunta em um só documento)
            self.assertEqual(sum(linha["perguntas"] for linha in ranking), 3 + 1 + 6 + 1)
            self.assertAlmostEqual(sum(linha["custo_usd"] for linha in ranking), 1.2 + 2.4 + 0.4, places=4)
            self.assertAlmostEqual(sum(linha["parte_do_custo"] for linha in ranking), 1.0, places=2)
            # ordenado do mais caro para o mais barato
            self.assertEqual(ranking[0]["codigo"], "ITOM 05")

    def test_candidato_a_treino_exige_volume_e_muita_pergunta_sem_resposta(self) -> None:
        eventos = [_evento(f"2026-10-1{n}T10:00:00", "ITOM 05", coberto=False) for n in range(0, 6)]  # 6, todas sem resposta
        eventos += [_evento(f"2026-10-0{n}T10:00:00", "ITOM 01") for n in range(1, 8)]  # 7, todas respondidas
        eventos += [_evento(f"2026-10-2{n}T10:00:00", "NO 01", coberto=False) for n in range(0, 3)]  # só 3: pouco para decidir
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._gravar(pasta, eventos)
            por = {linha["codigo"]: linha for linha in uso.ler_uso(pasta)["ranking_documentos"]}
            self.assertTrue(por["ITOM 05"]["candidato_a_treino"])
            self.assertFalse(por["ITOM 01"]["candidato_a_treino"])
            self.assertFalse(por["NO 01"]["candidato_a_treino"])
            self.assertEqual(por["ITOM 05"]["fracao_sem_resposta"], 1.0)

    def test_respeita_o_marco_de_zerar_e_a_retorno_do_operador(self) -> None:
        eventos = [_evento("2020-01-01T10:00:00", "ITOM 01"), _evento("2999-01-01T10:00:00", "ITOM 01", id="x1")]
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._gravar(pasta, eventos)
            (pasta / "logs" / "retornos.jsonl").write_text(
                json.dumps({"consulta": "x1", "util": False, "comentario": "", "quando": "2999-01-02T10:00:00"}) + "\n",
                encoding="utf-8",
            )
            uso.zerar(pasta)
            ranking = uso.ler_uso(pasta)["ranking_documentos"]
            self.assertEqual(ranking[0]["perguntas"], 1)
            self.assertEqual(ranking[0]["nao_uteis"], 1)

    def test_filtro_por_documento_no_historico(self) -> None:
        eventos = [_evento("2026-10-01T10:00:00", "ITOM 01"), _evento("2026-10-02T10:00:00", "NO 01")]
        eventos += [_evento("2026-10-03T10:00:00", "ITOM 05", coberto=False)]
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            self._gravar(pasta, eventos)
            self.assertEqual(len(uso.consultas(pasta, 10)["consultas"]), 3)
            so_no = uso.consultas(pasta, 10, documento="NO 01")["consultas"]
            self.assertEqual([item["documento"] for item in so_no], ["NO 01"])
            recusada = uso.consultas(pasta, 10, documento="ITOM 05")["consultas"]
            self.assertFalse(recusada[0]["coberto"])  # a recusada também aparece no documento mais provável

    def test_documento_da_consulta_vem_do_citado_ou_do_mais_provavel(self) -> None:
        trechos = [
            {"arquivo": "a.pdf", "secao": "1", "codigo": "ITOM 05", "revisao": "0"},
            {"arquivo": "b.pdf", "secao": "2", "codigo": "ITOM 01", "revisao": "0"},
        ]
        respondida = Resultado(
            pergunta="p", resposta="r", coberto=True, citacoes=[{"codigo": "ITOM 01", "arquivo": "b.pdf", "secao": "2"}], trechos=trechos
        )
        self.assertEqual(_documentos_da_consulta(respondida), (["ITOM 05", "ITOM 01"], ["ITOM 01"]))
        recusada = Resultado(pergunta="p", resposta="Esse ponto não está", coberto=False, citacoes=[], trechos=trechos)
        self.assertEqual(_documentos_da_consulta(recusada), (["ITOM 05", "ITOM 01"], ["ITOM 05"]))
        sem_nada = Resultado(pergunta="p", resposta="x", coberto=False, citacoes=[])
        self.assertEqual(_documentos_da_consulta(sem_nada), ([], []))


if __name__ == "__main__":
    unittest.main()
