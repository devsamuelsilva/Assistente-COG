from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import avaliacao, gpt, visao
from app.buscar import Acerto
from app.chunking import Trecho
from app.extrair import Figura
from app.indice import indexar
from app.rag import _interpretar, _ordem, _pedido, formatar, gerar_openai, responder
from app.uso import custo_usd
from tests.test_rag import _embedder

RAIZ = Path(__file__).resolve().parents[1]


def _acerto(secao: str, texto: str, arquivo: str = "a.pdf") -> Acerto:
    trecho = Trecho("1", f"Seção: {secao}\n{texto}", arquivo, "NO-01", "3", True, "t", secao, "1")
    return Acerto(trecho=trecho, coseno=1.0, bm25=1.0, rrf=1.0)


class InterpretacaoTeste(unittest.TestCase):
    def setUp(self) -> None:
        self.acertos = [
            _acerto("5.1 Prazos", "O prazo é de 15 dias úteis."),
            _acerto("7 Outro", "Texto de outro documento sem valor.", "b.pdf"),
            _acerto("5.2 Extra", "Também vale 30 dias corridos."),
        ]

    def _ler(self, dados: dict, **extra):
        return _interpretar("qual o prazo?", json.dumps(dados), self.acertos, 1, 1, ordem=_ordem(self.acertos), **extra)

    def test_numero_do_trecho_segue_a_ordem_do_pedido_que_agrupa_por_documento(self) -> None:
        # No pedido, os dois trechos de a.pdf vêm juntos: [1] 5.1, [2] 5.2, [3] 7 Outro.
        pedido = _pedido("qual o prazo?", self.acertos)
        self.assertIn("[2] Seção: 5.2 Extra", pedido)
        self.assertIn("[3] Seção: 7 Outro", pedido)
        resultado = self._ler({"status": "coberto", "resposta": "30 dias corridos.", "citacoes": [{"trecho": 2}]})
        self.assertEqual(resultado.citacoes[0]["secao"], "5.2 Extra")
        self.assertEqual(resultado.status, "coberto")

    def test_trecho_inexistente_vira_recusa(self) -> None:
        resultado = self._ler({"status": "coberto", "resposta": "15 dias úteis.", "citacoes": [{"trecho": 9}]})
        self.assertFalse(resultado.coberto)
        self.assertEqual(resultado.status, "ausente")
        self.assertTrue(resultado.resposta.startswith("Esse ponto não está no normativo indexado neste PC."))

    def test_parcial_conta_como_respondida_e_mostra_o_aviso(self) -> None:
        resultado = self._ler(
            {"status": "parcial", "resposta": "15 dias úteis. Falta a regra de urgência.", "citacoes": [{"trecho": 1}]}
        )
        self.assertTrue(resultado.coberto)
        self.assertEqual(resultado.status, "parcial")
        texto = formatar(resultado)
        self.assertIn("Resposta parcial.", texto)
        self.assertIn("Para o que falta, siga o fluxo humano do COG.", texto)

    def test_ausente_guarda_a_explicacao_e_ainda_aponta_as_secoes(self) -> None:
        resultado = self._ler({"status": "ausente", "resposta": "Não informa a tensão.", "citacoes": []})
        self.assertFalse(resultado.coberto)
        self.assertTrue(resultado.resposta.startswith("Não informa a tensão."))
        self.assertIn("Seções relacionadas encontradas", resultado.resposta)

    def test_valor_em_outro_trecho_enviado_vira_citacao_extra(self) -> None:
        resultado = self._ler(
            {"status": "coberto", "resposta": "São 15 dias úteis ou 30 dias corridos.", "citacoes": [{"trecho": 1}]}
        )
        self.assertEqual({citacao["secao"] for citacao in resultado.citacoes}, {"5.1 Prazos", "5.2 Extra"})
        self.assertEqual(resultado.alertas, [])

    def test_valor_que_nao_esta_em_trecho_nenhum_gera_alerta(self) -> None:
        resultado = self._ler({"status": "coberto", "resposta": "O prazo é de 45 dias.", "citacoes": [{"trecho": 1}]})
        self.assertTrue(resultado.coberto)
        self.assertIn("45", resultado.alertas[0])
        self.assertIn("Atenção:", formatar(resultado))

    def test_numero_da_consulta_exata_e_da_pergunta_e_de_lista_nao_geram_alerta(self) -> None:
        exata = "Linha: Alta | Coluna: Preventiva | Valor: 21 dias úteis"
        resultado = self._ler(
            {"status": "coberto", "resposta": "1. Abrir a SI.\n2. Aguardar 21 dias úteis.", "citacoes": [{"trecho": 1}]},
            extra=exata,
        )
        self.assertEqual(resultado.alertas, [])

    def test_formato_antigo_continua_aceito(self) -> None:
        resultado = self._ler(
            {"coberto": True, "resposta": "15 dias úteis.", "citacoes": [{"arquivo": "a.pdf", "secao": "5.1 Prazos"}]}
        )
        self.assertTrue(resultado.coberto)
        self.assertEqual(resultado.status, "coberto")


