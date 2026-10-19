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
    {"id": "invencao", "pergunta": "qual o setpoint do teste?", "deve_recusar": True},
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
    "qual o setpoint do teste?": (True, "O setpoint é 42.", "1. Objetivo"),
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


class RecusasPadraoTeste(unittest.TestCase):
    def test_prova_inclui_as_perguntas_fora_do_escopo_sem_duplicar(self) -> None:
        itens = avaliacao._com_recusas_padrao([{"id": "a", "deve_recusar": False}])
        padrao = [item for item in itens if item["id"].startswith("recusa-padrao-")]
        self.assertEqual(len(padrao), len(avaliacao.recusas_padrao()))
        self.assertTrue(all(item["deve_recusar"] for item in padrao))
        self.assertEqual(len(avaliacao._com_recusas_padrao(itens)), len(itens))


class AvaliacaoTeste(unittest.TestCase):
    def test_rodada_classifica_cada_status_e_repete_do_cache(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
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
                com_recusas=False,
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
            self.assertEqual(len(julgamentos), 2)
            self.assertTrue(all("Trechos citados:\nDocumento: IT-EXEMPLO-001" in julgamento for julgamento in julgamentos))
            self.assertEqual(itens["acerto"]["motivo"], "A resposta contém a resposta esperada.")

            resumo = rodada["resumo"]
            self.assertEqual(resumo["total"], 6)
            self.assertEqual(resumo["acertos"], 2)
            self.assertEqual(resumo["acertividade"], 33.3)
            self.assertGreater(resumo["custo_usd"], 0)
            self.assertEqual([item["id"] for item in avaliacao.listar_rodadas(data)], [rodada["id"]])
            self.assertEqual(avaliacao.ler_rodada(data, rodada["id"])["resumo"], resumo)

            log = (data / "logs" / "avaliacao.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertTrue(log)
            self.assertTrue(all(json.loads(linha)["origem"] == "avaliacao" for linha in log))
            self.assertFalse((data / "logs" / "consultas.jsonl").exists())

            nada: list[str] = []
            repetida = avaliacao.rodar(
                data, acervo, gerador=_gerador(nada), juiz=_juiz(nada), embedder=_embedder, indice=indice, com_recusas=False
            )
            self.assertEqual(nada, [])
            self.assertEqual(repetida["resumo"]["do_cache"], 5)
            self.assertEqual(repetida["resumo"]["custo_usd"], 0)
            self.assertFalse(avaliacao.estado(data)["progresso"]["rodando"])

            rejulgada = avaliacao.rejulgar(data, rodada["id"], juiz=_juiz(nada), indice=indice)
            self.assertEqual(rejulgada["id"], f"{rodada['id']}-rejulgada")
            self.assertEqual(rejulgada["rejulgada_de"], rodada["id"])
            self.assertEqual({item["id"]: item["status"] for item in rejulgada["itens"]}, status)
            self.assertEqual(nada, [])
            self.assertEqual(avaliacao.ler_rodada(data, rejulgada["id"])["resumo"]["acertos"], 2)

    def test_revisao_corrige_descarta_e_mede_as_revisadas(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            avaliacao.importar(data, json.dumps(_GABARITO))
            chamadas: list[str] = []

            def rodar() -> dict:
                return avaliacao.rodar(
                    data, acervo, gerador=_gerador(chamadas), juiz=_juiz(chamadas), embedder=_embedder, indice=indice
                )

            antes = rodar()
            amostra = avaliacao.revisao(data, tamanho=4)
            self.assertEqual(amostra["total"], 4)
            self.assertEqual([item["id"] for item in avaliacao.revisao(data)["itens"]][:4], [item["id"] for item in amostra["itens"]])
            avaliacao.revisar(data, "acerto", "confere")
            avaliacao.revisar(data, "erro", "corrigida", "Citar a seção 3.", "Conferido com o COG")
            avaliacao.revisar(data, "invencao", "descartada")
            with self.assertRaisesRegex(ValueError, "resposta corrigida"):
                avaliacao.revisar(data, "recusa", "corrigida", "  ")
            with self.assertRaises(FileNotFoundError):
                avaliacao.revisar(data, "nao-existe", "confere")
            self.assertEqual(avaliacao.estado(data)["revisadas"], 3)

            depois = rodar()
            itens = {item["id"]: item for item in depois["itens"]}
            self.assertNotIn("invencao", itens)
            self.assertEqual(itens["erro"]["resposta_esperada"], "Citar a seção 3.")
            self.assertEqual(itens["erro"]["revisao"], "corrigida")
            self.assertEqual(
                avaliacao.ler_rodada(data, depois["id"])["revisadas"], {"total": 2, "acertos": 1, "acertividade": 50.0}
            )
            self.assertEqual(avaliacao.ler_rodada(data, antes["id"])["revisadas"]["total"], 1)

            avaliacao.desfazer_revisao(data, "invencao")
            self.assertEqual(avaliacao.estado(data)["revisadas"], 2)

    def test_importar_valida_e_aceita_bloco_de_codigo(self) -> None:
        with TemporaryDirectory() as temporario:
            data = Path(temporario)
            texto = "```json\n" + json.dumps({"perguntas": [{"pergunta": "Qual o prazo?", "resposta_esperada": "15 dias úteis", "valores": "15 dias úteis"}]}) + "\n```"
            itens = avaliacao.importar(data, texto.encode("utf-8"))["itens"]
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
            self.assertEqual(avaliacao.importar(data, json.dumps({"gabarito": [pergunta, pergunta | {"id": "b"}]}))["importadas"], 2)
            self.assertEqual(avaliacao.importar(data, json.dumps({"titulo": "ITOM", "dados": {"questoes": [pergunta]}}))["importadas"], 1)
            self.assertEqual(avaliacao.importar(data, json.dumps(pergunta))["importadas"], 1)
            self.assertEqual(len(avaliacao.gabarito(data)), 1)

    def test_importar_soma_blocos_por_documento(self) -> None:
        def bloco(documento: str, quantidade: int, prefixo: str = "") -> str:
            itens = [
                {"id": i, "pergunta": f"{prefixo}{documento} {i}?", "resposta_esperada": "Sim.", "documento": documento}
                for i in range(1, quantidade + 1)
            ]
            return json.dumps(itens, ensure_ascii=False)

        with TemporaryDirectory() as temporario:
            data = Path(temporario)
            primeiro = avaliacao.importar(data, bloco("ITOM EGP 01", 3))
            segundo = avaliacao.importar(data, bloco("ITOM AGD 01 Rev.00 - PROCEDIMENTOS", 2))
            ids = [item["id"] for item in avaliacao.gabarito(data)]
            avaliacao.revisar(data, "ITOM-AGD-01-1", "confere")
            terceiro = avaliacao.importar(data, bloco("ITOM AGD 01", 4, "Nova "))
            depois = avaliacao.gabarito(data)
            revisao = avaliacao.revisoes(data)["itens"]["ITOM-AGD-01-1"]
            avaliacao.revisar(data, "ITOM-AGD-01-2", "confere")
            avaliacao.importar(data, bloco("ITOM AGD 01", 4, "Nova "))
            mantida = avaliacao.revisoes(data)["itens"]["ITOM-AGD-01-2"]
            reimportada = next(item for item in avaliacao.gabarito(data) if item["id"] == "ITOM-AGD-01-2")
            estado = avaliacao.estado(data)["gabarito"]

        self.assertEqual((primeiro["total"], primeiro["substituidas"]), (3, 0))
        self.assertEqual((segundo["importadas"], segundo["substituidas"], segundo["total"]), (2, 0, 5))
        self.assertEqual(ids, ["1", "2", "3", "ITOM-AGD-01-1", "ITOM-AGD-01-2"])
        self.assertEqual((terceiro["importadas"], terceiro["substituidas"], terceiro["total"]), (4, 2, 7))
        self.assertEqual(terceiro["documentos"], ["ITOM AGD 01"])
        self.assertEqual(sum(1 for item in depois if item["documento"] == "ITOM EGP 01"), 3)
        self.assertEqual(sum(1 for item in depois if item["documento"].startswith("ITOM AGD")), 4)
        self.assertNotEqual(revisao["pergunta"], next(item for item in depois if item["id"] == "ITOM-AGD-01-1")["pergunta"])
        self.assertEqual(mantida["pergunta"], reimportada["pergunta"])
        self.assertEqual([item["perguntas"] for item in estado["documentos"]], [3, 4])

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
            itens = avaliacao.importar(data, json.dumps(conteudo, ensure_ascii=False).encode("utf-8"))["itens"]
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
