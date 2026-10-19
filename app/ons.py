from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import httpx

from app import cache, gpt, registro
from app.arquivos import gravar_texto
from app.config import DATA_DIR, ONS_AGENTE, ONS_MAX_PASSOS, ONS_MCP_URL, OPENAI_MODEL

AVISO_ONS = (
    "Dados públicos do ONS (TIAGO Dados Abertos). O ONS revisa períodos recentes. "
    "Para decisão operacional, confirme nos canais oficiais."
)
CODIGO_ONS = "ONS Dados Abertos"
_LIMITE_LINHAS = 50
_LIMITE_SAIDA = 12_000
_VALIDADE_CATALOGO = 24 * 3600
_VALIDADE_CONTRATO = 24 * 3600
_VALIDADE_ARQUIVOS = 6 * 3600
_LIMITE_DESCRICAO = 180
_S3 = "https://ons-aws-prod-opendata.s3.amazonaws.com/"
_PARQUET = re.compile(r"read_parquet\(\s*'s3://ons-aws-prod-opendata/(dataset/[^'*]+/)\*\.parquet'")
_DATA = re.compile(r"(<=|>=|<|>|=|\bBETWEEN|\bAND)?\s*(?:DATE\s+|TIMESTAMP\s+)?'(\d{4})-(\d{2})-(\d{2})", re.IGNORECASE)
_ANO = re.compile(r"(?:\bEXTRACT\s*\(\s*YEAR\s+FROM\b|\bYEAR\s*\()[^=]*?\)\s*=\s*(\d{4})\b", re.IGNORECASE)
_ARQUIVO_PERIODO = re.compile(r"_(\d{4})(?:_(\d{2}))?\.parquet$", re.IGNORECASE)
_DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
_RODAPE_TEMPORAL = re.compile(r"\s*\*\[contexto_temporal:[^\]]*\]\*\s*")
_AVISO_VAZIO = (
    "\n\n[Aviso do assistente do COG: a consulta não trouxe valor (sem linhas, nulo ou zero). Em carga, geração, "
    "demanda ou energia armazenada isso é dado ainda não publicado neste dataset, não valor zero. Troque para o "
    "dataset de grão menor (diário, horário ou 30 min), chame descrever_dataset dele e agregue no SQL. Só responda "
    "zero se a grandeza puder ser zero de fato, como restrição ou geração solar à noite.]"
)
_AVISO_CONVERSAO = (
    "\n\n[Aviso do assistente do COG: nesta coluna os valores já são numéricos em parte dos arquivos. Tire toda "
    "comparação com '' e todo NULLIF, mesmo que o contrato recomende, e use só TRY_CAST(coluna AS DOUBLE).]"
)
_INSISTIR = (
    "Nenhuma consulta trouxe valor ainda. Antes de responder, tente o dataset de grão menor do mesmo assunto "
    "(por exemplo carga-energia no lugar de carga-mensal): chame descrever_dataset dele e rode executar_sql agregando "
    "o período pedido. Só responda que não encontrou se essa tentativa também vier sem valor."
)
_BLOCO = re.compile(r"```(result-schema|tiago-dados-abertos-sources)\s*\n(.*?)\n```", re.DOTALL)