class ReescritaTeste(unittest.TestCase):
    def _montar(self, raiz: Path):
        acervo = raiz / "acervo"
        acervo.mkdir()
        shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
        data = raiz / "data"
        return acervo, data, indexar(acervo, data, embedder=_embedder)

    def _gerador(self, vistos: list[str]):
        def gerador(mensagens):
            vistos.append(mensagens[1]["content"])
            resposta = json.dumps({"status": "ausente", "resposta": "Não consta.", "citacoes": []})
            return resposta, 7, 2, 0, "stop", None

        return gerador

    def test_pergunta_reescrita_entra_no_pedido_e_no_custo_e_fica_guardada(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))
            chamadas: list[str] = []

            def reescritor(mensagens):
                chamadas.append(mensagens[1]["content"])
                return json.dumps({"pergunta": "qual o valor de teste na revisão seguinte?"}), 10, 3

            vistos: list[str] = []
            historico = [{"pergunta": "qual o valor de teste?", "resposta": "42"}]
            primeira = responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador(vistos),
                embedder=_embedder,
                historico=historico,
                reescritor=reescritor,
            )
            self.assertEqual(len(chamadas), 1)
            self.assertEqual(primeira.pergunta_busca, "qual o valor de teste na revisão seguinte?")
            self.assertIn("Pergunta completa (reescrita a partir da conversa)", vistos[0])
            self.assertEqual(primeira.tokens_aux_prompt, 10)
            self.assertTrue(primeira.modelo_aux)
            # repetir a mesma pergunta não chama o GPT de novo para reescrever
            responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador(vistos),
                embedder=_embedder,
                historico=historico,
                reescritor=reescritor,
            )
            self.assertEqual(len(chamadas), 1)

    def test_sem_conversa_nao_reescreve_e_falha_nao_derruba_a_consulta(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))

            def quebrado(_mensagens):
                raise RuntimeError("fora do ar")

            sem_conversa = responder(
                "qual o valor de teste?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador([]),
                embedder=_embedder,
                reescritor=lambda _m: (json.dumps({"pergunta": "outra coisa", "tipo": "especifica", "consultas": []}), 1, 1),
            )
            # sem conversa, a pergunta que se busca é a original, mesmo que o planejador devolva outra
            self.assertEqual(sem_conversa.pergunta_busca, "")
            com_falha = responder(
                "e na seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=self._gerador([]),
                embedder=_embedder,
                historico=[{"pergunta": "qual o valor de teste?", "resposta": "42"}],
                reescritor=quebrado,
            )
            self.assertEqual(com_falha.pergunta_busca, "")
            self.assertEqual(com_falha.status, "ausente")

    def test_status_e_alertas_vao_para_o_cache_e_para_o_registro(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            acervo, data, indice = self._montar(Path(temporario))

            def gerador(_mensagens):
                resposta = json.dumps({"status": "parcial", "resposta": "Vale 99 unidades.", "citacoes": [{"trecho": 1}]})
                return resposta, 5, 2, 0, "stop", None

            primeira = responder(
                "qual o valor de teste?", data_dir=data, acervo=acervo, indice=indice, gerador=gerador, embedder=_embedder
            )
            segunda = responder(
                "qual o valor de teste?", data_dir=data, acervo=acervo, indice=indice, gerador=gerador, embedder=_embedder
            )
            self.assertTrue(segunda.cache)
            self.assertEqual(segunda.status, primeira.status)
            self.assertEqual(segunda.alertas, primeira.alertas)
            self.assertTrue(primeira.alertas)
            registro = (data / "logs" / "consultas.jsonl").read_text(encoding="utf-8")
            self.assertIn('"status": "parcial"', registro)


class OpenAITeste(unittest.TestCase):
    class _Completions:
        def __init__(self, recusar: tuple[str, ...]) -> None:
            self.recusar = recusar
            self.formatos: list[str | None] = []
            self.logprobs: list[bool] = []

        def create(self, **argumentos):
            formato = (argumentos.get("response_format") or {}).get("type")
            self.formatos.append(formato)
            self.logprobs.append(bool(argumentos.get("logprobs")))
            if formato in self.recusar:
                raise RuntimeError(f"unsupported response_format {formato}")
            mensagem = SimpleNamespace(content='{"ok": true}')
            escolha = SimpleNamespace(message=mensagem, finish_reason="stop", logprobs=None)
            return SimpleNamespace(choices=[escolha], usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2))

    def _api(self, recusar: tuple[str, ...] = ()):
        completions = self._Completions(recusar)
        return completions, SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def test_esquema_e_o_primeiro_formato(self) -> None:
        completions, api = self._api()
        with patch.object(gpt, "cliente", return_value=api):
            saida = gerar_openai([{"role": "user", "content": "x"}], "m", {"type": "object"})
        self.assertEqual(completions.formatos, ["json_schema"])
        self.assertEqual((saida.texto, saida.tokens_prompt, saida.tokens_resposta), ('{"ok": true}', 5, 2))

    def test_modelo_sem_json_schema_cai_para_json_object_e_depois_texto(self) -> None:
        completions, api = self._api(recusar=("json_schema", "json_object"))
        with patch.object(gpt, "cliente", return_value=api):
            gerar_openai([{"role": "user", "content": "x"}], "m", {"type": "object"})
        self.assertEqual(completions.formatos, ["json_schema", "json_object", None])

    def test_sem_esquema_nao_usa_json_schema(self) -> None:
        completions, api = self._api()
        with patch.object(gpt, "cliente", return_value=api):
            gerar_openai([{"role": "user", "content": "x"}], "m")
        self.assertEqual(completions.formatos, ["json_object"])

    def test_erro_que_nao_e_de_formato_sobe(self) -> None:
        class Falha:
            def create(self, **_):
                raise RuntimeError("401 chave inválida")

        with patch.object(gpt, "cliente", return_value=SimpleNamespace(chat=SimpleNamespace(completions=Falha()))):
            with self.assertRaises(RuntimeError):
                gerar_openai([], "m", {"type": "object"})


