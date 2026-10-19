from __future__ import annotations

import json
import unittest

from app.buscar import Acerto
from app.chunking import Trecho
from app.rag import _SISTEMA_V2, _interpretar, _sem_referencias


class SemReferenciasTeste(unittest.TestCase):
    def test_tira_a_numeracao_interna_dos_trechos(self) -> None:
        texto = "conforme quadro transcrito nos trechos [3] a [11]. Além disso, o trecho [1] complementa. O trecho [12] informa Y."
        self.assertEqual(
            _sem_referencias(texto),
            "conforme quadro transcrito no documento. Além disso, o documento complementa. O documento informa Y.",
        )

    def test_referencia_solta_some_sem_deixar_espaco_antes_da_pontuacao(self) -> None:
        self.assertEqual(_sem_referencias("Vale 15 dias [2]."), "Vale 15 dias.")
        self.assertEqual(_sem_referencias("Vale 15 dias [2], conforme o item 5.1.1."), "Vale 15 dias, conforme o item 5.1.1.")

    def test_nao_mexe_em_numero_de_item_nem_em_texto_sem_referencia(self) -> None:
        texto = "Prazo de 2 dias úteis, seção 5.1.1, item [a] e anexo 02."
        self.assertEqual(_sem_referencias(texto), texto)

    def test_interpretar_entrega_a_resposta_limpa_e_sem_alerta_falso(self) -> None:
        trecho = Trecho("1", "Seção: 5.1.1 Prazos\nO prazo é de 15 dias úteis.", "a.pdf", "C", "0", True, "t", "5.1.1 Prazos", "1")
        acertos = [Acerto(trecho=trecho, coseno=1, bm25=1, rrf=1)]
        bruto = json.dumps(
            {"status": "coberto", "resposta": "O prazo é de 15 dias úteis, conforme o trecho [1].", "citacoes": [{"trecho": 1}]}
        )
        resultado = _interpretar("qual o prazo?", bruto, acertos, 1, 1)
        self.assertEqual(resultado.resposta, "O prazo é de 15 dias úteis, conforme o documento.")
        self.assertEqual(resultado.alertas, [])


class PromptDeFormatoTeste(unittest.TestCase):
    def test_prompt_pede_lista_paragrafos_e_proibe_citar_numero_de_trecho(self) -> None:
        self.assertIn('começando com "- "', _SISTEMA_V2)
        self.assertIn("parágrafos curtos", _SISTEMA_V2)
        self.assertIn("Nunca escreva no texto da resposta números de trecho", _SISTEMA_V2)



class RespostaCortadaTeste(unittest.TestCase):
    def _acertos(self):
        trecho = Trecho("1", "Seção: 1 Prazos\nO prazo é de 15 dias úteis.", "a.pdf", "C", "0", True, "t", "1 Prazos", "1")
        return [Acerto(trecho=trecho, coseno=1, bm25=1, rrf=1)]

    def test_json_cortado_nao_vira_fora_do_normativo(self) -> None:
        from app.rag import _CORTADA

        cortado = '{"status":"coberto","resposta":"As responsabilidades são:\n- Assegurar que'
        resultado = _interpretar("quais as responsabilidades?", cortado, self._acertos(), 1, 1)
        self.assertFalse(resultado.coberto)
        self.assertEqual(resultado.status, "ausente")
        self.assertEqual(resultado.resposta, _CORTADA)
        self.assertNotIn("não está no normativo", resultado.resposta)

    def test_resposta_vazia_continua_sendo_recusa_padrao(self) -> None:
        resultado = _interpretar("p?", "", self._acertos(), 1, 1)
        self.assertTrue(resultado.resposta.startswith("Esse ponto não está no normativo"))

    def test_limite_de_saida_comporta_lista_longa(self) -> None:
        from app.rag import _MAXIMO_RESPOSTA

        self.assertGreaterEqual(_MAXIMO_RESPOSTA, 2000)


if __name__ == "__main__":
    unittest.main()
