from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.painel import estado_acervo, gravar_meta, nome_seguro, remover_arquivo, salvar_envio

_MD = """# Instrução de teste

Código: IT-100
Revisão: 3
Status: vigente

## 1. Escopo

Texto do escopo da instrução para a pré-análise.
""".encode("utf-8")


class PainelTeste(unittest.TestCase):
    def test_envio_preanalise_meta_e_remocao(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            salvar_envio(pasta, "IT 100.md", _MD)
            estado = estado_acervo(pasta, pasta / "data")
            self.assertEqual(len(estado["documentos"]), 1)
            documento = estado["documentos"][0]
            self.assertEqual(documento["codigo"], "IT-100")
            self.assertEqual(documento["revisao"], "3")
            self.assertTrue(documento["responde"])
            self.assertFalse(documento["meta"])
            self.assertIn("1. Escopo", {secao["nome"] for secao in documento["secoes"]})
            self.assertFalse(estado["indice"]["existe"])
            self.assertTrue(any("metadados" in aviso for aviso in documento["avisos"]))

            gravar_meta(pasta, "IT 100.md", "NO-01", "00", "Relatório de teste", True)
            atualizado = estado_acervo(pasta, pasta / "data")["documentos"][0]
            self.assertEqual(atualizado["codigo"], "NO-01")
            self.assertEqual(atualizado["revisao"], "00")
            self.assertTrue(atualizado["meta"])
            self.assertFalse(any("metadados" in aviso for aviso in atualizado["avisos"]))

            remover_arquivo(pasta, "IT 100.md")
            self.assertEqual(estado_acervo(pasta, pasta / "data")["documentos"], [])
            self.assertFalse((pasta / "IT 100.md.meta.json").exists())

    def test_rejeita_nome_fora_do_acervo(self) -> None:
        with self.assertRaises(ValueError):
            nome_seguro("../segredo.md")
        with self.assertRaises(ValueError):
            nome_seguro("nota.exe")
        with self.assertRaises(ValueError):
            nome_seguro("..md")