class AvaliacaoTeste(unittest.TestCase):
    def test_comparacao_acha_regressao_e_a_porta_reprova(self) -> None:
        anterior = {"id": "r1", "itens": [{"id": "a", "status": "acerto"}, {"id": "b", "status": "erro"}]}
        agora = [{"id": "a", "status": "erro"}, {"id": "b", "status": "acerto"}]
        comparacao = avaliacao.comparar(anterior, agora)
        self.assertEqual(comparacao["regressoes"], ["a"])
        self.assertEqual(comparacao["melhorias"], ["b"])
        rodada = {"resumo": {"acertividade": 90.0, "invencoes": 0}, "comparacao": comparacao}
        ok, motivos = avaliacao.aprovada(rodada, 80.0)
        self.assertFalse(ok)
        self.assertIn("pioraram", motivos[0])
        self.assertTrue(avaliacao.aprovada(rodada, 80.0, permitir_regressao=True)[0])
        com_invencao = {"resumo": {"acertividade": 90.0, "invencoes": 1}, "comparacao": None}
        self.assertFalse(avaliacao.aprovada(com_invencao, 80.0)[0])
        self.assertIsNone(avaliacao.comparar(None, agora))

    def test_metricas_de_busca_separam_recall_e_posicao(self) -> None:
        resultados = [
            {"secao": "5.1", "deve_recusar": False, "status": "acerto", "posicao_recuperada": 1},
            {"secao": "5.2", "deve_recusar": False, "status": "erro", "posicao_recuperada": 4},
            {"secao": "5.3", "deve_recusar": False, "status": "recusa_indevida", "posicao_recuperada": None},
            {"secao": "", "deve_recusar": True, "status": "acerto", "posicao_recuperada": None},
        ]
        metricas = avaliacao._metricas_busca(resultados)
        self.assertEqual(metricas["recall_busca"], 66.7)
        self.assertEqual(metricas["mrr_busca"], round((1 + 0.25) / 3, 3))
        self.assertEqual(avaliacao._metricas_busca([])["recall_busca"], None)

    def test_posicao_da_secao_esperada_entre_os_trechos_enviados(self) -> None:
        trechos = [
            Trecho("1", "Seção: 2 Outro\nx", "a.pdf", "N", "1", True, "t", "2 Outro", "1"),
            Trecho("2", "Seção: 5.1.1 Prazos\ny", "a.pdf", "N", "1", True, "t", "5.1.1 Prazos", "1"),
        ]
        indice = SimpleNamespace(trechos=trechos)
        enviados = [{"arquivo": "a.pdf", "secao": "2 Outro"}, {"arquivo": "a.pdf", "secao": "5.1.1 Prazos"}]
        self.assertEqual(avaliacao._posicao_secao("5.1", indice, enviados), 2)
        self.assertIsNone(avaliacao._posicao_secao("9", indice, enviados))
        self.assertIsNone(avaliacao._posicao_secao("", indice, enviados))


