from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import numpy as np

from app import avaliacao, confianca, treino
from app.arquivos import gravar_json, ler_json
from app.indice import caminho_aprendidas, carregar, indexar
from tests.test_rag import _embedder

_TEXTO = (
    "O operador confirma a condição do equipamento, registra o horário no livro de turno e comunica o centro "
    "regional antes de seguir. Se houver alarme ativo, a sequência para e o supervisor decide o próximo passo "
    "conforme a instrução vigente e o registro da ocorrência."
)
_DOCUMENTO = f"""# IT-TREINO-002 — Instrução de treino

Código: IT-TREINO-002
Revisão: 0
Status: vigente

## 1. Partida da turbina

{_TEXTO} A partida exige vento acima de 4 m/s.

## 2. Parada de emergência

{_TEXTO} A parada de emergência usa o botão local.

## 3. Inspeção do transformador

{_TEXTO} A inspeção do transformador ocorre a cada 30 dias.
"""


def _gerador(chamadas: list[str]):
    def gerar(sistema: str, texto: str) -> tuple[str, int, int]:
        chamadas.append(sistema)
        if sistema == treino._PEDIDO_SECAO:
            secao = texto.splitlines()[0].removeprefix("Seção: ")
            pergunta = {"pergunta": f"o que fazer em {secao.lower()}?", "resposta": "Seguir a seção.", "valores": []}
            return json.dumps({"perguntas": [pergunta]}), 100, 20
        if sistema == treino._PEDIDO_RECUSA:
            return json.dumps({"perguntas": ["qual a cor da torre?", "quem paga o almoço?", "qual o salário?"]}), 100, 20
        return json.dumps({"pergunta": f"pergunta reescrita: {texto}"}), 50, 10

    return gerar


def _recusa(mensagens: list[dict]) -> tuple[str, int, int]:
    return json.dumps({"coberto": False, "resposta": "", "citacoes": []}), 100, 20


