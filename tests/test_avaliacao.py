from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app import avaliacao
from app.indice import indexar
from tests.test_rag import _embedder

RAIZ = Path(__file__).resolve().parents[1]

_GABARITO = [
    {
        "id": "acerto",
        "pergunta": "qual o valor de teste?",
        "resposta_esperada": "42 unidades de exemplo.",
        "secao": "2",
        "valores": ["42 unidades"],
    },
    {
        "id": "valor-ausente",
        "pergunta": "qual o valor numérico do exemplo?",
        "resposta_esperada": "42 unidades de exemplo.",
        "secao": "2",
        "valores": ["42 unidades"],
    },
    {
        "id": "erro",
        "pergunta": "qual a sequência de teste?",
        "resposta_esperada": "Confirmar a pergunta, citar a seção 3 e recusar o resto.",
        "secao": "3",
    },
    {"id": "recusa", "pergunta": "qual a cor do cachorro?", "deve_recusar": True},
    {"id": "invencao", "pergunta": "qual o setpoint de tensão?", "deve_recusar": True},
    {
        "id": "recusa-indevida",
        "pergunta": "qual o objetivo do exemplo?",
        "resposta_esperada": "Permitir que o assistente cite uma seção.",
        "secao": "1",
    },
]

_RESPOSTAS = {
    "qual o valor de teste?": (True, "O valor de teste é 42 unidades de exemplo.", "2. Valor de teste"),
    "qual o valor numérico do exemplo?": (True, "O valor é quarenta e dois.", "2. Valor de teste"),
    "qual a sequência de teste?": (True, "Resposta errada sobre a sequência.", "3. Sequência de teste"),
    "qual a cor do cachorro?": (False, "", ""),
    "qual o setpoint de tensão?": (True, "O setpoint é 42.", "1. Objetivo"),
    "qual o objetivo do exemplo?": (False, "", ""),
}


def _gerador(chamadas: list[str]):
    def gerar(mensagens: list[dict]) -> tuple[str, int, int]:
        pedido = mensagens[1]["content"]
        pergunta = pedido.split("Pergunta atual:\n", 1)[1].split("\n\nTrechos", 1)[0].strip()
        chamadas.append(pergunta)
        coberto, resposta, secao = _RESPOSTAS[pergunta]
        citacoes = [{"arquivo": "exemplo-normativo.md", "secao": secao}] if secao else []
        return json.dumps({"coberto": coberto, "resposta": resposta, "citacoes": citacoes}), 100, 20

    return gerar


def _juiz(chamadas: list[str]):
    def julgar(mensagens: list[dict]) -> tuple[str, int, int]:
        conteudo = mensagens[1]["content"]
        chamadas.append(conteudo)
        nota = "incorreto" if "errada" in conteudo else "correto"
        return json.dumps({"nota": nota, "motivo": f"Juiz de teste: {nota}."}), 50, 10

    return julgar