class VisaoTeste(unittest.TestCase):
    def test_pagina_sem_texto_usa_o_pedido_de_ocr(self) -> None:
        pedidos: list[str] = []

        class Completions:
            def create(self, **argumentos):
                pedidos.append(argumentos["messages"][0]["content"][0]["text"])
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="texto"))])

        api = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        with patch.object(gpt, "cliente", return_value=api):
            visao.gerar_openai(Figura("a.pdf", 1, "h", b"png", "image/png", sem_texto=True))
            visao.gerar_openai(Figura("a.pdf", 2, "h", b"png", "image/png"))
        self.assertIn("digitalizada", pedidos[0])
        self.assertIn("Transcreva esta figura", pedidos[1])

    def test_listar_e_revisar_transcricoes(self) -> None:
        with tempfile.TemporaryDirectory() as temporario:
            data = Path(temporario)
            figura = Figura("a.pdf", 3, "0" * 64, b"png", "image/png")
            visao.transcrever_figuras(data, [figura], gerador=lambda _f: "Linha: A | Coluna: B | Valor: 1")
            itens = visao.listar(data)
            self.assertEqual([item["revisado"] for item in itens], [False])
            visao.revisar(data, "0" * 64, "Linha: A | Coluna: B | Valor: 2")
            itens = visao.listar(data)
            self.assertTrue(itens[0]["revisado"])
            self.assertIn("Valor: 2", itens[0]["texto"])
            with self.assertRaises(FileNotFoundError):
                visao.revisar(data, "1" * 64)
            with self.assertRaises(FileNotFoundError):
                visao.revisar(data, "../../x")


class UsoTeste(unittest.TestCase):
    def test_tabela_conhece_os_modelos_novos(self) -> None:
        self.assertEqual(custo_usd("gpt-4.1", 1_000_000, 0), 2.0)
        self.assertEqual(custo_usd("gpt-4o", 0, 1_000_000), 10.0)
        self.assertIsNone(custo_usd("modelo-desconhecido", 10, 10))


if __name__ == "__main__":
    unittest.main()


class PerguntaGeralTeste(unittest.TestCase):
    """Caso real: "qual a finalidade da ITOM EGP 05?" trazia só o glossário e o assistente recusava."""

    def test_detecta_pergunta_sobre_o_documento_inteiro(self) -> None:
        from app.rag import _GERAL

        for pergunta in (
            "Qual a finalidade da ITOM EGP 05 ?",
            "oque pode me dizer sobre ela ?",
            "o que você pode me dizer sobre a ITOM 01?",
            "qual o objetivo desse normativo",
            "faça um resumo da AGD 01",
            "do que trata a ITOM EGP 01?",
        ):
            self.assertTrue(_GERAL.search(pergunta), pergunta)
        for pergunta in ("qual o prazo de solicitação de SI?", "quem aprova a manobra?", "qual a tensão da barra 1?"):
            self.assertFalse(_GERAL.search(pergunta), pergunta)

    def test_visao_geral_e_objetivo_vao_na_frente_so_do_documento_citado(self) -> None:
        from app.rag import _com_abertura

        def trecho(arquivo: str, secao: str) -> Trecho:
            return Trecho(secao, f"Seção: {secao}\ntexto", arquivo, "C", "0", True, "t", secao, "1")

        vigentes = [
            trecho("a.pdf", "Visão geral do documento"),
            trecho("a.pdf", "2 OBJETIVO"),
            trecho("a.pdf", "Anexo V GLOSSÁRIO (parte 1)"),
            trecho("b.pdf", "2 OBJETIVO"),
        ]
        achados = [Acerto(trecho=vigentes[2], coseno=0.3, bm25=2.0, rrf=0.1)]
        resultado = _com_abertura(vigentes, achados, {"a.pdf"})
        self.assertEqual(
            [acerto.trecho.secao for acerto in resultado], ["Visão geral do documento", "2 OBJETIVO", "Anexo V GLOSSÁRIO (parte 1)"]
        )
        # nada duplicado quando a busca já trouxe o objetivo
        de_novo = _com_abertura(vigentes, resultado, {"a.pdf"})
        self.assertEqual(len(de_novo), 3)

    def test_finalidade_puxa_objetivo_na_busca_por_palavras(self) -> None:
        from app.buscar import expandir

        self.assertIn("objetivo", expandir("qual a finalidade da instrução"))
