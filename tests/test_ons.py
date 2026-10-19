from __future__ import annotations

import json
import time
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from app import ons
from app.ons import ClienteMcp, ErroOns, compactar_contrato, ler_blocos, reescrever_periodo, responder_ons, sem_valor
from app.uso import itens, ler_uso

_RESULTADO_SQL = """| id_subsistema | carga |
|---|---|
| NE | 13588.3 |

```result-schema
{"datasets": ["carga-energia"], "columns": [{"name": "id_subsistema", "unit": null, "additive": false}, {"name": "carga", "unit": "MWmed", "additive": false}]}
```

*[contexto_temporal: hoje=2026-09-30 (qua) | ontem=2026-09-29]*

```tiago-dados-abertos-sources
{"sources": [{"dataset": "carga-energia", "portal_url": "https://dados.ons.org.br/dataset/carga-energia"}]}
```"""


class ClienteFalso:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict]] = []

    def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
        self.chamadas.append((ferramenta, argumentos))
        if ferramenta == "listar_datasets":
            return "## Catalogo\n- `carga-energia` | diária\n\n*[contexto_temporal: hoje=2026-09-30]*", False
        if ferramenta == "descrever_dataset":
            return "schema de carga-energia", False
        if ferramenta == "executar_sql":
            return _RESULTADO_SQL, False
        return "?", True

    def fechar(self) -> None:
        pass


def _chamada(identificador: str, nome: str, argumentos: dict) -> dict:
    return {"id": identificador, "nome": nome, "argumentos": json.dumps(argumentos)}


class GeradorRoteiro:
    def __init__(self, passos: list[dict]) -> None:
        self.passos = passos
        self.pedidos: list[tuple[list[dict], bool]] = []

    def __call__(self, mensagens: list[dict], ferramentas: bool) -> dict:
        self.pedidos.append(([dict(item) for item in mensagens], ferramentas))
        passo = self.passos[min(len(self.pedidos) - 1, len(self.passos) - 1)]
        return {"tokens_prompt": 1000, "tokens_cache": 400, "tokens_resposta": 50, **passo}


