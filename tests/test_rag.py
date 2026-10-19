from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path

import numpy as np

from app.indice import carregar, fingerprint_acervo, indexar
from app.rag import formatar, responder

RAIZ = Path(__file__).resolve().parents[1]


def _vetor(texto: str) -> np.ndarray:
    digest = hashlib.sha256(texto.casefold().encode("utf-8")).digest()
    base = np.array([b / 255 for b in digest[:8]], dtype=np.float32)
    if "42" in texto or "valor de teste" in texto.casefold():
        return np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32)
    return base


def _embedder(textos: list[str]) -> list[np.ndarray]:
    return [_vetor(texto) for texto in textos]


class RagTeste(unittest.TestCase):
    def test_resposta_cita_secao_e_recusa_citacao_inventada(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            self.assertGreaterEqual(len(indice.trechos), 3)
            recarregado = carregar(data)
            self.assertEqual(len(recarregado.trechos), len(indice.trechos))
            self.assertIs(carregar(data), recarregado)
            primeiro_par = sorted(caminho.name for caminho in (data / "indice").glob("vetores*.npy"))
            indexar(acervo, data, embedder=_embedder)
            indexar(acervo, data, embedder=_embedder)
            pares = sorted(caminho.name for caminho in (data / "indice").glob("vetores*.npy"))
            self.assertEqual(len(pares), 2)
            self.assertFalse(set(primeiro_par) & set(pares))
            self.assertIsNot(carregar(data), recarregado)

            def gerador(mensagens: list[dict]) -> tuple[str, int, int]:
                pedido = mensagens[1]["content"]
                self.assertIn("42 unidades de exemplo", pedido)
                self.assertNotIn("OPENAI", pedido)
                return (
                    json.dumps(
                        {
                            "coberto": True,
                            "resposta": "O valor de teste é 42 unidades de exemplo.",
                            "citacoes": [
                                {
                                    "arquivo": "exemplo-normativo.md",
                                    "codigo": "IT-EXEMPLO-001",
                                    "revisao": "0",
                                    "secao": "2. Valor de teste",
                                },
                                {"arquivo": "outro.md", "secao": "9. Inventada"},
                            ],
                        }
                    ),
                    11,
                    7,
                    5,
                    "stop",
                    -0.02,
                )

            resultado = responder(
                "qual o valor de teste?",
                autor="operador",
                origem="teste",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=gerador,
                embedder=_embedder,
            )
            self.assertTrue(resultado.coberto)
            self.assertEqual(len(resultado.citacoes), 1)
            self.assertEqual(resultado.citacoes[0]["secao"], "2. Valor de teste")
            self.assertEqual(resultado.sinais["logprob"], -0.02)
            self.assertEqual(resultado.sinais["citacoes"], 1)
            self.assertEqual(resultado.sinais["cit_posicao"], 0)
            self.assertFalse(resultado.sinais["doc_citado"])
            self.assertEqual(resultado.confianca, {})
            texto = formatar(resultado, autor="operador")
            self.assertIn("42 unidades de exemplo", texto)
            self.assertIn("IT-EXEMPLO-001, revisão 0, seção 2. Valor de teste", texto)
            self.assertIn("responsabilidade do operador", texto)
            self.assertNotIn("Inventada", texto)

            segunda = responder(
                "qual o valor de teste?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=lambda _mensagens: (_ for _ in ()).throw(AssertionError("cache nao usado")),
                embedder=_embedder,
            )
            self.assertTrue(segunda.cache)
            self.assertEqual(segunda.tokens_prompt, 11)
            self.assertEqual(segunda.tokens_cache, 5)
            self.assertEqual(segunda.ms_gpt, 0)
            self.assertEqual(resultado.tokens_cache, 5)
            registros = [
                json.loads(linha)
                for linha in (data / "logs" / "consultas.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            for campo in ("tokens_cache", "duracao_ms", "ms_busca", "ms_gpt"):
                self.assertIn(campo, registros[0])
            self.assertEqual(registros[0]["tokens_cache"], 5)

            antes = fingerprint_acervo(acervo)
            (acervo / "exemplo-normativo.md.meta.json").write_text('{"vigente": true}', encoding="utf-8")
            self.assertNotEqual(fingerprint_acervo(acervo), antes)

    def test_complemento_usa_a_conversa_e_nao_repete_o_cache(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            vistos: list[str] = []

            def gerador(mensagens: list[dict]) -> tuple[str, int, int]:
                vistos.append(mensagens[1]["content"])
                return json.dumps({"coberto": False, "resposta": "parcial", "citacoes": []}), 4, 2

            historico = [{"pergunta": "qual o valor de teste?", "resposta": "42 unidades de exemplo."}]
            responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=gerador,
                embedder=_embedder,
                historico=historico,
            )
            self.assertEqual(len(vistos), 1)
            self.assertIn("Conversa deste chat:", vistos[0])
            self.assertIn("qual o valor de teste?", vistos[0])
            self.assertIn("Pergunta atual:\ne na revisão seguinte?", vistos[0])

            repetida = responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=lambda _mensagens: (_ for _ in ()).throw(AssertionError("cache nao usado")),
                embedder=_embedder,
                historico=historico,
            )
            self.assertTrue(repetida.cache)

            def gerador_novo(mensagens: list[dict]) -> tuple[str, int, int]:
                self.assertNotIn("qual o valor de teste?", mensagens[1]["content"])
                return json.dumps({"coberto": False, "resposta": "sozinha", "citacoes": []}), 4, 2

            isolada = responder(
                "e na revisão seguinte?",
                data_dir=data,
                acervo=acervo,
                indice=indice,
                gerador=gerador_novo,
                embedder=_embedder,
            )
            self.assertFalse(isolada.cache)
            self.assertTrue(isolada.resposta.startswith("Esse ponto não está no normativo indexado neste PC.\nSeções relacionadas"))

    def test_falha_do_gpt_nao_vai_para_o_cache_e_fora_do_escopo_nem_chama(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            indice = indexar(acervo, data, embedder=_embedder)
            chamadas: list[str] = []

            def gerador(saida):
                def gerar(mensagens: list[dict]):
                    chamadas.append(mensagens[1]["content"])
                    return saida

                return gerar

            argumentos = {"data_dir": data, "acervo": acervo, "indice": indice, "embedder": _embedder}
            for saida in (("não é json", 10, 5), ('{"coberto": true', 10, 800, 0, "length")):
                primeira = responder("qual o valor de teste?", gerador=gerador(saida), **argumentos)
                segunda = responder("qual o valor de teste?", gerador=gerador(saida), **argumentos)
                self.assertFalse(primeira.coberto)
                self.assertFalse(segunda.cache)
            self.assertEqual(len(chamadas), 4)

            fora = responder("oi, tudo bem?", gerador=gerador(("{}", 1, 1)), **argumentos)
            self.assertEqual(len(chamadas), 4)
            self.assertFalse(fora.coberto)


class PreparacaoTeste(unittest.TestCase):
    def test_indexacao_gera_visao_geral_e_perguntas_e_reaproveita_o_cache(self) -> None:
        from tempfile import TemporaryDirectory

        from app.ficha import SECAO

        fichas: list[str] = []
        perguntas: list[str] = []

        def redator(texto: str) -> str:
            fichas.append(texto)
            return json.dumps({"titulo": "Exemplo de teste", "objetivo": "Testar o assistente.", "pontos": [{"secao": "2. Valor de teste", "resumo": "Valor usado nos testes."}]})

        def perguntador(texto: str) -> str:
            perguntas.append(texto)
            return json.dumps({"perguntas": ["quanto vale o teste?", "qual o número do exemplo?"]})

        def falha(_texto: str) -> str:
            raise RuntimeError("sem chave")

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            acervo = raiz / "acervo"
            acervo.mkdir()
            shutil.copy(RAIZ / "tests" / "dados" / "exemplo-normativo.md", acervo / "exemplo-normativo.md")
            data = raiz / "data"
            avisos: list[str] = []
            indice = indexar(acervo, data, embedder=_embedder, redator=redator, perguntador=perguntador, avisos=avisos)
            self.assertEqual(avisos, [])
            visao = next(trecho for trecho in indice.trechos if trecho.secao == SECAO)
            self.assertIn("Resumo gerado automaticamente", visao.texto)
            self.assertIn("Objetivo: Testar o assistente.", visao.texto)
            self.assertIn("- 2. Valor de teste: Valor usado nos testes.", visao.texto)
            self.assertIn("Quais os principais pontos do IT-EXEMPLO-001?", visao.perguntas)
            self.assertTrue(all(trecho.perguntas for trecho in indice.trechos))
            self.assertEqual(len(perguntas), len(indice.trechos) - 1)
            recarregado = carregar(data)
            self.assertEqual(recarregado.vetores_perguntas.shape, recarregado.vetores.shape)
            self.assertEqual(recarregado.trechos[0].perguntas, ["quanto vale o teste?", "qual o número do exemplo?"])

            indexar(acervo, data, embedder=_embedder, redator=falha, perguntador=falha, avisos=avisos)
            self.assertEqual(avisos, [])
            self.assertEqual(len(fichas), 1)

            outro = raiz / "outro"
            sem_gpt = indexar(acervo, outro, embedder=_embedder, redator=falha, perguntador=falha, avisos=avisos)
            self.assertEqual(len(avisos), 2)
            self.assertIn("visão geral não gerada (sem chave)", avisos[0])
            self.assertIn("Perguntas de busca não geradas", avisos[1])
            self.assertIsNone(sem_gpt.vetores_perguntas)
            self.assertFalse(any(trecho.secao == SECAO for trecho in sem_gpt.trechos))

    def test_busca_usa_o_vetor_das_perguntas(self) -> None:
        from app.buscar import buscar
        from app.chunking import Trecho
        from app.indice import Indice

        trechos = [
            Trecho("1", "Seção: 1 Alfa\nprimeiro texto", "a.pdf", "IT", "0", True, "t", "1 Alfa", "1"),
            Trecho("2", "Seção: 2 Beta\nsegundo texto", "a.pdf", "IT", "0", True, "t", "2 Beta", "1", ["como faço beta?"]),
        ]
        vetores = np.array([[0.5, 0.5, 0, 0], [0, 0, 0.5, 0.5]], dtype=np.float32)
        perguntas = np.array([[0, 0, 0, 0], [1, 0, 0, 0]], dtype=np.float32)
        indice = Indice("teste", "x", trechos, vetores, perguntas)
        acertos = buscar(indice, "texto", np.array([1, 0, 0, 0], dtype=np.float32), top_k=2)
        self.assertEqual(acertos[0].trecho.secao, "2 Beta")
        self.assertAlmostEqual(acertos[0].coseno, 1.0, places=5)


class EscopoTeste(unittest.TestCase):
    def _trechos(self):
        from app.chunking import Trecho

        def trecho(arquivo: str, codigo: str, secao: str, texto: str) -> Trecho:
            return Trecho(secao, f"Documento: {codigo}\nSeção: {secao}\n{texto}", arquivo, codigo, "0", True, "t", secao, "1")

        return [
            trecho("agd.pdf", "ITOM AGD 01 Rev.00", "Sumário (parte 1)", "manobras manobras manobras"),
            trecho("agd.pdf", "ITOM AGD 01 Rev.00", "7.4 SECCIONADORAS", "restrição a manobras em carga"),
            trecho("egp.pdf", "ITOM EGP 01 Rev.02 (1)", "5 SI-PS", "manobras da SI-PS"),
            trecho("no.pdf", "NO-01", "2 RAI", "relatório de análise"),
        ]

    def test_codigo_completo_parcial_colado_e_ambiguo(self) -> None:
        from app.buscar import escopo

        trechos = self._trechos()
        self.assertEqual(escopo("manobras contidas na ITOM AGD 01", trechos), ({"agd.pdf"}, "manobras contidas na"))
        self.assertEqual(escopo("o que diz a AGD-01?", trechos)[0], {"agd.pdf"})
        self.assertEqual(escopo("o que diz a agd01 sobre tensão", trechos), ({"agd.pdf"}, "o que diz a sobre tensão"))
        self.assertEqual(escopo("objetivo da ITOM 01", trechos)[0], {"agd.pdf", "egp.pdf"})
        self.assertEqual(escopo("o que é o RAI na NO-01", trechos), ({"no.pdf"}, "o que é o RAI na"))
        self.assertEqual(escopo("operar no 1 disjuntor", trechos), (set(), "operar no 1 disjuntor"))
        self.assertEqual(escopo("qual o prazo da SI-PS?", trechos), (set(), "qual o prazo da SI-PS?"))
        self.assertEqual(escopo("ITOM AGD 01", trechos), ({"agd.pdf"}, "ITOM AGD 01"))

    def test_busca_fica_no_documento_citado_e_ignora_o_sumario(self) -> None:
        from app.buscar import buscar
        from app.indice import Indice

        trechos = self._trechos()
        vetores = np.ones((len(trechos), 4), dtype=np.float32)
        indice = Indice("teste", "x", trechos, vetores)
        consulta = np.ones(4, dtype=np.float32)
        todos = buscar(indice, "manobras", consulta, top_k=4)
        self.assertNotIn("Sumário (parte 1)", [acerto.trecho.secao for acerto in todos])
        so_agd = buscar(indice, "manobras", consulta, top_k=4, arquivos={"agd.pdf"})
        self.assertEqual([acerto.trecho.secao for acerto in so_agd], ["7.4 SECCIONADORAS"])

    def test_recusa_aponta_as_secoes_relacionadas(self) -> None:
        from app.buscar import Acerto
        from app.rag import _interpretar

        trechos = self._trechos()
        acertos = [Acerto(trecho=trecho, coseno=1.0, bm25=1.0, rrf=1.0) for trecho in trechos]
        repetida = {"arquivo": "agd.pdf", "secao": "7.4 SECCIONADORAS"}
        coberto = _interpretar("seccionadora?", json.dumps({"coberto": True, "resposta": "sim", "citacoes": [repetida, repetida]}), acertos, 1, 1)
        self.assertEqual(len(coberto.citacoes), 1)
        resultado = _interpretar("manobras?", json.dumps({"coberto": False, "resposta": "não há", "citacoes": []}), acertos, 1, 1)
        self.assertFalse(resultado.coberto)
        self.assertEqual(
            resultado.resposta,
            "Esse ponto não está no normativo indexado neste PC.\n"
            "Seções relacionadas encontradas, para conferir no normativo: "
            "7.4 SECCIONADORAS (ITOM AGD 01 Rev.00); 5 SI-PS (ITOM EGP 01 Rev.02); 2 RAI (NO-01).",
        )


class PedidoTeste(unittest.TestCase):
    def test_linha_documento_vira_titulo_no_cabecalho_do_grupo(self) -> None:
        from app.buscar import Acerto
        from app.chunking import Trecho
        from app.rag import _pedido

        texto = "Documento: NO-01 – Pós-Operação\nSeção: 2 RAI\nDentro de: 1 Geral\nO RAI é emitido pela pós-operação."
        trecho = Trecho("1", texto, "a.pdf", "NO-01", "3", True, "t", "2 RAI", "1")
        pedido = _pedido("o que é o RAI?", [Acerto(trecho=trecho, coseno=1.0, bm25=1.0, rrf=1.0)])
        self.assertIn("Documento: arquivo=a.pdf codigo=NO-01 revisao=3 titulo=Pós-Operação", pedido)
        self.assertIn("[1] Seção: 2 RAI\nDentro de: 1 Geral\nO RAI é emitido", pedido)
        self.assertNotIn("Documento: NO-01", pedido)

    def test_trechos_primeiro_um_cabecalho_por_documento_sem_sobreposicao(self) -> None:
        from app.buscar import Acerto
        from app.chunking import Trecho
        from app.rag import _pedido

        repetido = "texto que aparece no fim da parte um e no começo da parte dois"

        def acerto(secao: str, texto: str, arquivo: str = "a.pdf") -> Acerto:
            trecho = Trecho("1", f"Seção: {secao}\n{texto}", arquivo, "NO-01", "3", True, "t", secao, "1")
            return Acerto(trecho=trecho, coseno=1.0, bm25=1.0, rrf=1.0)

        acertos = [
            acerto("5 Quadro (parte 1)", f"início da parte um {repetido}"),
            acerto("7 Outro", "seção de outro documento", arquivo="b.pdf"),
            acerto("5 Quadro (parte 2)", f"{repetido} e o resto da parte dois\nLinha: Alta | Coluna: X | Valor: 5 dias"),
        ]
        historico = [("pergunta anterior", "resposta " + "longa " * 100)]
        exata = "arquivo=a.pdf secao=5 Quadro\nLinha: Alta | Coluna: X | Valor: 5 dias"
        pedido = _pedido("qual o prazo?", acertos, historico, exata)

        self.assertTrue(pedido.startswith("Trechos:"))
        self.assertTrue(pedido.endswith("Pergunta atual:\nqual o prazo?"))
        self.assertEqual(pedido.count("Documento: arquivo=a.pdf codigo=NO-01 revisao=3"), 1)
        self.assertLess(pedido.index("5 Quadro (parte 2)"), pedido.index("Documento: arquivo=b.pdf"))
        self.assertEqual(pedido.count(repetido), 1)
        self.assertIn("Seção: 5 Quadro (parte 2)\ne o resto da parte dois", pedido)
        self.assertEqual(pedido.count("Valor: 5 dias"), 1)
        self.assertLess(pedido.index("Conversa deste chat:"), pedido.index("Consulta exata ao quadro:"))
        self.assertLess(len(pedido.split("Assistente: ")[1].split("\n\n")[0]), 310)


if __name__ == "__main__":
    unittest.main()