class AvaliacaoTeste(unittest.TestCase):
    def test_rodada_classifica_cada_status_e_repete_do_cache(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "acervo" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            avaliacao.importar(data, json.dumps(_GABARITO))

            respostas: list[str] = []
            julgamentos: list[str] = []
            rodada = avaliacao.rodar(
                data,
                acervo,
                gerador=_gerador(respostas),
                juiz=_juiz(julgamentos),
                embedder=_embedder,
                indice=indice,
            )
            status = {item["id"]: item["status"] for item in rodada["itens"]}
            self.assertEqual(
                status,
                {
                    "acerto": "acerto",
                    "valor-ausente": "parcial",
                    "erro": "erro",
                    "recusa": "acerto",
                    "invencao": "invencao",
                    "recusa-indevida": "recusa_indevida",
                },
            )
            itens = {item["id"]: item for item in rodada["itens"]}
            self.assertEqual(itens["valor-ausente"]["valores_ausentes"], ["42 unidades"])
            self.assertTrue(itens["acerto"]["secao_ok"])
            self.assertTrue(itens["recusa-indevida"]["secao_recuperada"])
            self.assertEqual(len(julgamentos), 3)

            resumo = rodada["resumo"]
            self.assertEqual(resumo["total"], 6)
            self.assertEqual(resumo["acertos"], 2)
            self.assertEqual(resumo["acertividade"], 33.3)
            self.assertGreater(resumo["custo_usd"], 0)
            self.assertEqual([item["id"] for item in avaliacao.listar_rodadas(data)], [rodada["id"]])
            self.assertEqual(avaliacao.ler_rodada(data, rodada["id"])["resumo"], resumo)

            log = (data / "logs" / "consultas.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertTrue(all(json.loads(linha)["origem"] == "avaliacao" for linha in log))

            nada: list[str] = []
            repetida = avaliacao.rodar(
                data, acervo, gerador=_gerador(nada), juiz=_juiz(nada), embedder=_embedder, indice=indice
            )
            self.assertEqual(nada, [])
            self.assertEqual(repetida["resumo"]["do_cache"], 6)
            self.assertEqual(repetida["resumo"]["custo_usd"], 0)
            self.assertFalse(avaliacao.estado(data)["progresso"]["rodando"])

    def test_importar_valida_e_aceita_bloco_de_codigo(self) -> None:
        with TemporaryDirectory() as temporario:
            data = Path(temporario)
            texto = "```json\n" + json.dumps({"perguntas": [{"pergunta": "Qual o prazo?", "resposta_esperada": "15 dias úteis", "valores": "15 dias úteis"}]}) + "\n```"
            itens = avaliacao.importar(data, texto.encode("utf-8"))
            self.assertEqual(itens[0]["id"], "q-001")
            self.assertEqual(itens[0]["valores"], ["15 dias úteis"])
            self.assertEqual(avaliacao.estado(data)["gabarito"]["total"], 1)
            with self.assertRaisesRegex(ValueError, "sem resposta_esperada"):
                avaliacao.importar(data, json.dumps([{"pergunta": "Sem resposta?"}]))
            with self.assertRaisesRegex(ValueError, "id repetido"):
                avaliacao.importar(
                    data,
                    json.dumps(
                        [
                            {"id": "a", "pergunta": "Um?", "deve_recusar": True},
                            {"id": "a", "pergunta": "Dois?", "deve_recusar": True},
                        ]
                    ),
                )
            with self.assertRaisesRegex(ValueError, "JSON válido"):
                avaliacao.importar(data, "não é json")
            with self.assertRaisesRegex(ValueError, "Chaves encontradas: titulo"):
                avaliacao.importar(data, json.dumps({"titulo": "ITOM 01", "total": 0}))
            self.assertEqual(len(avaliacao.gabarito(data)), 1)

    def test_importar_acha_a_lista_dentro_de_qualquer_chave(self) -> None:
        pergunta = {"pergunta": "Qual o prazo?", "resposta_esperada": "15 dias úteis"}
        with TemporaryDirectory() as temporario:
            data = Path(temporario)
            self.assertEqual(len(avaliacao.importar(data, json.dumps({"gabarito": [pergunta, pergunta | {"id": "b"}]}))), 2)
            self.assertEqual(len(avaliacao.importar(data, json.dumps({"titulo": "ITOM", "dados": {"questoes": [pergunta]}}))), 1)
            self.assertEqual(len(avaliacao.importar(data, json.dumps(pergunta))), 1)

    def test_importar_formato_do_notebooklm(self) -> None:
        conteudo = {
            "documento": "ITOM 01 - Instrução de Trabalho",
            "total_perguntas": 3,
            "categorias": ["Prazos e SI-PS"],
            "perguntas_e_respostas": [
                {"id": 1, "categoria": "Prazos e SI-PS", "pergunta": "Prazo?", "resposta": "15 dias úteis.", "referencia": "ITOM EGP 01, Tabela 5.1.1"},
                {"id": 2, "categoria": "Responsabilidades", "pergunta": "Quem?", "resposta": "O emitente.", "referencia": "ITOM EGP 01, Item 2 e Item 11 (subitem j)"},
                {"id": 3, "categoria": "Documentos", "pergunta": "Quais anexos?", "resposta": "AI e ATEIE.", "referencia": "ITOM EGP 01, Anexos 03 e 04"},
            ],
        }
        with TemporaryDirectory() as temporario:
            data = Path(temporario)
            itens = avaliacao.importar(data, json.dumps(conteudo, ensure_ascii=False).encode("utf-8"))
            self.assertEqual([item["id"] for item in itens], ["1", "2", "3"])
            self.assertEqual(itens[0]["resposta_esperada"], "15 dias úteis.")
            self.assertEqual([item["secao"] for item in itens], ["5.1.1", "2; 11", "Anexo 03; Anexo 04"])
            self.assertEqual(itens[0]["documento"], "ITOM EGP 01")
            self.assertEqual(itens[1]["categoria"], "Responsabilidades")
            gabarito = avaliacao.estado(data)["gabarito"]
            self.assertEqual(len(gabarito["categorias"]), 3)
        self.assertTrue(avaliacao._secao_confere("2; 11", ["11.3.2 SERVIÇOS EM LINHAS ENERGIZADAS"]))
        self.assertTrue(avaliacao._secao_confere("Anexo 03; Anexo 04", ["Anexo 4 ATEIE (preenchimento)"]))
        self.assertFalse(avaliacao._secao_confere("Anexo 01", ["Anexo 10 Outro"]))

    def test_secao_confere_por_numero_e_por_nome(self) -> None:
        self.assertTrue(avaliacao._secao_confere("5.1", ["5.1.1 SOLICITAÇÃO DA SI-PS"]))
        self.assertTrue(avaliacao._secao_confere("5.1.1", ["5.1.1 SOLICITAÇÃO DA SI-PS"]))
        self.assertFalse(avaliacao._secao_confere("5.1", ["5.10 OUTRA"]))
        self.assertTrue(avaliacao._secao_confere("Anexo 01", ["Anexo 01 SI-PS"]))
        self.assertIsNone(avaliacao._secao_confere("", ["5.1.1"]))


if __name__ == "__main__":
    unittest.main()
