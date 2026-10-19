from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.indice import indexar
from app.rag import _CATALOGO, _linha_documento, formatar, responder
from tests.test_rag import _embedder


def _documento(codigo: str, titulo: str, status: str = "vigente", revisao: str = "0") -> str:
    return (
        f"# {titulo}\n\nCódigo: {codigo}\nRevisão: {revisao}\nStatus: {status}\n\n"
        f"## 1. Objetivo\n\nDescrever o assunto de {codigo} com texto suficiente para virar um trecho.\n\n"
        f"## 2. Procedimento\n\nO operador executa o procedimento de {codigo} conforme esta instrução.\n"
    )


class DeteccaoTeste(unittest.TestCase):
    def test_pergunta_sobre_a_base_e_detectada(self) -> None:
        for pergunta in (
            "pode me listar todos os documentos disponiveis pra consulta ?",
            "quero a lista da base de dados",
            "quais documentos você tem para consulta?",
            "quantos documentos estão cadastrados?",
            "o que você pode consultar?",
            "liste os normativos do acervo",
            "mostre os itoms disponíveis",
        ):
            self.assertTrue(_CATALOGO.search(pergunta), pergunta)

    def test_pergunta_de_conteudo_nao_vira_catalogo(self) -> None:
        for pergunta in (
            "quais documentos são exigidos para a SI-PS?",
            "quais documentos devo anexar na solicitação?",
            "qual o prazo de solicitação de SI-PS?",
            "liste as etapas da manobra",
            "quais as responsabilidades da gestão?",
        ):
            self.assertFalse(_CATALOGO.search(pergunta), pergunta)


class LinhaTeste(unittest.TestCase):
    def test_nao_repete_revisao_nem_titulo_igual_ao_codigo(self) -> None:
        doc = {"codigo": "ITOM EGP 01 Rev.02", "revisao": "02", "titulo": "INSTRUÇÃO DE TRABALHO", "secoes": {"1", "2"}}
        self.assertEqual(_linha_documento(doc), "- ITOM EGP 01 Rev.02: INSTRUÇÃO DE TRABALHO · 2 seções")
        igual = {"codigo": "ITOM AGD 01 Rev.00", "revisao": "00", "titulo": "ITOM AGD 01", "secoes": set()}
        self.assertEqual(_linha_documento(igual), "- ITOM AGD 01 Rev.00")
        sem_rev = {"codigo": "NO-01", "revisao": "3", "titulo": "Relatórios", "secoes": set()}
        self.assertEqual(_linha_documento(sem_rev), "- NO-01 (revisão 3): Relatórios")


class CatalogoTeste(unittest.TestCase):
    def _montar(self, raiz: Path):
        acervo = raiz / "acervo"
        acervo.mkdir()
        (acervo / "a.md").write_text(_documento("ITOM TST 01", "Instrução A"), encoding="utf-8")
        (acervo / "b.md").write_text(_documento("ITOM TST 02", "Instrução B", revisao="2"), encoding="utf-8")
        (acervo / "c.md").write_text(_documento("ITOM TST 03", "Instrução C", status="substituído"), encoding="utf-8")
        data = raiz / "data"
        return acervo, data, indexar(acervo, data, embedder=_embedder)

    def _gerador(self):
        def gerador(_mensagens):
            raise AssertionError("pergunta sobre a base não deve chamar o GPT de resposta")

        return gerador

    def test_lista_os_documentos_vigentes_do_indice_sem_chamar_o_gpt(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))
            resultado = responder(
                "pode me listar todos os documentos disponíveis para consulta?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador(),
                embedder=_embedder,
            )
            self.assertEqual(resultado.status, "catalogo")
            self.assertTrue(resultado.coberto)
            self.assertEqual(resultado.tokens_prompt, 0)
            self.assertIn("Documentos disponíveis para consulta (2):", resultado.resposta)
            self.assertIn("ITOM TST 01", resultado.resposta)
            self.assertIn("ITOM TST 02 (revisão 2)", resultado.resposta)
            antes, _, depois = resultado.resposta.partition("Fora de vigência")
            self.assertNotIn("ITOM TST 03", antes)  # fora de vigência não entra na lista principal
            self.assertIn("ITOM TST 03", depois)
            texto = formatar(resultado)
            self.assertIn("Lista montada a partir do acervo indexado.", texto)
            self.assertNotIn("fluxo humano", texto)
            self.assertNotIn("Fontes:", texto)

    def test_planejador_com_tipo_catalogo_cobre_a_continuacao_da_conversa(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))
            plano = {"pergunta": "Liste todos os documentos disponíveis", "tipo": "catalogo", "consultas": []}
            resultado = responder(
                "quero todos em geral, não só os citados",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador(),
                embedder=_embedder,
                historico=[{"pergunta": "liste os documentos", "resposta": "ITOM TST 01"}],
                reescritor=lambda _m: (json.dumps(plano), 10, 2),
            )
            self.assertEqual(resultado.status, "catalogo")
            self.assertEqual(resultado.tokens_aux_prompt, 10)
            self.assertIn("ITOM TST 02", resultado.resposta)

    def test_documento_nomeado_mostra_so_os_dados_dele(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))
            plano = {"pergunta": "Quais os dados do ITOM TST 02?", "tipo": "catalogo", "consultas": []}
            resultado = responder(
                "qual a revisão do ITOM TST 02 nos documentos disponíveis?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador(),
                embedder=_embedder,
                reescritor=lambda _m: (json.dumps(plano), 1, 1),
            )
            self.assertEqual(resultado.status, "catalogo")
            self.assertIn("ITOM TST 02", resultado.resposta)
            self.assertNotIn("ITOM TST 01", resultado.resposta)

    def test_pergunta_de_conteudo_continua_indo_para_a_busca(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))
            chamadas: list[int] = []

            def gerador(_mensagens):
                chamadas.append(1)
                resposta = {"status": "ausente", "resposta": "Não consta.", "citacoes": []}
                return json.dumps(resposta), 5, 2, 0, "stop", None

            resultado = responder(
                "qual o procedimento do ITOM TST 01?", data_dir=data, acervo=acervo, indice=indice, gerador=gerador, embedder=_embedder
            )
            self.assertNotEqual(resultado.status, "catalogo")
            self.assertTrue(chamadas)


if __name__ == "__main__":
    unittest.main()