FERRAMENTAS = [
    {
        "type": "function",
        "function": {
            "name": "listar_datasets",
            "description": "Catálogo inteiro dos datasets do ONS. O catálogo já está no prompt; use só se ele faltar.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_dataset",
            "description": "Filtra o catálogo por termo (até 10 datasets). Passe a pergunta inteira, sem resumir.",
            "parameters": {
                "type": "object",
                "properties": {"tema": {"type": "string"}},
                "required": ["tema"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "descrever_dataset",
            "description": (
                "Schema, colunas, TRY_CAST obrigatórios, caminho read_parquet, exemplos de SQL e URL do portal "
                "de um dataset. Obrigatório antes de escrever SQL."
            ),
            "parameters": {
                "type": "object",
                "properties": {"nome_dataset": {"type": "string", "description": "Nome exato do catálogo."}},
                "required": ["nome_dataset"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "executar_sql",
            "description": (
                "Executa SQL DuckDB somente leitura (SELECT ou WITH) sobre os dados abertos do ONS. "
                "Devolve a tabela, a unidade de cada coluna e a fonte."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql_query": {"type": "string"},
                    "limit": {"type": "integer", "description": f"Máximo de linhas, até {_LIMITE_LINHAS}."},
                },
                "required": ["sql_query"],
                "additionalProperties": False,
            },
        },
    },
]
_PERMITIDAS = {item["function"]["name"] for item in FERRAMENTAS}

_SISTEMA = """Você é o assistente do COG - Centro de Operação da Geração para consultar os dados abertos do ONS (Operador Nacional do Sistema Elétrico) pelo servidor TIAGO Dados Abertos.
REGRAS:
1. Responda só com números que vieram de executar_sql nesta conversa. Não estime nem complete de memória. Só diga que o dado não foi encontrado depois de tentar também o dataset de grão menor (regra 4a).
2. Escolha o dataset pelo assunto e pelo grão da pergunta (30 min, horário, diário, mensal; verificado, programado ou cadastral). O catálogo está abaixo. Use buscar_dataset só se não achar pelo catálogo.
3. Antes de escrever SQL, chame descrever_dataset com o nome exato e use as colunas, os TRY_CAST e o caminho read_parquet do contrato. Nunca chute nome de coluna.
4. Agregue no SQL e traga poucas linhas. Resposta que começa com [ERRO] é consulta que falhou: leia a mensagem, corrija e tente de novo. TRY_CAST já transforma valor inválido em NULL e AVG ignora NULL; não compare coluna com texto vazio ('').
4a. Resultado vazio, nulo ou zero em grandeza que não zera (carga, geração, demanda, energia armazenada) significa dado ainda não publicado naquele dataset, não valor zero. Nunca informe esse zero como resultado. Nesse caso, e sempre que o período for dos últimos 2 meses, troque para o dataset de grão menor (diário, horário ou 30 min) e agregue no SQL. Exemplo: carga de um mês recente sai de carga-energia (diário), não de carga-mensal.
4b. Cada dataset tem o próprio caminho read_parquet e as próprias colunas. Ao trocar de dataset, chame descrever_dataset dele antes do SQL.
4c. Filtre por código quando o contrato tiver (id_subsistema = 'NE', 'SE', 'S', 'N'). Usina ou conjunto se filtra por strip_accents(nom_usina) ILIKE '%nome sem acento%', pelo ceg ou pelo id_ons, nunca por igualdade de nome: o ONS grava "Conj. Papagaios" (id_ons CJU_CEPPG), não "Papagaios", e grava acentos ("Conj. Água Doce" só casa com strip_accents(...) ILIKE '%agua doce%'). Se o filtro vier vazio, rode SELECT DISTINCT nom_usina, id_ons com o mesmo filtro e refaça.
4e. Empresa, agente, dono ou operador: o nome da empresa não aparece em nom_usina nem em nom_conjunto. Descubra as usinas em capacidade-geracao (strip_accents(nom_agenteoperador) ILIKE '%nome%' OR strip_accents(nom_agenteproprietario) ILIKE '%nome%'), pegue os ceg e filtre os outros datasets por ceg IN (...) ou pelos conjuntos dessas usinas (usina_conjunto).
4f. Em cadastro com data de fim (dat_fimrelacionamento e parecidas), data vazia significa vigente: use (TRY_CAST(coluna AS DATE) IS NULL OR TRY_CAST(coluna AS DATE) >= data).
4d. Filtre o período com datas literais na coluna de tempo (din_instante >= '2026-08-01' AND din_instante < '2026-09-01', ou EXTRACT(YEAR FROM din_instante) = 2025). Não use CURRENT_DATE nem subconsulta de MAX(data): com datas literais o programa lê só os arquivos do período e a consulta fica muito mais rápida.
5. Não some ao longo do tempo colunas com "additive": false (médias, estoques). Informe a unidade que veio no result-schema.
6. "Hoje", "ontem", "semana passada" e "mês passado" usam a data informada na pergunta, no fuso America/Sao_Paulo.
7. No SQL, coloque só o necessário para a consulta.
7a. Respeite a perspectiva pedida. Pergunta sobre o que aconteceu (verificado, medido, ontem) usa dataset verificado. Se só houver programado ou previsto, diga primeiro que o verificado ainda não está publicado e apresente o número como programado, sem chamá-lo de verificado.
8. Resposta em português, direta, frases curtas, no tom das instruções de operação. Diga o período, o recorte (subsistema, usina, fonte) e a unidade, e cite o dataset. Não repita o SQL; ele aparece à parte. Se o dado for de período recente, avise que o ONS ainda pode revisar.

RECEITAS (valem mais que os exemplos do contrato quando divergirem):
- Corte de geração, curtailment ou constrained-off eólico: restricao_coff_eolica_usi; solar: restricao_coff_fotovoltaica. Energia não gerada em GWh = SUM(val_geracaonaorealizadaapurada) * 0.5 / 1000. Não multiplique por num_minutos_* nem divida por 60. Por razão, agrupe por cod_razaorestricao.
- Carga diária: carga-energia, com AVG(TRY_CAST(val_cargaenergiamwmed AS DOUBLE)) agrupado por DATE(din_instante) e id_subsistema (o ONS publica o mesmo dia duas vezes). Unidade MWmed.
- Carga mensal: carga-mensal para meses fechados antigos. Mês dos últimos 2 meses sai de carga-energia: média das médias diárias em MWmed; energia em GWh = soma das médias diárias * 24 / 1000.
{empresa}
CATÁLOGO DOS DATASETS:
{catalogo}"""

_EMPRESA = """
EMPRESA: o COG é da {agente}. "{agente}", "a empresa", "nossas usinas", "nossos parques" e "nossos conjuntos" são as usinas abaixo, em que a {agente} é agente operador ou proprietário no cadastro capacidade-geracao do ONS (lista lida em {dia}). Dataset com registro por usina: filtre por ceg IN ({cegs}). Dataset com registro por conjunto (restricao_coff_eolica_usi, restricao_coff_fotovoltaica e outros em que o ceg vem vazio ou "-"): filtre por id_ons IN ({conjuntos}). Se um filtro vier vazio, tente o outro. Para dizer quais são as usinas, use esta lista e cite capacidade-geracao; potência e demais números saem de executar_sql.
{tabela}
"""
_CEG = re.compile(r"\b[A-Z]{3}\.[A-Z]{2}\.[A-Z]{2}\.\d{6}-\d\.\d{2}\b")
_CONJUNTO = re.compile(r"\bCJU_\w+")


class ErroOns(RuntimeError):
    pass


class ClienteMcp:
    def __init__(self, url: str = ONS_MCP_URL, timeout: float = 90.0, transporte: httpx.BaseTransport | None = None):
        self.url = url
        self._http = httpx.Client(
            timeout=timeout,
            transport=transporte,
            headers={"User-Agent": "assistente-cog/1.0 (COG - Centro de Operacao da Geracao)"},
        )
        self._sessao: str | None = None
        self._proximo = 0
        self._iniciado = False
        self._trava_id = threading.Lock()
        self._trava_inicio = threading.RLock()

    def __enter__(self) -> ClienteMcp:
        return self

    def __exit__(self, *_erro) -> None:
        self.fechar()

    def fechar(self) -> None:
        self._http.close()

    def chamar(self, ferramenta: str, argumentos: dict) -> tuple[str, bool]:
        self._iniciar()
        resultado = self._enviar("tools/call", {"name": ferramenta, "arguments": argumentos})
        partes = [
            str(item.get("text") or "")
            for item in resultado.get("content") or []
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(partes), bool(resultado.get("isError"))

    def _iniciar(self) -> None:
        if self._iniciado:
            return
        with self._trava_inicio:
            if self._iniciado:
                return
            self._enviar(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "assistente-cog", "version": "1.0"},
                },
            )
            self._enviar("notifications/initialized", notificacao=True)
            self._iniciado = True

    def _enviar(
        self, metodo: str, params: dict | None = None, notificacao: bool = False, repetido: bool = False
    ) -> dict:
        corpo: dict = {"jsonrpc": "2.0", "method": metodo}
        if params is not None:
            corpo["params"] = params
        if not notificacao:
            with self._trava_id:
                self._proximo += 1
                corpo["id"] = self._proximo
        cabecalhos = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        sessao = self._sessao
        if sessao:
            cabecalhos["mcp-session-id"] = sessao
        try:
            resposta = self._http.post(self.url, json=corpo, headers=cabecalhos)
        except httpx.HTTPError as erro:
            raise ErroOns(f"Não foi possível falar com o servidor do ONS ({erro.__class__.__name__}).") from erro
        if resposta.status_code == 404 and sessao and metodo != "initialize" and not repetido:
            with self._trava_inicio:
                if self._sessao == sessao:
                    self._sessao = None
                    self._iniciado = False
            self._iniciar()
            return self._enviar(metodo, params, notificacao, repetido=True)
        if resposta.status_code >= 400:
            raise ErroOns(f"O servidor do ONS respondeu {resposta.status_code}. Tente de novo em alguns minutos.")
        self._sessao = resposta.headers.get("mcp-session-id") or self._sessao
        if notificacao:
            return {}
        mensagem = ler_mensagem(resposta.headers.get("content-type", ""), resposta.text, corpo["id"])
        if mensagem.get("error"):
            detalhe = mensagem["error"].get("message") if isinstance(mensagem["error"], dict) else mensagem["error"]
            raise ErroOns(f"O servidor do ONS recusou o pedido: {detalhe}")
        return mensagem.get("result") or {}


_MCP: ClienteMcp | None = None
_TRAVA_MCP = threading.Lock()


def _cliente_compartilhado() -> ClienteMcp:
    global _MCP
    with _TRAVA_MCP:
        if _MCP is None:
            _MCP = ClienteMcp()
        return _MCP


def ler_mensagem(tipo: str, texto: str, identificador: int) -> dict:
    if "text/event-stream" not in tipo.lower():
        try:
            return json.loads(texto)
        except json.JSONDecodeError as erro:
            raise ErroOns("Resposta ilegível do servidor do ONS.") from erro
    for evento in re.split(r"\r?\n\r?\n", texto):
        dados = "\n".join(
            linha[5:].lstrip() for linha in evento.splitlines() if linha.startswith("data:")
        ).strip()
        if not dados:
            continue
        try:
            mensagem = json.loads(dados)
        except json.JSONDecodeError:
            continue
        if isinstance(mensagem, dict) and mensagem.get("id") == identificador:
            return mensagem
    raise ErroOns("O servidor do ONS não devolveu a resposta esperada.")


def ler_blocos(texto: str) -> tuple[list[dict], list[dict]]:
    fontes: list[dict] = []
    unidades: list[dict] = []
    for tipo, conteudo in _BLOCO.findall(texto):
        try:
            dados = json.loads(conteudo)
        except json.JSONDecodeError:
            continue
        if tipo == "tiago-dados-abertos-sources":
            for fonte in dados.get("sources") or []:
                if isinstance(fonte, dict) and fonte.get("dataset"):
                    fontes.append({"dataset": str(fonte["dataset"]), "portal_url": str(fonte.get("portal_url") or "")})
        else:
            for coluna in dados.get("columns") or []:
                if isinstance(coluna, dict) and coluna.get("unit"):
                    unidades.append(
                        {
                            "coluna": str(coluna.get("name") or ""),
                            "unidade": str(coluna["unit"]),
                            "aditiva": bool(coluna.get("additive")),
                        }
                    )
    return fontes, unidades


def sem_valor(texto: str) -> bool:
    tabela = texto.split("```", 1)[0]
    linhas = [linha.strip() for linha in tabela.splitlines() if linha.strip().startswith("|")]
    if len(linhas) < 2:
        return True
    cabecalho = [celula.strip() for celula in linhas[0].strip("|").split("|")]
    dados = [[celula.strip() for celula in linha.strip("|").split("|")] for linha in linhas[2:]]
    if not dados:
        return True
    medidas = set()
    for tipo, conteudo in _BLOCO.findall(texto):
        if tipo != "result-schema":
            continue
        try:
            colunas = json.loads(conteudo).get("columns") or []
        except json.JSONDecodeError:
            continue
        medidas |= {coluna.get("name") for coluna in colunas if isinstance(coluna, dict) and coluna.get("role") == "measure"}
    indices = [posicao for posicao, nome in enumerate(cabecalho) if nome in medidas]
    if not indices:
        return False
    return all(_nulo_ou_zero(linha[posicao] if posicao < len(linha) else "") for linha in dados for posicao in indices)


def _nulo_ou_zero(celula: str) -> bool:
    if celula.casefold() in {"", "none", "null", "nan", "<na>"}:
        return True
    try:
        return float(celula.replace(",", "")) == 0
    except ValueError:
        return False


def catalogo(cliente: ClienteMcp, data_dir: Path = DATA_DIR) -> str:
    arquivo = data_dir / "ons" / "catalogo.txt"
    if _recente(arquivo, _VALIDADE_CATALOGO):
        return _compactar_catalogo(arquivo.read_text(encoding="utf-8"))
    try:
        texto, erro = cliente.chamar("listar_datasets", {})
    except ErroOns:
        if arquivo.is_file():
            return _compactar_catalogo(arquivo.read_text(encoding="utf-8"))
        raise
    texto = _RODAPE_TEMPORAL.sub("\n", texto).strip()
    if erro or not texto:
        raise ErroOns("O servidor do ONS não devolveu o catálogo de datasets.")
    gravar_texto(arquivo, texto)
    return _compactar_catalogo(texto)


def _compactar_catalogo(texto: str) -> str:
    return re.sub(r"^- (?:\[ONS\] )?`([^`]+)`", r"- \1", texto, flags=re.MULTILINE)


def contrato(cliente: ClienteMcp, nome: str, data_dir: Path = DATA_DIR) -> tuple[str, bool]:
    arquivo = data_dir / "ons" / "contratos" / f"{_nome_arquivo(nome)}.txt"
    if nome and _recente(arquivo, _VALIDADE_CONTRATO):
        return arquivo.read_text(encoding="utf-8"), False
    texto, erro = cliente.chamar("descrever_dataset", {"nome_dataset": nome})
    if erro or texto.lstrip().startswith("[ERRO]") or not texto.strip():
        return texto, True
    texto = compactar_contrato(texto)
    gravar_texto(arquivo, texto)
    return texto, False


def empresa(cliente: ClienteMcp, agente: str, hoje: date, data_dir: Path = DATA_DIR) -> str:
    nome = re.sub(r"[^\w .&-]+", "", unicodedata.normalize("NFKD", agente).encode("ascii", "ignore").decode()).strip()
    if not nome:
        return ""
    arquivo = data_dir / "ons" / f"empresa-{_nome_arquivo(nome.lower())}.txt"
    if _recente(arquivo, _VALIDADE_CONTRATO):
        return arquivo.read_text(encoding="utf-8")
    sql = f"""WITH usinas AS (
  SELECT DISTINCT ceg, nom_usina, nom_estado
  FROM read_parquet('s3://ons-aws-prod-opendata/dataset/capacidade-geracao/*.parquet', union_by_name=true)
  WHERE strip_accents(nom_agenteoperador) ILIKE '%{nome}%' OR strip_accents(nom_agenteproprietario) ILIKE '%{nome}%'
), conjuntos AS (
  SELECT DISTINCT ceg, nom_conjunto, id_ons_conjunto
  FROM read_parquet('s3://ons-aws-prod-opendata/dataset/usina_conjunto/*.parquet', union_by_name=true)
  WHERE TRY_CAST(dat_fimrelacionamento AS DATE) IS NULL OR TRY_CAST(dat_fimrelacionamento AS DATE) >= DATE '{hoje}'
)
SELECT c.nom_conjunto, c.id_ons_conjunto, u.nom_usina, u.ceg, u.nom_estado
FROM usinas u LEFT JOIN conjuntos c USING (ceg)
ORDER BY 1, 3"""
    try:
        texto, erro = cliente.chamar("executar_sql", {"sql_query": sql, "limit": _LIMITE_LINHAS})
    except ErroOns:
        return arquivo.read_text(encoding="utf-8") if arquivo.is_file() else ""
    texto = _BLOCO.sub("", _RODAPE_TEMPORAL.sub("\n", texto)).strip()
    if erro or texto.startswith("[ERRO]"):
        return arquivo.read_text(encoding="utf-8") if arquivo.is_file() else ""
    texto = re.sub(r"-{3,}", "---", re.sub(r" {2,}", " ", texto))
    cegs = sorted(set(_CEG.findall(texto)))
    conjuntos = sorted(set(_CONJUNTO.findall(texto)))
    bloco = ""
    if cegs:
        bloco = _EMPRESA.format(
            agente=agente,
            dia=f"{hoje:%d/%m/%Y}",
            cegs=", ".join(f"'{ceg}'" for ceg in cegs),
            conjuntos=", ".join(f"'{conjunto}'" for conjunto in conjuntos) or "sem conjunto",
            tabela=texto,
        )
    gravar_texto(arquivo, bloco)
    return bloco


def compactar_contrato(texto: str) -> str:
    texto = _BLOCO.sub("", _RODAPE_TEMPORAL.sub("\n", texto))
    cabeca, achou, resto = texto.partition("\nschema:")
    if not achou:
        return texto.strip()
    esquema, *secoes = re.split(r"\n(?=## )", resto)
    partes = [cabeca.strip(), "Colunas (nome tipo papel: descrição):", *_colunas(esquema)]
    for secao in secoes:
        titulo, _, corpo = secao.partition("\n")
        titulo = titulo.removeprefix("## ").strip()
        chave = titulo.casefold()
        if chave.startswith("semantica"):
            partes += _semantica(corpo)
        elif chave.startswith("instrucoes"):
            partes += ["Instruções críticas:", *_instrucoes(corpo)]
        else:
            partes += [f"{titulo}:", *_sem_vazias(corpo)]
    return "\n".join(parte for parte in partes if parte)


def _colunas(esquema: str) -> list[str]:
    colunas: list[dict] = []
    for linha in esquema.splitlines():
        limpa = linha.strip()
        if limpa.startswith("- name:"):
            colunas.append({"name": limpa[len("- name:") :].strip()})
        elif colunas and ":" in limpa:
            chave, _, valor = limpa.partition(":")
            colunas[-1].setdefault(chave.strip(), valor.strip())
    linhas = []
    for coluna in colunas:
        linha = f"- {coluna['name']} {coluna.get('type', '')} {coluna.get('role', '').lower()}: "
        linha += _curto(coluna.get("description", ""))
        if coluna.get("valid_values"):
            linha += f" Valores: {coluna['valid_values']}."
        if coluna.get("cast"):
            linha += f" Use {coluna['cast']}."
        linhas.append(linha)
    return linhas


def _curto(texto: str) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= _LIMITE_DESCRICAO:
        return texto
    return texto[:_LIMITE_DESCRICAO].rsplit(" ", 1)[0] + "…"


def _semantica(corpo: str) -> list[str]:
    unidades = re.findall(r"^- `([^`]+)` \(([^,)]+)", corpo, flags=re.MULTILINE)
    padroes = re.findall(r"^- `([^`]+)` -- saida: ([^\n]*)\n\s*```sql\n(.*?)\n\s*```", corpo, flags=re.MULTILINE | re.DOTALL)
    linhas = []
    if unidades:
        linhas.append("Unidades das métricas: " + "; ".join(f"{nome} {unidade}" for nome, unidade in unidades) + ".")
    if padroes:
        linhas.append(
            "Padrões SQL prontos ({source} é o parquet_source; troque {year}, {start}, {end} e {subsistema} "
            "pelos valores reais):"
        )
        formatos: dict[str, list[tuple[str, str, str]]] = {}
        for nome, saida, sql in padroes:
            sql = " ".join(sql.split())
            formatos.setdefault(re.sub(r"\bval_\w+", "val_*", sql) + saida, []).append((nome, saida.strip(), sql))
        for grupo in formatos.values():
            nome, saida, sql = grupo[0]
            linha = f"- {nome} (saída {saida}): {sql}"
            if len(grupo) > 1:
                linha += " Mesmo formato, trocando a coluna val_: " + ", ".join(outro for outro, _, _ in grupo[1:]) + "."
            linhas.append(linha)
    return linhas


def _instrucoes(corpo: str) -> list[str]:
    grupos: dict[str, list[str]] = {}
    soltas: list[str] = []
    for linha in _sem_vazias(corpo):
        achado = re.match(r"- \[([^\]]+)\]\s*(.*)", linha)
        if achado:
            colunas = grupos.setdefault(achado[2], [])
            if achado[1] not in colunas:
                colunas.append(achado[1])
        else:
            soltas.append(linha)
    return [f"- [{', '.join(colunas)}] {texto}" for texto, colunas in grupos.items()] + soltas


def _sem_vazias(texto: str) -> list[str]:
    return [linha.strip() for linha in texto.splitlines() if linha.strip()]


def reescrever_periodo(sql: str, listar: Callable[[str], list[str]], hoje: date) -> str:
    periodo = _periodo(sql, hoje)
    if periodo is None:
        return sql
    inicio, fim = (periodo[0].year, periodo[0].month), (periodo[1].year, periodo[1].month)

    def trocar(achado: re.Match) -> str:
        pasta = achado[1]
        datados = []
        for chave in listar(pasta):
            periodo_arquivo = _ARQUIVO_PERIODO.search(chave)
            if not periodo_arquivo:
                return achado[0]
            ano, mes = int(periodo_arquivo[1]), int(periodo_arquivo[2]) if periodo_arquivo[2] else None
            datados.append((ano, mes, chave))
        if not datados or fim > max((ano, mes or 12) for ano, mes, _ in datados):
            return achado[0]
        escolhidos = [
            chave
            for ano, mes, chave in datados
            if (inicio[0] <= ano <= fim[0] if mes is None else inicio <= (ano, mes) <= fim)
        ]
        if not escolhidos or len(escolhidos) == len(datados):
            return achado[0]
        return "read_parquet([" + ", ".join(f"'s3://ons-aws-prod-opendata/{chave}'" for chave in escolhidos) + "]"

    return _PARQUET.sub(trocar, sql)


def _periodo(sql: str, hoje: date) -> tuple[date, date] | None:
    inicios: list[date] = []
    fins: list[date] = []
    for operador, ano, mes, dia in _DATA.findall(sql):
        try:
            dado = date(int(ano), int(mes), int(dia))
        except ValueError:
            return None
        operador = operador.upper()
        if operador in {">", ">=", "BETWEEN"}:
            inicios.append(dado)
        elif operador == "=":
            inicios.append(dado)
            fins.append(dado)
        elif operador in {"<=", "AND"}:
            fins.append(dado)
        elif operador == "<":
            fins.append(dado - timedelta(days=1))
        else:
            return None
    for ano in _ANO.findall(sql):
        inicios.append(date(int(ano), 1, 1))
        fins.append(date(int(ano), 12, 31))
    if not inicios:
        return None
    inicio, fim = min(inicios), max(fins) if fins else hoje
    return (inicio, fim) if inicio <= fim else None


def _arquivos_s3(pasta: str, data_dir: Path) -> list[str]:
    arquivo = data_dir / "ons" / "arquivos" / f"{_nome_arquivo(pasta)}.json"
    if _recente(arquivo, _VALIDADE_ARQUIVOS):
        try:
            return json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    chaves: list[str] = []
    parametros = {"list-type": "2", "prefix": pasta}
    try:
        with httpx.Client(timeout=20.0) as http:
            while True:
                resposta = http.get(_S3, params=parametros)
                resposta.raise_for_status()
                chaves += [
                    chave
                    for chave in re.findall(r"<Key>([^<]+\.parquet)</Key>", resposta.text)
                    if "/" not in chave[len(pasta) :]
                ]
                proximo = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", resposta.text)
                if not proximo:
                    break
                parametros["continuation-token"] = proximo[1]
    except httpx.HTTPError:
        return []
    gravar_texto(arquivo, json.dumps(chaves))
    return chaves


def _recente(arquivo: Path, validade: float) -> bool:
    return arquivo.is_file() and time.time() - arquivo.stat().st_mtime < validade


def _nome_arquivo(nome: str) -> str:
    return re.sub(r"[^\w.-]+", "_", nome).strip("._")[:120] or "_"


def responder_ons(
    pergunta: str,
    historico: list[dict] | None = None,
    autor: str = "web",
    origem: str = "ons",
    data_dir: Path = DATA_DIR,
    gerador=None,
    cliente: ClienteMcp | None = None,
    hoje: date | None = None,
) -> dict:
    pergunta = " ".join(pergunta.split())
    if not pergunta:
        raise ValueError("Escreva a pergunta.")
    hoje = hoje or date.today()
    turnos = gpt.turnos(historico)
    conversa = "\n".join(f"{anterior}\n{resposta}" for anterior, resposta in turnos)
    regras = hashlib.sha256((_SISTEMA + _AVISO_VAZIO + _AVISO_CONVERSAO + _INSISTIR).encode("utf-8")).hexdigest()[:16]
    assinatura = f"ons:{hoje.isoformat()}:{regras}"
    identificador = "ons-" + cache.chave(pergunta, assinatura, conversa)
    inicio = time.perf_counter()
    guardado = cache.ler(data_dir, identificador)
    if guardado is not None:
        medida = {"duracao_ms": _ms(inicio), "passos": 0, "ms_gpt": 0, "ms_ons": 0, "repeticoes": 0, "etapas": []}
        return _concluir(data_dir, {**guardado, **medida, "cache": True}, autor, origem, bool(turnos))

    cliente = cliente or _cliente_compartilhado()
    resultado = _conversar(pergunta, turnos, hoje, cliente, gerador or gerar_openai_ferramentas, data_dir)
    resultado["duracao_ms"] = _ms(inicio)
    if resultado["fontes"]:
        cache.gravar(data_dir, identificador, resultado)
    return _concluir(data_dir, {**resultado, "cache": False}, autor, origem, bool(turnos))


def _ms(inicio: float) -> int:
    return round((time.perf_counter() - inicio) * 1000)


def _conversar(pergunta, turnos, hoje: date, cliente: ClienteMcp, gerador, data_dir: Path) -> dict:
    etapas: list[dict] = []
    inicio = time.perf_counter()
    texto_empresa = ""
    if ONS_AGENTE:
        def ler_empresa() -> tuple[str, int]:
            comeco = time.perf_counter()
            return empresa(cliente, ONS_AGENTE, hoje, data_dir), _ms(comeco)

        with ThreadPoolExecutor(max_workers=1) as grupo:
            pedido = grupo.submit(ler_empresa)
            texto_catalogo = catalogo(cliente, data_dir)
            etapas.append({"tipo": "ons", "ferramenta": "catalogo", "ms": _ms(inicio)})
            texto_empresa, ms_empresa = pedido.result()
        etapas.append({"tipo": "ons", "ferramenta": "empresa", "ms": ms_empresa})
    else:
        texto_catalogo = catalogo(cliente, data_dir)
        etapas.append({"tipo": "ons", "ferramenta": "catalogo", "ms": _ms(inicio)})
    sistema = _SISTEMA.format(catalogo=texto_catalogo, empresa=texto_empresa)
    mensagens: list[dict] = [{"role": "system", "content": sistema}]
    for anterior, resposta in turnos:
        mensagens.append({"role": "user", "content": anterior})
        mensagens.append({"role": "assistant", "content": resposta or "(sem resposta)"})
    mensagens.append(
        {
            "role": "user",
            "content": f"Data de hoje: {hoje:%d/%m/%Y} ({_DIAS[hoje.weekday()]}).\n\nPergunta: {pergunta}",
        }
    )
    fontes: dict[str, dict] = {}
    consultas: list[dict] = []
    tokens_prompt = tokens_resposta = tokens_cache = 0
    resposta = ""
    insistiu = False
    for passo in range(ONS_MAX_PASSOS + 1):
        ultimo = passo == ONS_MAX_PASSOS
        if ultimo:
            mensagens.append(
                {
                    "role": "system",
                    "content": (
                        "Limite de consultas atingido. Responda agora só com o que executar_sql já devolveu. "
                        "Se não houver dado, diga que não foi possível montar a consulta nos dados abertos do ONS."
                    ),
                }
            )
        inicio = time.perf_counter()
        saida = gerador(mensagens, not ultimo)
        chamadas = [] if ultimo else (saida.get("chamadas") or [])
        etapa = {
            "tipo": "gpt",
            "ms": _ms(inicio),
            "tokens_prompt": int(saida.get("tokens_prompt") or 0),
            "tokens_cache": int(saida.get("tokens_cache") or 0),
            "tokens_resposta": int(saida.get("tokens_resposta") or 0),
            "repeticoes": int(saida.get("repeticoes") or 0),
            "chamadas": [chamada.get("nome") or "" for chamada in chamadas],
        }
        etapas.append(etapa)
        tokens_prompt += etapa["tokens_prompt"]
        tokens_cache += etapa["tokens_cache"]
        tokens_resposta += etapa["tokens_resposta"]
        if not chamadas and not ultimo and not insistiu and not fontes and any(c.get("vazio") for c in consultas):
            insistiu = True
            mensagens.append({"role": "assistant", "content": str(saida.get("conteudo") or "")})
            mensagens.append({"role": "system", "content": _INSISTIR})
            continue
        if not chamadas:
            resposta = str(saida.get("conteudo") or "").strip()
            break
        mensagens.append(
            {
                "role": "assistant",
                "content": saida.get("conteudo") or None,
                "tool_calls": [
                    {
                        "id": chamada["id"],
                        "type": "function",
                        "function": {"name": chamada["nome"], "arguments": chamada.get("argumentos") or "{}"},
                    }
                    for chamada in chamadas
                ],
            }
        )
        def rodar(chamada: dict) -> tuple[str, dict | None, int]:
            inicio = time.perf_counter()
            texto, consulta = _executar(cliente, chamada, data_dir, hoje)
            return texto, consulta, _ms(inicio)

        if len(chamadas) == 1:
            execucoes = [rodar(chamadas[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(chamadas))) as grupo:
                execucoes = list(grupo.map(rodar, chamadas))
        for chamada, (texto, consulta, ms) in zip(chamadas, execucoes):
            if consulta is not None:
                for fonte in consulta.pop("fontes"):
                    fontes.setdefault(fonte["dataset"], fonte)
                consultas.append(consulta)
            etapas.append({"tipo": "ons", "ferramenta": chamada.get("nome") or "", "ms": ms, "caracteres": len(texto)})
            mensagens.append({"role": "tool", "tool_call_id": chamada["id"], "content": texto})
    if not resposta:
        resposta = "Não foi possível montar a consulta nos dados abertos do ONS."
    return {
        "pergunta": pergunta,
        "resposta": resposta,
        "fontes": list(fontes.values()),
        "consultas": consultas,
        "tokens_prompt": tokens_prompt,
        "tokens_cache": tokens_cache,
        "tokens_resposta": tokens_resposta,
        "modelo": OPENAI_MODEL,
        "passos": sum(1 for etapa in etapas if etapa["tipo"] == "gpt"),
        "ms_gpt": sum(etapa["ms"] for etapa in etapas if etapa["tipo"] == "gpt"),
        "ms_ons": sum(etapa["ms"] for etapa in etapas if etapa["tipo"] == "ons"),
        "repeticoes": sum(etapa.get("repeticoes", 0) for etapa in etapas),
        "etapas": etapas,
    }


def _executar(cliente: ClienteMcp, chamada: dict, data_dir: Path, hoje: date) -> tuple[str, dict | None]:
    nome = chamada.get("nome") or ""
    if nome not in _PERMITIDAS:
        return f"Ferramenta não disponível: {nome}. Use buscar_dataset, descrever_dataset ou executar_sql.", None
    try:
        argumentos = json.loads(chamada.get("argumentos") or "{}")
    except json.JSONDecodeError:
        return "Argumentos inválidos: envie um JSON.", None
    if not isinstance(argumentos, dict):
        return "Argumentos inválidos: envie um objeto JSON.", None
    consulta = None
    if nome == "descrever_dataset":
        texto, erro = contrato(cliente, str(argumentos.get("nome_dataset") or ""), data_dir)
    elif nome == "executar_sql":
        try:
            limite = max(1, min(int(argumentos.get("limit") or _LIMITE_LINHAS), _LIMITE_LINHAS))
        except (TypeError, ValueError):
            limite = _LIMITE_LINHAS
        sql = str(argumentos.get("sql_query") or "")
        recortado = reescrever_periodo(sql, lambda pasta: _arquivos_s3(pasta, data_dir), hoje)
        texto, erro = cliente.chamar(nome, {"sql_query": recortado, "limit": limite})
        erro = erro or texto.lstrip().startswith("[ERRO]")
        if erro and recortado != sql:
            texto, erro = cliente.chamar(nome, {"sql_query": sql, "limit": limite})
            erro = erro or texto.lstrip().startswith("[ERRO]")
        achadas, unidades = ler_blocos(texto)
        vazio = not erro and sem_valor(texto)
        consulta = {
            "sql": sql,
            "unidades": unidades,
            "erro": erro,
            "vazio": vazio,
            "fontes": [] if erro or vazio else achadas,
        }
        if vazio:
            texto += _AVISO_VAZIO
        if erro and "could not convert string" in texto.casefold():
            texto += _AVISO_CONVERSAO
    else:
        texto, _erro = cliente.chamar(nome, argumentos)
    if len(texto) > _LIMITE_SAIDA:
        texto = texto[:_LIMITE_SAIDA] + "\n\n[Saída cortada. Refine a consulta para trazer menos linhas.]"
    return texto or "(sem conteúdo)", consulta


def _concluir(data_dir: Path, resultado: dict, autor: str, origem: str, continuacao: bool = False) -> dict:
    fontes = resultado.get("fontes") or []
    coberto = bool(fontes)
    citacoes = (
        [{"codigo": CODIGO_ONS, "revisao": "", "secao": ", ".join(fonte["dataset"] for fonte in fontes)}]
        if coberto
        else []
    )
    consulta = registro.registrar(
        data_dir,
        {
            "autor": autor,
            "origem": origem,
            "pergunta": resultado["pergunta"],
            "coberto": coberto,
            "resposta": resultado["resposta"],
            "citacoes": citacoes,
            "consultas_sql": [item["sql"] for item in resultado.get("consultas") or []],
            "tokens_prompt": resultado.get("tokens_prompt", 0),
            "tokens_cache": resultado.get("tokens_cache", 0),
            "tokens_resposta": resultado.get("tokens_resposta", 0),
            "cache": resultado["cache"],
            "modelo": resultado.get("modelo", OPENAI_MODEL),
            "duracao_ms": resultado.get("duracao_ms", 0),
            "passos": resultado.get("passos", 0),
            "ms_gpt": resultado.get("ms_gpt", 0),
            "ms_ons": resultado.get("ms_ons", 0),
            "repeticoes": resultado.get("repeticoes", 0),
            "etapas": resultado.get("etapas") or [],
            "continuacao": continuacao,
        },
    )
    return {
        "pergunta": resultado["pergunta"],
        "resposta": resultado["resposta"],
        "coberto": coberto,
        "fontes": fontes,
        "consultas": [
            {"sql": item["sql"], "unidades": [] if item.get("vazio") else item.get("unidades") or []}
            for item in resultado.get("consultas") or []
            if not item.get("erro")
        ],
        "cache": resultado["cache"],
        "consulta": consulta,
        "aviso": AVISO_ONS,
    }


def gerar_openai_ferramentas(mensagens: list[dict], ferramentas: bool) -> dict:
    cliente = gpt.cliente("Defina OPENAI_API_KEY no arquivo .env para usar o chat ONS.")
    chamada = {"model": OPENAI_MODEL, "temperature": 0, "messages": mensagens, "tools": FERRAMENTAS}
    if not ferramentas:
        chamada["tool_choice"] = "none"
    bruta = cliente.chat.completions.with_raw_response.create(**chamada)
    resposta = bruta.parse()
    mensagem = resposta.choices[0].message
    uso = resposta.usage
    return {
        "conteudo": mensagem.content or "",
        "chamadas": [
            {"id": item.id, "nome": item.function.name, "argumentos": item.function.arguments or "{}"}
            for item in mensagem.tool_calls or []
        ],
        "tokens_prompt": int(uso.prompt_tokens if uso else 0),
        "tokens_cache": gpt.tokens_cache(uso),
        "tokens_resposta": int(uso.completion_tokens if uso else 0),
        "repeticoes": int(getattr(bruta, "retries_taken", 0) or 0),
    }
