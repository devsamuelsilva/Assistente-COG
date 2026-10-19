from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.chunking import Trecho
from app.painel import (
    _avisos_quebra,
    estado_acervo,
    estado_indexacao,
    gravar_meta,
    iniciar_indexacao,
    nome_seguro,
    remover_arquivo,
    salvar_envio,
)

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

    def test_envio_invalido_nao_entra_na_pasta_e_arquivo_ruim_vira_aviso(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            with self.assertRaises(ValueError):
                salvar_envio(pasta, "falso.pdf", b"isto nao e pdf")
            with self.assertRaises(ValueError):
                salvar_envio(pasta, "quebrado.pdf", b"%PDF-1.7 lixo sem estrutura")
            with self.assertRaises(ValueError):
                salvar_envio(pasta, "falso.docx", b"texto qualquer")
            self.assertEqual(list(pasta.iterdir()), [])

            salvar_envio(pasta, "IT 100.md", _MD)
            (pasta / "IT 100.md.meta.json").write_text("{quebrado", encoding="utf-8")
            (pasta / "antigo.pdf").write_bytes(b"%PDF-1.7 lixo")
            documentos = {documento["arquivo"]: documento for documento in estado_acervo(pasta, pasta / "data")["documentos"]}

        self.assertEqual(documentos["IT 100.md"]["codigo"], "IT-100")
        self.assertTrue(any("ilegível" in aviso for aviso in documentos["IT 100.md"]["avisos"]))
        self.assertFalse(documentos["antigo.pdf"]["responde"])
        self.assertTrue(any("Não foi possível ler" in aviso for aviso in documentos["antigo.pdf"]["avisos"]))

    def test_avisos_da_quebra(self) -> None:
        def trecho(secao: str, corpo: str) -> Trecho:
            return Trecho("1", f"Documento: IT\nSeção: {secao}\n{corpo}", "a.pdf", "IT", "0", True, "t", secao, "1")

        longo = "x" * 400
        trechos = [trecho(f"4 Glossário (parte {numero})", longo) for numero in range(1, 10)]
        trechos += [trecho("Seção de | Parte de uma subestação", longo), trecho("5 A", "curto"), trecho("6 B", "curto"), trecho("7 C", "curto")]
        avisos = _avisos_quebra(trechos)
        self.assertEqual(len(avisos), 3)
        self.assertIn("4 Glossário (9 partes)", avisos[0])
        self.assertIn("Seção de | Parte de uma subestação", avisos[1])
        self.assertTrue(avisos[2].startswith("3 trechos com menos de 150 caracteres"))
        self.assertEqual(_avisos_quebra([trecho("1 Objetivo", longo)]), [])

    def test_rejeita_nome_fora_do_acervo(self) -> None:
        with self.assertRaises(ValueError):
            nome_seguro("../segredo.md")
        with self.assertRaises(ValueError):
            nome_seguro("nota.exe")
        with self.assertRaises(ValueError):
            nome_seguro("..md")

    def test_pedido_durante_preparacao_roda_em_seguida(self) -> None:
        liberar = threading.Event()
        chamadas: list[set[str]] = []
        terminou = threading.Event()

        def preparador(data_dir, acervo, forcar, avisos, progresso) -> None:
            chamadas.append(set(forcar))
            progresso("indexando")
            if len(chamadas) == 1:
                liberar.wait(5)
            else:
                terminou.set()

        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            primeiro = iniciar_indexacao(raiz / "acervo", raiz / "data", preparador=preparador)
            self.assertTrue(primeiro["rodando"])
            segundo = iniciar_indexacao(raiz / "acervo", raiz / "data", forcar={"a.pdf"}, preparador=preparador)
            self.assertTrue(segundo["pendente"])
            liberar.set()
            self.assertTrue(terminou.wait(5))
            for _ in range(50):
                if not estado_indexacao()["rodando"]:
                    break
                time.sleep(0.05)
        self.assertEqual(chamadas, [set(), {"a.pdf"}])
        self.assertFalse(estado_indexacao()["rodando"])
        self.assertFalse(estado_indexacao()["pendente"])
