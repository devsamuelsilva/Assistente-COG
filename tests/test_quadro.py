from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.buscar import buscar
from app.chunking import Trecho
from app.indice import Indice, embutir_pergunta, prefixos
from app.quadro import consulta_exata, consultar, quadros
from app.rag import _assinatura_resposta, estrategia, responder

_LINHAS = [
    "Complexidade Alta – Com Desligamento (desligamento de LT)",
    "Complexidade Alta – Sem Desligamento (sem desligamento de LT)",
    "Complexidade Média – Com Desligamento (circuito 34,5 kV)",
]
_COLUNAS = {
    "Corretiva Programada (manutenção corretiva)": ("15 dias úteis", "15 dias úteis", "7 dias úteis"),
    "Corretiva de Urgência (urgência)": ("Inferior a 2 dias úteis",) * 3,
    "Preventiva Programada (cronograma)": ("15 dias úteis", "15 dias úteis", "7 dias úteis"),
}


def _trecho(numero: int, secao: str, texto: str, arquivo: str = "itom.pdf") -> Trecho:
    return Trecho(
        id=f"{arquivo}:{numero}",
        texto=f"Seção: {secao}\n{texto}",
        arquivo=arquivo,
        codigo="ITOM-01",
        revisao="02",
        vigente=True,
        titulo="ITOM",
        secao=secao,
        pagina="11",
    )


def _celulas() -> list[str]:
    return [
        f"Linha: {linha} | Coluna: {coluna} | Valor: {valores[posicao]}"
        for coluna, valores in _COLUNAS.items()
        for posicao, linha in enumerate(_LINHAS)
    ]


def _indice() -> Indice:
    celulas = _celulas()
    trechos = [
        _trecho(1, "5.1.1 SOLICITAÇÃO (parte 1)", "Prazos no quadro a seguir.\n" + "\n".join(celulas[:6])),
        _trecho(2, "5.1.1 SOLICITAÇÃO (parte 2)", "\n".join(celulas[4:])),
        _trecho(3, "8.1 EMISSÃO DA ATEIE", "A ATEIE é emitida pelo COG."),
        _trecho(4, "Anexo 01 SI-PS", "Formulário da SI-PS.", arquivo="outro.pdf"),
    ]
    vetores = np.eye(len(trechos), 8, dtype=np.float32)
    return Indice(modelo="teste", fingerprint="x", trechos=trechos, vetores=vetores)


class QuadroTeste(unittest.TestCase):
    def test_celulas_repetidas_entre_partes_contam_uma_vez(self) -> None:
        encontrados = quadros(_indice().trechos)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].secao, "5.1.1 SOLICITAÇÃO")
        self.assertEqual(len(encontrados[0].celulas), 9)

    def test_escolhe_a_celula_pela_linha_e_pela_coluna(self) -> None:
        quadro = quadros(_indice().trechos)[0]
        celulas = consultar(quadro, "Prazo da corretiva programada de complexidade média com desligamento?")
        self.assertEqual([(c.linha[:20], c.valor) for c in celulas], [("Complexidade Média –", "7 dias úteis")])
        urgencia = consultar(quadro, "Qual o prazo de uma corretiva de urgência?")
        self.assertEqual({c.valor for c in urgencia}, {"Inferior a 2 dias úteis"})
        self.assertEqual(len(urgencia), 3)
        self.assertEqual(consultar(quadro, "Quem emite a ATEIE?"), [])

    def test_quadro_fora_do_contexto_nao_entra(self) -> None:
        indice = _indice()
        pergunta = "Prazo da preventiva programada de complexidade alta sem desligamento?"
        self.assertEqual(consulta_exata(indice.trechos, [indice.trechos[2]], pergunta), "")
        bloco = consulta_exata(indice.trechos, [indice.trechos[1]], pergunta)
        self.assertIn("secao=5.1.1 SOLICITAÇÃO", bloco)
        self.assertEqual(bloco.count("Linha:"), 1)
        self.assertIn("Preventiva Programada (cronograma) | Valor: 15 dias úteis", bloco)

    def test_pedido_leva_a_consulta_exata(self) -> None:
        indice = _indice()
        vistos: list[str] = []

        def gerador(mensagens: list[dict]) -> tuple[str, int, int]:
            vistos.append(mensagens[1]["content"])
            resposta = {
                "coberto": True,
                "resposta": "7 dias úteis.",
                "citacoes": [{"arquivo": "itom.pdf", "secao": "5.1.1 SOLICITAÇÃO"}],
            }
            return json.dumps(resposta), 10, 3

        with TemporaryDirectory() as temporario:
            resultado = responder(
                "Qual o prazo da corretiva programada de complexidade média com desligamento?",
                data_dir=Path(temporario),
                acervo=Path(temporario) / "sem-acervo",
                indice=indice,
                gerador=gerador,
                embedder=lambda textos: [np.eye(1, 8, 0, dtype=np.float32)[0] for _ in textos],
                modo="trechos",
                reranker="",
            )
        self.assertTrue(resultado.coberto)
        self.assertIn("Consulta exata ao quadro:\narquivo=itom.pdf secao=5.1.1 SOLICITAÇÃO", vistos[0])
        exata = vistos[0].split("Consulta exata ao quadro:")[1].split("Trechos:")[0]
        self.assertEqual(exata.count("Linha:"), 1)
        self.assertIn("Valor: 7 dias úteis", exata)