class OnsTeste(unittest.TestCase):
    def setUp(self) -> None:
        agente = patch.object(ons, "ONS_AGENTE", "")
        agente.start()
        self.addCleanup(agente.stop)

    def test_usinas_do_agente_entram_no_prompt_e_ficam_guardadas(self) -> None:
        class ClienteEmpresa(ClienteFalso):
            def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
                if ferramenta == "executar_sql" and "nom_agenteoperador" in argumentos["sql_query"]:
                    self.chamadas.append((ferramenta, argumentos))
                    return (
                        "| nom_conjunto | id_ons_conjunto | nom_usina | ceg | nom_estado |\n|---|---|---|---|---|\n"
                        "| Conj. Água Doce | CJU_SCAD | Amparo | EOL.CV.SC.029069-6.01 | SANTA CATARINA |\n\n"
                        "*[contexto_temporal: hoje=2026-09-30 (qua)]*"
                    ), False
                return super().chamar(ferramenta, argumentos)

        roteiro = [{"conteudo": "ok"}]
        cliente = ClienteEmpresa()
        with TemporaryDirectory() as temporario, patch.object(ons, "ONS_AGENTE", "Energimp"):
            pasta = Path(temporario)
            gerador = GeradorRoteiro(roteiro)
            responder_ons("Quais são nossas usinas?", data_dir=pasta, gerador=gerador, cliente=cliente, hoje=date(2026, 9, 30))
            responder_ons("Outra", data_dir=pasta, gerador=GeradorRoteiro(roteiro), cliente=cliente, hoje=date(2026, 9, 30))

        sistema = gerador.pedidos[0][0][0]["content"]
        sql = next(argumentos["sql_query"] for nome, argumentos in cliente.chamadas if nome == "executar_sql")
        self.assertIn("EMPRESA: o COG é da Energimp", sistema)
        self.assertIn("ceg IN ('EOL.CV.SC.029069-6.01')", sistema)
        self.assertIn("id_ons IN ('CJU_SCAD')", sistema)
        self.assertIn("Conj. Água Doce", sistema)
        self.assertNotIn("contexto_temporal", sistema)
        self.assertIn("ILIKE '%Energimp%'", sql)
        self.assertIn("IS NULL", sql)
        self.assertEqual(sum(1 for nome, _ in cliente.chamadas if nome == "executar_sql"), 1)

    def test_agente_sem_usinas_nao_muda_o_prompt(self) -> None:
        with TemporaryDirectory() as temporario, patch.object(ons, "ONS_AGENTE", "Ninguem"):
            gerador = GeradorRoteiro([{"conteudo": "ok"}])
            responder_ons("Pergunta", data_dir=Path(temporario), gerador=gerador, cliente=ClienteFalso())

        self.assertNotIn("EMPRESA:", gerador.pedidos[0][0][0]["content"])

    def test_laco_executa_ferramentas_e_devolve_fonte_unidade_e_sql(self) -> None:
        gerador = GeradorRoteiro(
            [
                {"chamadas": [_chamada("a", "descrever_dataset", {"nome_dataset": "carga-energia"})]},
                {"chamadas": [_chamada("b", "executar_sql", {"sql_query": "SELECT 1", "limit": 500})]},
                {"conteudo": "A carga média do Nordeste foi 13.588,3 MWmed."},
            ]
        )
        cliente = ClienteFalso()
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            saida = responder_ons("Carga do NE em agosto?", data_dir=pasta, gerador=gerador, cliente=cliente, hoje=date(2026, 9, 30))
            lista = itens(pasta)
            evento = json.loads((pasta / "logs" / "consultas.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            catalogo = (pasta / "ons" / "catalogo.txt").read_text(encoding="utf-8")

        self.assertEqual(saida["resposta"], "A carga média do Nordeste foi 13.588,3 MWmed.")
        self.assertTrue(saida["coberto"])
        self.assertEqual(saida["fontes"], [{"dataset": "carga-energia", "portal_url": "https://dados.ons.org.br/dataset/carga-energia"}])
        self.assertEqual(saida["consultas"][0]["sql"], "SELECT 1")
        self.assertEqual(saida["consultas"][0]["unidades"], [{"coluna": "carga", "unidade": "MWmed", "aditiva": False}])
        self.assertIn(("executar_sql", {"sql_query": "SELECT 1", "limit": 50}), cliente.chamadas)
        self.assertNotIn("contexto_temporal", catalogo)
        self.assertIn("carga-energia", gerador.pedidos[0][0][0]["content"])
        self.assertIn("30/09/2026", gerador.pedidos[0][0][-1]["content"])
        item = lista[0]
        self.assertEqual(item["origem"], "ons")
        self.assertEqual(item["citacoes"][0]["codigo"], "ONS Dados Abertos")
        self.assertEqual(item["tokens_prompt"], 3000)
        self.assertEqual(item["passos"], 3)
        self.assertIsNotNone(item["duracao_ms"])
        etapas = [(etapa["tipo"], etapa.get("ferramenta") or etapa.get("chamadas")) for etapa in evento["etapas"]]
        self.assertEqual(
            etapas,
            [
                ("ons", "catalogo"),
                ("gpt", ["descrever_dataset"]),
                ("ons", "descrever_dataset"),
                ("gpt", ["executar_sql"]),
                ("ons", "executar_sql"),
                ("gpt", []),
            ],
        )
        self.assertEqual(evento["tokens_cache"], 1200)

    def test_para_no_limite_de_passos_e_recusa_ferramenta_desconhecida(self) -> None:
        gerador = GeradorRoteiro(
            [{"chamadas": [_chamada("x", "apagar_tudo", {})], "conteudo": "sem dado"}]
        )
        cliente = ClienteFalso()
        with TemporaryDirectory() as temporario:
            saida = responder_ons("Pergunta qualquer", data_dir=Path(temporario), gerador=gerador, cliente=cliente)

        self.assertFalse(any(nome == "apagar_tudo" for nome, _ in cliente.chamadas))
        mensagens_ferramenta = [m for m in gerador.pedidos[-1][0] if m["role"] == "tool"]
        self.assertTrue(mensagens_ferramenta)
        self.assertIn("Ferramenta não disponível", mensagens_ferramenta[0]["content"])
        self.assertFalse(gerador.pedidos[-1][1])
        self.assertTrue(all(permitido for _, permitido in gerador.pedidos[:-1]))
        self.assertEqual(saida["resposta"], "sem dado")
        self.assertFalse(saida["coberto"])

    def test_cache_do_dia(self) -> None:
        roteiro = [
            {"chamadas": [_chamada("b", "executar_sql", {"sql_query": "SELECT 1"})]},
            {"conteudo": "resposta"},
        ]
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            primeira = GeradorRoteiro(roteiro)
            responder_ons("Carga do NE?", data_dir=pasta, gerador=primeira, cliente=ClienteFalso(), hoje=date(2026, 9, 30))
            segunda = GeradorRoteiro(roteiro)
            repetida = responder_ons("carga do  ne?", data_dir=pasta, gerador=segunda, cliente=ClienteFalso(), hoje=date(2026, 9, 30))
            outro_dia = GeradorRoteiro(roteiro)
            responder_ons("Carga do NE?", data_dir=pasta, gerador=outro_dia, cliente=ClienteFalso(), hoje=date(2026, 10, 1))
            uso = ler_uso(pasta)
            lista = itens(pasta)

        self.assertTrue(repetida["cache"])
        self.assertEqual(repetida["fontes"][0]["dataset"], "carga-energia")
        self.assertEqual(segunda.pedidos, [])
        self.assertTrue(outro_dia.pedidos)
        self.assertEqual(uso["resumo"]["cache"], 1)
        self.assertFalse(any(item["continuacao"] for item in lista))

    def test_pergunta_com_historico_fica_marcada_como_continuacao(self) -> None:
        roteiro = [
            {"chamadas": [_chamada("b", "executar_sql", {"sql_query": "SELECT 1"})]},
            {"conteudo": "resposta"},
        ]
        historico = [{"pergunta": "Corte eólico em agosto?", "resposta": "3.922,2 GWh"}]
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            responder_ons("E de Papagaios?", historico=historico, data_dir=pasta, gerador=GeradorRoteiro(roteiro), cliente=ClienteFalso())
            evento = json.loads((pasta / "logs" / "consultas.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            lista = itens(pasta)

        self.assertTrue(evento["continuacao"])
        self.assertTrue(lista[0]["continuacao"])

    def test_erro_em_texto_do_ons_nao_vira_fonte_nem_sql_exibido(self) -> None:
        class ClienteComErro(ClienteFalso):
            def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
                if ferramenta == "executar_sql" and "ruim" in argumentos["sql_query"]:
                    self.chamadas.append((ferramenta, argumentos))
                    return "[ERRO] Conversion Error: Could not convert string '' to DOUBLE", False
                return super().chamar(ferramenta, argumentos)

        gerador = GeradorRoteiro(
            [
                {"chamadas": [_chamada("a", "executar_sql", {"sql_query": "SELECT ruim"})]},
                {"chamadas": [_chamada("b", "executar_sql", {"sql_query": "SELECT 1"})]},
                {"conteudo": "ok"},
            ]
        )
        with TemporaryDirectory() as temporario:
            saida = responder_ons("Pergunta", data_dir=Path(temporario), gerador=gerador, cliente=ClienteComErro())

        self.assertEqual([consulta["sql"] for consulta in saida["consultas"]], ["SELECT 1"])
        self.assertEqual(len(saida["fontes"]), 1)

    def test_zero_do_mes_nao_fechado_vira_aviso_e_nao_fonte(self) -> None:
        zerado = (
            "| id_subsistema | carga |\n|---|---|\n| NE | 0.0 |\n\n```result-schema\n"
            '{"columns": [{"name": "id_subsistema", "role": "dimension"}, {"name": "carga", "role": "measure", "unit": "MWmed"}]}\n```\n\n'
            "```tiago-dados-abertos-sources\n"
            '{"sources": [{"dataset": "carga-mensal", "portal_url": "https://dados.ons.org.br/dataset/carga-mensal"}]}\n```'
        )
        self.assertTrue(sem_valor(zerado))
        self.assertTrue(sem_valor("| a |\n|---|\n"))
        self.assertFalse(sem_valor(zerado.replace("0.0", "13588.3")))

        class ClienteZerado(ClienteFalso):
            def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
                if ferramenta == "executar_sql":
                    return zerado, False
                return super().chamar(ferramenta, argumentos)

        gerador = GeradorRoteiro(
            [{"chamadas": [_chamada("a", "executar_sql", {"sql_query": "SELECT 1"})]}, {"conteudo": "não encontrado"}]
        )
        with TemporaryDirectory() as temporario:
            saida = responder_ons("Carga", data_dir=Path(temporario), gerador=gerador, cliente=ClienteZerado())

        retorno = [m for m in gerador.pedidos[-1][0] if m["role"] == "tool"][0]["content"]
        self.assertIn("dado ainda não publicado", retorno)
        self.assertEqual(len(gerador.pedidos), 3)
        self.assertIn("grão menor", gerador.pedidos[-1][0][-1]["content"])
        self.assertEqual(saida["consultas"], [{"sql": "SELECT 1", "unidades": []}])
        self.assertEqual(saida["fontes"], [])
        self.assertFalse(saida["coberto"])

    def test_resposta_sem_fonte_nao_vai_para_o_cache(self) -> None:
        roteiro = [{"conteudo": "não encontrei"}]
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            responder_ons("Pergunta", data_dir=pasta, gerador=GeradorRoteiro(roteiro), cliente=ClienteFalso())
            segunda = GeradorRoteiro(roteiro)
            repetida = responder_ons("Pergunta", data_dir=pasta, gerador=segunda, cliente=ClienteFalso())
        self.assertFalse(repetida["cache"])
        self.assertTrue(segunda.pedidos)

    def test_ferramentas_do_mesmo_passo_rodam_juntas_e_contrato_fica_em_cache(self) -> None:
        class ClienteLento(ClienteFalso):
            def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
                if ferramenta == "executar_sql":
                    time.sleep(0.3)
                return super().chamar(ferramenta, argumentos)

        roteiro = [
            {
                "chamadas": [
                    _chamada("a", "descrever_dataset", {"nome_dataset": "carga-energia"}),
                    _chamada("b", "executar_sql", {"sql_query": "SELECT 1"}),
                    _chamada("c", "executar_sql", {"sql_query": "SELECT 2"}),
                ]
            },
            {"conteudo": "ok"},
        ]
        cliente = ClienteLento()
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            inicio = time.perf_counter()
            saida = responder_ons("Pergunta", data_dir=pasta, gerador=GeradorRoteiro(roteiro), cliente=cliente)
            decorrido = time.perf_counter() - inicio
            responder_ons("Outra", data_dir=pasta, gerador=GeradorRoteiro(roteiro), cliente=cliente)
            guardado = (pasta / "ons" / "contratos" / "carga-energia.txt").is_file()

        self.assertLess(decorrido, 0.55)
        self.assertEqual([consulta["sql"] for consulta in saida["consultas"]], ["SELECT 1", "SELECT 2"])
        self.assertTrue(guardado)
        self.assertEqual(sum(1 for nome, _ in cliente.chamadas if nome == "descrever_dataset"), 1)

    def test_contrato_compacto_mantem_colunas_instrucoes_e_sql(self) -> None:
        bruto = """# [ONS] teste
**parquet_source:** `read_parquet('s3://ons-aws-prod-opendata/dataset/teste/*.parquet')`
**Granularidade:** diária

schema:
  columns:
    - name: val_carga
      type: VARCHAR
      role: METRIC
      description: Carga em MWmed.
      cast: TRY_CAST(val_carga AS DOUBLE)
      aliases: [carga, demanda]

## Semantica (para gerar SQL correto)
**Metricas:**
- `carga` (MWmed, default: sum) — modos: total_energy, average_power
**Suporta:** time_series
**Padroes SQL prontos (copie):**
- `carga_mensal` -- saida: MWmed
  ```sql
SELECT AVG(val_carga)
FROM {source}
  ```
- `outra_mensal` -- saida: MWmed
  ```sql
SELECT AVG(val_outra)
FROM {source}
  ```

## Instrucoes criticas
- [a] Texto repetido.

- [b] Texto repetido.

## Exemplos SQL
Q: Carga?
SQL: SELECT 1

```tiago-dados-abertos-sources
{"sources": []}
```

*[contexto_temporal: hoje=2026-09-30]*"""
        compacto = compactar_contrato(bruto)
        self.assertIn("parquet_source", compacto)
        self.assertIn("- val_carga VARCHAR metric: Carga em MWmed. Use TRY_CAST(val_carga AS DOUBLE).", compacto)
        self.assertIn("carga MWmed", compacto)
        self.assertIn("SELECT AVG(val_carga) FROM {source} Mesmo formato, trocando a coluna val_: outra_mensal.", compacto)
        self.assertIn("- [a, b] Texto repetido.", compacto)
        self.assertIn("SQL: SELECT 1", compacto)
        for fora in ("aliases", "modos:", "Suporta", "contexto_temporal", "tiago-dados-abertos-sources"):
            self.assertNotIn(fora, compacto)

    def test_reescreve_read_parquet_para_os_arquivos_do_periodo(self) -> None:
        pasta = "dataset/coff/"
        mensais = [f"{pasta}COFF_{ano}_{mes:02d}.parquet" for ano in (2025, 2026) for mes in range(1, 13)][:21]
        listar = lambda _pasta: mensais  # noqa: E731
        hoje = date(2026, 9, 30)
        origem = f"SELECT 1 FROM read_parquet('s3://ons-aws-prod-opendata/{pasta}*.parquet', union_by_name=true)"

        agosto = reescrever_periodo(origem + " WHERE din_instante >= '2026-08-01' AND din_instante < '2026-09-01'", listar, hoje)
        self.assertIn(f"read_parquet(['s3://ons-aws-prod-opendata/{pasta}COFF_2026_08.parquet'], union_by_name=true)", agosto)

        ano = reescrever_periodo(origem + " WHERE EXTRACT(YEAR FROM TRY_CAST(din_instante AS TIMESTAMP)) = 2025", listar, hoje)
        self.assertEqual(ano.count(".parquet'"), 12)

        aberto = reescrever_periodo(origem + " WHERE din_instante >= '2026-07-15'", listar, hoje)
        self.assertEqual(aberto.count(".parquet'"), 3)

        for intacto in (
            origem,
            origem + " WHERE din_instante < '2026-01-01'",
            origem + " WHERE din_instante >= '2026-10-01'",
            origem + " WHERE din_instante >= CAST('2026-08-01' AS DATE)",
        ):
            self.assertEqual(reescrever_periodo(intacto, listar, hoje), intacto)
        self.assertEqual(
            reescrever_periodo(origem + " WHERE din_instante = '2026-08-01'", lambda _p: [f"{pasta}LEIAME.parquet"], hoje),
            origem + " WHERE din_instante = '2026-08-01'",
        )

    def test_sql_recortado_que_falha_roda_de_novo_como_veio(self) -> None:
        class ClienteRecorte(ClienteFalso):
            def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
                if ferramenta == "executar_sql" and "read_parquet([" in argumentos["sql_query"]:
                    self.chamadas.append((ferramenta, argumentos))
                    return "[ERRO] arquivo não encontrado", False
                return super().chamar(ferramenta, argumentos)

        sql = (
            "SELECT 1 FROM read_parquet('s3://ons-aws-prod-opendata/dataset/coff/*.parquet') "
            "WHERE din_instante >= '2026-08-01' AND din_instante < '2026-09-01'"
        )
        cliente = ClienteRecorte()
        roteiro = [{"chamadas": [_chamada("a", "executar_sql", {"sql_query": sql})]}, {"conteudo": "ok"}]
        original = ons._arquivos_s3
        ons._arquivos_s3 = lambda _pasta, _data: ["dataset/coff/C_2026_07.parquet", "dataset/coff/C_2026_08.parquet"]
        try:
            with TemporaryDirectory() as temporario:
                saida = responder_ons("P", data_dir=Path(temporario), gerador=GeradorRoteiro(roteiro), cliente=cliente, hoje=date(2026, 9, 30))
        finally:
            ons._arquivos_s3 = original
        enviados = [argumentos["sql_query"] for nome, argumentos in cliente.chamadas if nome == "executar_sql"]
        self.assertEqual(len(enviados), 2)
        self.assertIn("C_2026_08.parquet", enviados[0])
        self.assertEqual(enviados[1], sql)
        self.assertEqual(saida["consultas"][0]["sql"], sql)
        self.assertTrue(saida["coberto"])

    def test_ler_blocos_ignora_json_quebrado(self) -> None:
        fontes, unidades = ler_blocos("```result-schema\n{quebrado\n```\n" + _RESULTADO_SQL)
        self.assertEqual(len(fontes), 1)
        self.assertEqual(unidades[0]["unidade"], "MWmed")


class ClienteMcpTeste(unittest.TestCase):
    def _cliente(self, sse: bool) -> tuple[ClienteMcp, list[dict]]:
        pedidos: list[dict] = []

        def responder(pedido: httpx.Request) -> httpx.Response:
            corpo = json.loads(pedido.content)
            pedidos.append({"corpo": corpo, "sessao": pedido.headers.get("mcp-session-id")})
            if "id" not in corpo:
                return httpx.Response(202)
            if corpo["method"] == "initialize":
                resultado = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "falso"}}
            else:
                resultado = {"content": [{"type": "text", "text": "ok " + corpo["params"]["name"]}], "isError": False}
            mensagem = {"jsonrpc": "2.0", "id": corpo["id"], "result": resultado}
            cabecalhos = {"mcp-session-id": "sessao-1"}
            if sse:
                texto = f"event: message\ndata: {json.dumps(mensagem)}\n\n"
                return httpx.Response(200, text=texto, headers={**cabecalhos, "content-type": "text/event-stream"})
            return httpx.Response(200, json=mensagem, headers=cabecalhos)

        return ClienteMcp("https://mcp.teste/", transporte=httpx.MockTransport(responder)), pedidos

    def test_json_e_sse(self) -> None:
        for sse in (False, True):
            cliente, pedidos = self._cliente(sse)
            with cliente:
                texto, erro = cliente.chamar("listar_datasets", {})
                cliente.chamar("descrever_dataset", {"nome_dataset": "x"})
            self.assertEqual(texto, "ok listar_datasets")
            self.assertFalse(erro)
            metodos = [pedido["corpo"]["method"] for pedido in pedidos]
            self.assertEqual(metodos, ["initialize", "notifications/initialized", "tools/call", "tools/call"])
            self.assertIsNone(pedidos[0]["sessao"])
            self.assertEqual(pedidos[-1]["sessao"], "sessao-1")

    def test_sessao_expirada_reinicia_e_repete(self) -> None:
        pedidos: list[str] = []
        sessoes = iter(["sessao-1", "sessao-2"])
        atual = {"sessao": None}

        def responder(pedido: httpx.Request) -> httpx.Response:
            corpo = json.loads(pedido.content)
            pedidos.append(corpo["method"])
            if "id" not in corpo:
                return httpx.Response(202)
            if corpo["method"] == "initialize":
                atual["sessao"] = next(sessoes)
                mensagem = {"jsonrpc": "2.0", "id": corpo["id"], "result": {}}
                return httpx.Response(200, json=mensagem, headers={"mcp-session-id": atual["sessao"]})
            if pedido.headers.get("mcp-session-id") == "sessao-1" and len(pedidos) > 3:
                return httpx.Response(404)
            resultado = {"content": [{"type": "text", "text": "ok"}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": corpo["id"], "result": resultado})

        with ClienteMcp("https://mcp.teste/", transporte=httpx.MockTransport(responder)) as cliente:
            cliente.chamar("listar_datasets", {})
            texto, _ = cliente.chamar("listar_datasets", {})
        self.assertEqual(texto, "ok")
        self.assertEqual(
            pedidos,
            ["initialize", "notifications/initialized", "tools/call", "tools/call", "initialize", "notifications/initialized", "tools/call"],
        )

    def test_servidor_fora_do_ar(self) -> None:
        cliente = ClienteMcp("https://mcp.teste/", transporte=httpx.MockTransport(lambda _pedido: httpx.Response(503)))
        with cliente, self.assertRaises(ErroOns):
            cliente.chamar("listar_datasets", {})


if __name__ == "__main__":
    unittest.main()