class TreinoTeste(unittest.TestCase):
    def test_preparar_gera_confere_aprende_e_amostra_so_o_que_mudou(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            (acervo / "it-treino.md").write_text(_DOCUMENTO, encoding="utf-8")
            data = raiz / "data"
            chamadas: list[str] = []
            etapas: list[str] = []

            def preparar() -> dict:
                return treino.preparar(
                    data,
                    acervo,
                    progresso=lambda etapa, feitos=0, total=0: etapas.append(etapa),
                    indexador=lambda: indexar(acervo, data, embedder=_embedder),
                    gerador=_gerador(chamadas),
                    respondedor=_recusa,
                    embedder=_embedder,
                )

            resultado = preparar()
            self.assertEqual(resultado["treinados"], ["it-treino.md"])
            dados = treino.registro(data, "it-treino.md")
            normais = [item for item in dados["perguntas"] if not item["deve_recusar"]]
            recusas = [item for item in dados["perguntas"] if item["deve_recusar"]]
            self.assertEqual(len(normais), 3)
            self.assertEqual(dados["busca"]["total"], 3)
            self.assertEqual(sum(item["pergunta"].startswith("IT-TREINO-002 - ") for item in normais), 1)
            self.assertEqual(len(recusas), 3)
            self.assertTrue(all(item["pergunta"].startswith("IT-TREINO-002 - ") for item in recusas))
            self.assertGreater(dados["custo_usd"]["perguntas"], 0)

            falhas = dados["busca"]["total"] - dados["busca"]["ok"]
            self.assertEqual(dados["aprendidas"], falhas)
            aprendidas = ler_json(caminho_aprendidas(data), {})
            self.assertEqual(sum(len(lista) for lista in aprendidas.values()), falhas)
            if falhas:
                self.assertIn("reindexando com as perguntas aprendidas", etapas)
                self.assertEqual(dados["conferencia"]["total"], falhas)
                perguntas_indice = {pergunta for trecho in carregar(data).trechos for pergunta in trecho.perguntas}
                for lista in aprendidas.values():
                    self.assertTrue(set(lista) <= perguntas_indice)

            self.assertEqual(dados["amostra"]["total"], 6)
            self.assertEqual(avaliacao.listar_rodadas(data), [])
            rodadas = avaliacao.listar_rodadas(data, "treino")
            self.assertEqual([rodada["documento"] for rodada in rodadas], ["IT-TREINO-002"])
            rodada = avaliacao.ler_rodada(data, rodadas[0]["id"])
            self.assertTrue(all(item["status"] == "acerto" for item in rodada["itens"] if item["deve_recusar"]))
            self.assertTrue(all(item["causa"] in {"busca", "recusa"} for item in rodada["itens"] if not item["deve_recusar"]))
            self.assertTrue(all("coseno_max" in item["sinais"] for item in rodada["itens"]))

            chamadas.clear()
            self.assertEqual(preparar()["treinados"], [])
            self.assertEqual(chamadas, [])

    def test_falha_de_busca_vira_pergunta_aprendida_e_e_conferida(self) -> None:
        conferir_real = treino.conferir_busca

        def conferir_com_falha(indice, itens, *argumentos) -> None:
            conferir_real(indice, itens, *argumentos)
            for item in itens:
                if "transformador" in item["pergunta"] and "reescrita" not in item["pergunta"]:
                    item["busca_ok"] = False

        with TemporaryDirectory() as temporario, mock.patch.object(treino, "conferir_busca", conferir_com_falha):
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            (acervo / "it-treino.md").write_text(_DOCUMENTO, encoding="utf-8")
            data = raiz / "data"
            etapas: list[str] = []
            argumentos = {
                "progresso": lambda etapa, feitos=0, total=0: etapas.append(etapa),
                "indexador": lambda: indexar(acervo, data, embedder=_embedder),
                "gerador": _gerador([]),
                "respondedor": _recusa,
                "embedder": _embedder,
            }
            treino.preparar(data, acervo, **argumentos)
            dados = treino.registro(data, "it-treino.md")
            self.assertEqual(dados["busca"], {"ok": 2, "total": 3})
            self.assertEqual(dados["aprendidas"], 1)
            self.assertIn("reindexando com as perguntas aprendidas", etapas)
            self.assertEqual(dados["conferencia"]["total"], 1)
            self.assertEqual(dados["conferencia"]["ok"], 1)
            aprendidas = ler_json(caminho_aprendidas(data), {})
            self.assertEqual(aprendidas, {"it-treino.md\n3. Inspeção do transformador": ["o que fazer em 3. inspeção do transformador?"]})
            secao = [trecho for trecho in carregar(data).trechos if trecho.secao == "3. Inspeção do transformador"]
            self.assertIn("o que fazer em 3. inspeção do transformador?", secao[0].perguntas)

            treino.preparar(data, acervo, forcar={"it-treino.md"}, **argumentos)
            self.assertEqual(ler_json(caminho_aprendidas(data), {}), aprendidas)

    def test_valores_do_gpt_nao_entram_no_julgamento(self) -> None:
        geradas = [
            {"pergunta": "Qual a tensão?", "resposta": "138 kV.", "valores": ["138kV"]},
            {"pergunta": "Sem resposta?", "resposta": ""},
        ]
        self.assertEqual(
            treino._perguntas_validas({"perguntas": geradas}),
            [{"pergunta": "Qual a tensão?", "resposta": "138 kV.", "valores": []}],
        )

    def test_descarta_pergunta_quase_igual_a_de_busca(self) -> None:
        self.assertTrue(treino._parecida("Qual o prazo da manobra programada?", ["qual o prazo da manobra programada"]))
        self.assertFalse(treino._parecida("Quem autoriza a manobra?", ["Qual o prazo da manobra programada?"]))

    def test_amostra_estavel_e_espalhada_pelas_secoes(self) -> None:
        itens = [
            treino._item("a.pdf", "IT-1", f"{secao}. Seção", f"pergunta {secao}-{numero}", "r", [])
            for secao in range(1, 5)
            for numero in range(5)
        ]
        itens += [treino._item("a.pdf", "IT-1", "", f"recusa {numero}", "", [], deve_recusar=True) for numero in range(5)]
        escolhidos = treino.amostra(itens, normais=8, recusas=3)
        self.assertEqual(escolhidos, treino.amostra(list(reversed(itens)), normais=8, recusas=3))
        normais = [item for item in escolhidos if not item["deve_recusar"]]
        self.assertEqual(len(normais), 8)
        self.assertEqual({item["secao"] for item in normais}, {f"{secao}. Seção" for secao in range(1, 5)})
        self.assertEqual(sum(item["deve_recusar"] for item in escolhidos), 3)


class ConfiancaTeste(unittest.TestCase):
    def test_auc(self) -> None:
        self.assertEqual(confianca.auc_roc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])), 1.0)
        self.assertEqual(confianca.auc_roc(np.array([0, 1]), np.array([0.5, 0.5])), 0.5)

    def test_liga_so_com_exemplos_suficientes_e_separacao(self) -> None:
        aleatorio = np.random.default_rng(1)

        def rodada(data: Path, quantidade: int) -> None:
            itens = []
            for numero in range(quantidade):
                coseno = float(aleatorio.uniform(0.2, 0.9))
                acerto = coseno + aleatorio.normal(0, 0.08) > 0.55
                itens.append(
                    {
                        "pergunta": f"pergunta {numero}",
                        "resposta": f"resposta {numero}",
                        "coberto": True,
                        "status": "acerto" if acerto else "erro",
                        "sinais": {"coseno_max": coseno, "cit_coseno": coseno, "citacoes": 1, "logprob": None},
                    }
                )
            itens.append({"pergunta": "recusou", "coberto": False, "status": "acerto", "sinais": {"coseno_max": 0.1}})
            gravar_json(data / "avaliacao" / "rodadas" / "r1.json", {"id": "r1", "itens": itens})

        with TemporaryDirectory() as temporario:
            data = Path(temporario)
            rodada(data, 50)
            pouco = confianca.treinar(data)
            self.assertFalse(pouco["ligado"])
            self.assertEqual(pouco["exemplos"], 50)
            self.assertEqual(confianca.avaliar(data, {"coseno_max": 0.9}), {})

            rodada(data, 400)
            estado = confianca.treinar(data)
            self.assertTrue(estado["ligado"], estado.get("motivo"))
            self.assertGreaterEqual(estado["auc"], 0.7)
            alta = confianca.avaliar(data, {"coseno_max": 0.88, "cit_coseno": 0.88, "citacoes": 1})
            baixa = confianca.avaliar(data, {"coseno_max": 0.22, "cit_coseno": 0.22, "citacoes": 1})
            self.assertGreater(alta["probabilidade"], baixa["probabilidade"])
            self.assertEqual(baixa["nivel"], "baixa")


if __name__ == "__main__":
    unittest.main()