class ModosTeste(unittest.TestCase):
    def test_secao_traz_todas_as_partes_e_documento_traz_o_arquivo(self) -> None:
        indice = _indice()
        consulta = np.eye(1, 8, 1, dtype=np.float32)[0]
        trechos = buscar(indice, "zzz", consulta, top_k=1, modo="trechos")
        self.assertEqual(trechos[0].trecho.secao, "5.1.1 SOLICITAÇÃO (parte 2)")
        secao = buscar(indice, "zzz", consulta, top_k=1, modo="secao")
        self.assertEqual([a.trecho.secao for a in secao], ["5.1.1 SOLICITAÇÃO (parte 1)", "5.1.1 SOLICITAÇÃO (parte 2)"])
        documento = buscar(indice, "zzz", consulta, top_k=1, modo="documento")
        self.assertEqual({a.trecho.arquivo for a in documento}, {"itom.pdf"})
        self.assertEqual(len(documento), 3)

    def test_reordenador_decide_a_ordem_dos_candidatos(self) -> None:
        indice = _indice()
        consulta = np.eye(1, 8, 0, dtype=np.float32)[0]

        def reordenador(pergunta: str, textos: list[str]) -> list[float]:
            return [10.0 if "ATEIE" in texto else 0.0 for texto in textos]

        acertos = buscar(indice, "quem emite", consulta, top_k=1, reordenador=reordenador, candidatos=4)
        self.assertEqual(acertos[0].trecho.secao, "8.1 EMISSÃO DA ATEIE")
        self.assertEqual(acertos[0].rerank, 10.0)

    def test_modo_padrao_nao_muda_a_assinatura(self) -> None:
        indice = _indice()
        self.assertEqual(_assinatura_resposta(indice), _assinatura_resposta(indice, "trechos", ""))
        self.assertNotEqual(_assinatura_resposta(indice), _assinatura_resposta(indice, "secao", ""))
        self.assertNotEqual(_assinatura_resposta(indice), _assinatura_resposta(indice, "trechos", "modelo"))
        with self.assertRaises(ValueError):
            estrategia("inteiro", "")
        with self.assertRaises(ValueError):
            estrategia("trechos", "alguem/modelo-qualquer")
        self.assertEqual(estrategia("secao", "jinaai/jina-reranker-v2-base-multilingual")[0], "secao")

    def test_prefixos_por_modelo(self) -> None:
        self.assertEqual(prefixos("intfloat/multilingual-e5-large"), ("query: ", "passage: "))
        self.assertEqual(prefixos("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"), ("", ""))
        self.assertTrue(callable(embutir_pergunta))


if __name__ == "__main__":
    unittest.main()
