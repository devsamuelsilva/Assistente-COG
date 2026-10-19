from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, fields

import numpy as np

from app import cache, confianca, gpt, registro
from app.arquivos import gravar_json, ler_json
from app.buscar import Acerto, _parte_seguida, buscar, escopo, expandir, tem_base
from app.chunking import Trecho, codigo_limpo, secao_base, separar_cabecalho
from app.config import (
    ACERVO_DIR,
    AUX_MODEL,
    AVISO_OPERADOR,
    CANDIDATOS,
    DATA_DIR,
    MODO_CONTEXTO,
    MODOS_CONTEXTO,
    OPENAI_MODEL,
    PROMPT_VERSAO,
    REESCREVER,
    RERANKER_MODEL,
    RERANKER_SUGERIDO,
    TOP_K,
)
from app.quadro import consulta_exata
from app.reranker import reordenar
from app.indice import Indice, assinatura_conteudo, carregar, embutir_pergunta, fingerprint_acervo
from app.ficha import SECAO as SECAO_VISAO_GERAL

# Resposta em lista (vários prazos, responsabilidades) passa de 800 tokens. Cortada no meio, o JSON fica inválido.
_MAXIMO_RESPOSTA = 2500
_CORTADA = (
    "A resposta ficou longa demais e foi cortada antes de terminar, por isso não foi exibida. "
    "Pergunte por partes (um item de cada vez) ou peça só o ponto que você precisa."
)
_RELACIONADAS = 5
_LIMITE_HISTORICO = 300
_SOBREPOSICAO_MAXIMA = 260
_SOBREPOSICAO_MINIMA = 30

_SISTEMA_V1 = """Você é o assistente técnico do COG - Centro de Operação da Geração.
Sua função é responder perguntas operacionais utilizando exclusivamente as informações contidas nos trechos de documentos fornecidos no contexto.
REGRAS OBRIGATÓRIAS:
1. Utilize somente informações explicitamente presentes nos trechos fornecidos.
2. Não utilize conhecimento externo, conhecimento geral, experiência operacional, memória, suposições ou informações aprendidas durante treinamento.
3. Não complete informações ausentes com base em lógica, prática operacional ou contexto implícito.
4. Nunca invente, estime, deduza ou assuma:
   - limites operacionais;
   - setpoints;
   - valores mínimos ou máximos;
   - tolerâncias;
   - tempos;
   - estados de equipamentos;
   - permissivos;
   - intertravamentos;
   - bloqueios;
   - proteções;
   - alarmes;
   - condições de liberação;
   - sequência de manobra;
   - ordem de comandos;
   - comandos de abertura ou fechamento;
   - partidas ou paradas;
   - etapas de procedimentos;
   - responsabilidades;
   - autorizações;
   - exceções;
   - pré-condições;
   - pós-condições;
   - ações intermediárias não descritas explicitamente.
5. Defina "coberto": true somente quando os trechos fornecidos contiverem informação explícita e suficiente para responder integralmente à pergunta.
6. Defina "coberto": false quando:
   - nenhum trecho responder diretamente à pergunta;
   - os trechos tratarem apenas de assunto relacionado;
   - faltar valor, condição, etapa ou informação necessária;
   - for necessário inferir ou deduzir qualquer informação;
   - houver ambiguidade relevante;
   - houver conflito entre documentos que não possa ser resolvido pelos próprios trechos;
   - a resposta depender de informação não fornecida no contexto.
7. Não considere uma pergunta coberta apenas porque o documento menciona o mesmo equipamento, sistema ou assunto.
8. Quando "coberto": true:
   - responda de forma objetiva e técnica;
   - mantenha fidelidade ao texto fornecido;
   - preserve valores, unidades, condições, exceções e qualificadores;
   - não amplie o alcance da informação;
   - não transforme recomendação em obrigação;
   - não transforme possibilidade em autorização;
   - não transforme uma condição específica em regra geral;
   - não acrescente explicações não sustentadas pelos trechos.
9. Quando "coberto": false:
   - não tente responder por aproximação;
   - não forneça uma resposta provável;
   - explique brevemente no campo "resposta" qual informação está ausente ou insuficiente.
Exemplos:
"Os trechos fornecidos não informam o valor solicitado."
"Os trechos fornecidos não descrevem a sequência completa da manobra."
"Há informação sobre o equipamento, mas não sobre a condição perguntada."
"Os trechos fornecidos não permitem determinar essa condição sem inferência."
10. CITAÇÕES:
    - quando "coberto": true, cite somente os trechos que sustentam diretamente a resposta;
    - não cite documentos apenas porque mencionam o mesmo assunto;
    - cada citação deve corresponder a uma informação efetivamente utilizada;
    - quando "coberto": false, "citacoes" pode ser uma lista vazia;
    - se houver informação parcial relevante, podem ser citados apenas os trechos diretamente relacionados.
11. CAMPO "secao":
    - se o trecho contiver "Seção:", copie exatamente o texto que aparece após "Seção:";
    - a linha "Dentro de:" só mostra as seções acima; não a copie para o campo "secao";
    - se não houver "Seção:", utilize somente o número do item quando disponível, por exemplo "2.4" ou "5.3.1";
    - não invente nome ou número de seção;
    - não deduza a seção pelo conteúdo do texto.
12. CAMPOS "arquivo", "codigo" e "revisao":
    - copie exatamente os valores presentes nos trechos;
    - não corrija, normalize, complete ou invente metadados;
    - se algum desses dados não estiver disponível, utilize string vazia "".
13. CONFLITO ENTRE DOCUMENTOS:
    Se dois ou mais trechos apresentarem informações diferentes:
    - não escolha arbitrariamente uma delas;
    - utilize revisão, vigência ou hierarquia somente se isso estiver explicitamente informado;
    - se não for possível resolver o conflito exclusivamente pelos trechos fornecidos, retorne "coberto": false.
14. REGRA PRINCIPAL:
    É preferível retornar "coberto": false do que produzir uma resposta baseada em inferência.
Nunca preencha lacunas documentais.
15. FORMATO DA RESPOSTA:
    Retorne exclusivamente JSON válido.
Não utilize Markdown.
Não utilize bloco de código.
Não escreva texto antes ou depois do JSON.
Não adicione campos diferentes dos definidos abaixo.
Estrutura obrigatória:
{
  "coberto": true,
  "resposta": "texto da resposta",
  "citacoes": [
    {
      "arquivo": "nome do arquivo",
      "codigo": "codigo do documento",
      "revisao": "revisao do documento",
      "secao": "secao ou item"
    }
  ]
}
Antes de gerar a resposta final, faça internamente as seguintes verificações:
- A pergunta está integralmente respondida pelos trechos?
- Cada afirmação da resposta possui suporte explícito nos trechos?
- Algum valor, condição ou etapa foi inferido?
- As citações realmente sustentam a resposta?
- Existe algum conflito entre os trechos?
- O JSON final é válido?
- O JSON contém somente as chaves "coberto", "resposta" e "citacoes"?
Se qualquer parte essencial da resposta depender de inferência, retorne "coberto": false.
16. CONVERSA DO CHAT:
Quando houver mensagens anteriores neste chat, leia a pergunta atual como continuação delas.
Um complemento curto completa ou corrige a pergunta anterior. Exemplos: "com desligamento", "e sem desligamento", "de urgência", "na SE QXB", "era alta complexidade".
Responda à pergunta completa resultante.
A conversa serve apenas para entender a pergunta. Ela não é fonte.
Não repita prazo, valor ou condição da resposta anterior se os trechos indicarem outro.
Se a pergunta nomear uma natureza do quadro (emergência, urgência, corretiva programada, implantação, testes, melhoria ou preventiva), use somente a célula dessa coluna na linha de complexidade pedida.
Não use o prazo de outra coluna da mesma linha.
Perguntar quantos dias pede o valor escrito nessa célula, inclusive quando o texto for "Inferior a 2 dias úteis" ou "Imediato".
17. CONSULTA EXATA AO QUADRO:
Quando o pedido trouxer "Consulta exata ao quadro", essas células foram copiadas sem alteração da transcrição do quadro que está nos trechos e retiradas dos trechos para não aparecerem duas vezes.
Use essas células para responder prazo ou valor do quadro e cite a seção indicada nelas.
Se houver mais de uma célula, responda somente com as que correspondem à pergunta. Se a pergunta não permitir escolher entre elas, liste as células com a linha e a coluna de cada uma.
18. VISÃO GERAL DO DOCUMENTO:
O trecho com "Seção: Visão geral do documento" é um resumo gerado automaticamente a partir das seções.
Use-o só para perguntas sobre o assunto, o objetivo ou os principais pontos de um documento, e cite essa seção.
Para valor, prazo, etapa, condição ou responsabilidade, use e cite somente a seção original. Se apenas a visão geral tratar do ponto, retorne "coberto": false."""


_SISTEMA_V2 = """Você é o assistente técnico do COG - Centro de Operação da Geração.
Responda a pergunta usando SOMENTE os trechos numerados enviados. Os trechos são a única fonte: não use conhecimento geral, prática operacional, memória nem suposição.

Classifique cada resposta em um status:
- "coberto": os trechos trazem, de forma explícita, tudo o que a pergunta pede.
- "parcial": os trechos trazem parte do que a pergunta pede. Responda só a parte sustentada e diga com clareza o que falta.
- "ausente": os trechos não respondem. Em "resposta", explique em uma frase o que não foi encontrado, sem tentar uma resposta provável.

Regras:
1. Nunca invente nem deduza valor, prazo, limite, setpoint, tolerância, estado de equipamento, permissivo, intertravamento, sequência de manobra, responsável, autorização, exceção ou pré-condição. Mencionar o mesmo equipamento ou assunto não basta: a informação pedida precisa estar escrita.
2. Preserve valores, unidades, condições, exceções e qualificadores como estão. Não transforme recomendação em obrigação, possibilidade em autorização, nem condição específica em regra geral.
3. Em quadros e tabelas, use somente a célula que cruza a linha e a coluna perguntadas. Não use o valor de outra coluna ou linha. Se o texto da célula for "Imediato" ou "Inferior a 2 dias úteis", é esse texto que responde.
4. Quando o pedido trouxer "Consulta exata ao quadro", essas células foram copiadas sem alteração do quadro que está nos trechos e retiradas dos trechos para não aparecerem duas vezes. Use-as para responder prazo ou valor do quadro e cite o trecho [n] da seção indicada nelas. Se houver mais de uma célula, responda só com as que correspondem à pergunta; se a pergunta não permitir escolher, liste as células com a linha e a coluna de cada uma.
5. O trecho "Seção: Visão geral do documento" é um resumo gerado automaticamente. Use-o só para perguntas sobre o assunto, o objetivo ou os pontos principais de um documento. Para valor, prazo, etapa, condição ou responsabilidade, use e cite a seção original; se só a visão geral tratar do ponto, use "ausente".
6. A linha "Dentro de:" só mostra as seções acima do trecho. Não é fonte e não é nome de seção.
7. Se dois trechos divergirem e os próprios trechos não disserem qual vale (revisão, vigência, hierarquia), não escolha um: use "parcial" ou "ausente" e aponte a divergência.
8. Conversa anterior: leia a pergunta atual como continuação dela. Um complemento curto ("com desligamento", "de urgência") completa a pergunta anterior. A conversa serve só para entender a pergunta, nunca como fonte, e não repita valor da resposta anterior se os trechos indicarem outro.
9. Cite em "citacoes" o número [n] de cada trecho que sustenta a resposta, e apenas desses. Em "ausente", "citacoes" pode ser vazia.
10. Escreva em português, de forma objetiva e técnica. Comece pela resposta direta, em uma ou duas frases. Se houver vários itens, casos, prazos, etapas ou responsáveis, liste um por linha, cada linha começando com "- ". Separe assuntos diferentes em parágrafos curtos, com uma linha em branco entre eles. Não use negrito, títulos nem outra formatação.
10a. Nunca escreva no texto da resposta números de trecho ("trecho [3]", "[12]") nem expressões como "quadro transcrito nos trechos". Quem lê não vê essa numeração: as fontes vão só em "citacoes".
11. Responda só com JSON, com exatamente as chaves "status", "resposta" e "citacoes".

Exemplos (fictícios, só para mostrar o formato):

Trecho [1]: "5.2 O prazo de solicitação é de 10 dias úteis para a natureza preventiva e de 3 dias úteis para a corretiva programada."
Pergunta: qual o prazo da corretiva programada?
Saída: {"status": "coberto", "resposta": "O prazo de solicitação da natureza corretiva programada é de 3 dias úteis.", "citacoes": [{"trecho": 1}]}

Trecho [1]: "5.2 Prazos de solicitação: preventiva, 10 dias úteis; corretiva programada, 3 dias úteis; urgência, 1 dia útil."
Pergunta: quais os prazos de solicitação?
Saída: {"status": "coberto", "resposta": "Os prazos de solicitação, por natureza, são:\n- Preventiva: 10 dias úteis.\n- Corretiva programada: 3 dias úteis.\n- Urgência: 1 dia útil.", "citacoes": [{"trecho": 1}]}

Trecho [1]: "7.1 A manobra de abertura do disjuntor deve ser autorizada pelo supervisor."
Pergunta: qual a sequência completa de abertura do disjuntor?
Saída: {"status": "parcial", "resposta": "A abertura do disjuntor deve ser autorizada pelo supervisor. Os trechos não descrevem a sequência dos passos da manobra.", "citacoes": [{"trecho": 1}]}

Trecho [1]: "3.4 Os relatórios são arquivados por 5 anos."
Pergunta: qual a tensão nominal do transformador?
Saída: {"status": "ausente", "resposta": "Os trechos fornecidos não informam a tensão nominal do transformador.", "citacoes": []}"""

_ESQUEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["coberto", "parcial", "ausente"]},
        "resposta": {"type": "string"},
        "citacoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"trecho": {"type": "integer"}},
                "required": ["trecho"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "resposta", "citacoes"],
    "additionalProperties": False,
}
_SISTEMA = _SISTEMA_V2 if PROMPT_VERSAO == "v2" else _SISTEMA_V1

_PLANO = """Você prepara a pergunta de um operador para uma busca em normativos operacionais (ITOM, NO e similares).
Receba a conversa anterior (pode não haver) e a pergunta atual. Responda só com JSON:
{"pergunta": "...", "tipo": "geral" ou "especifica", "consultas": ["...", "..."]}
- "pergunta": a pergunta atual completa e independente, que se entenda sem a conversa (resolva "ela", "isso", complementos curtos como "e de urgência"). Se já for completa, repita-a. Mantenha siglas, números e nomes.
- "tipo": "geral" quando a pergunta é sobre o documento como um todo (finalidade, objetivo, escopo, assunto, resumo, a quem se aplica, o que ele diz). "especifica" nos demais casos (valor, prazo, passo, responsável, definição de um termo).
- "consultas": de 1 a 3 reformulações curtas da pergunta com o vocabulário que um normativo usaria: sinônimos, sigla por extenso, o título provável da seção (por exemplo: finalidade -> objetivo; quem pode -> responsabilidades; quando se aplica -> escopo). Não responda a pergunta e não invente valor, prazo ou fato."""


# Pergunta sobre o documento como um todo (finalidade, assunto, resumo), não sobre um ponto dele.
_GERAL = re.compile(
    r"(?i)\b(finalidade|objetivo|prop[oó]sito|resum[oa]|resumir|vis[aã]o geral|principais pontos|do que (trata|se trata)"
    r"|sobre o que (\w+ )?(trata|fala|[eé])|o ?que (voc[eê] )?(pode|consegue) (me )?(dizer|falar|informar) sobre"
    r"|o ?que [eé] (a|o) (itom|no|it|norma|instru))"
)
_SECAO_ABERTURA = re.compile(r"(?i)^\d+(\.\d+)*\.?\s*(objetivo|finalidade|escopo|introdu|prop[oó]sito)")
_REFERENCIA = r"\[\d+\](?:\s*(?:a|e|,|–|-)\s*\[\d+\])*"
_ARTIGO_TRECHO = re.compile(rf"(?i)\b(o|os|no|nos|do|dos|ao|aos|pelo|pelos)\s+trechos?\s+{_REFERENCIA}")
_TRECHO_SOLTO = re.compile(rf"(?i)\btrechos?\s+{_REFERENCIA}")
_CONTRACAO = {"o": "o", "os": "o", "no": "no", "nos": "no", "do": "do", "dos": "do", "ao": "ao", "aos": "ao", "pelo": "pelo", "pelos": "pelo"}
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_MARCADOR_LISTA = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")
_STATUS = ("coberto", "parcial", "ausente")
_RECUSA_PADRAO = "Esse ponto não está no normativo indexado neste PC."
_EXTRAS_MAXIMO = 4


@dataclass
class Resultado:
    pergunta: str
    resposta: str
    coberto: bool
    citacoes: list[dict]
    tokens_prompt: int = 0
    tokens_resposta: int = 0
    cache: bool = False
    modelo: str = ""
    trechos: list[dict] = field(default_factory=list)
    desatualizado: bool = False
    consulta: str = ""
    tokens_cache: int = 0
    duracao_ms: int = 0
    ms_busca: int = 0
    ms_gpt: int = 0
    sinais: dict = field(default_factory=dict)
    confianca: dict = field(default_factory=dict)
    status: str = ""
    alertas: list[str] = field(default_factory=list)
    pergunta_busca: str = ""
    tokens_aux_prompt: int = 0
    tokens_aux_resposta: int = 0
    modelo_aux: str = ""
    ampliado: bool = False


_CAMPOS_GUARDADOS = {campo.name for campo in fields(Resultado)} - {"cache", "desatualizado", "consulta", "confianca"}


def responder(
    pergunta: str,
    autor: str = "terminal",
    origem: str = "terminal",
    data_dir=DATA_DIR,
    acervo=ACERVO_DIR,
    indice: Indice | None = None,
    gerador=None,
    embedder=None,
    historico: list[dict] | None = None,
    modo: str | None = None,
    reranker: str | None = None,
    reordenador=None,
    reescritor=None,
) -> Resultado:
    pergunta = " ".join(pergunta.split())
    if not pergunta:
        return Resultado(pergunta="", resposta="Envie a pergunta junto com a menção.", coberto=False)
    inicio = time.perf_counter()
    modo, reranker = estrategia(modo, reranker)
    turnos = gpt.turnos(historico)
    indice = indice if indice is not None else carregar(data_dir)
    desatualizado = acervo.exists() and fingerprint_acervo(acervo) != indice.fingerprint

    if reordenador is None and reranker and indice.modelo != "teste":
        reordenador = lambda consulta, textos: reordenar(reranker, consulta, textos)
    # Gerador injetado (testes) desliga a reescrita, para não chamar a API sem querer.
    if reescritor is None and gerador is None and REESCREVER:
        reescritor = reescrever_openai
    uso_aux = [0, 0]
    plano = _planejar(data_dir, pergunta, turnos, reescritor, uso_aux) if reescritor else {}
    # Só há "pergunta reescrita" quando a conversa anterior pesa: sozinha, a pergunta original é a que se busca.
    reescrita = plano.get("pergunta", "") if turnos else ""
    inicio_busca = time.perf_counter()
    acertos, arquivos = buscar_contexto(indice, pergunta, turnos, embedder, reordenador, modo, reescrita, plano)
    ms_busca = _ms(inicio_busca)
    if not acertos or (not arquivos and not tem_base(acertos)):
        resultado = Resultado(
            pergunta=pergunta,
            resposta="Esse ponto não está no normativo indexado neste PC.",
            coberto=False,
            citacoes=[],
            modelo=OPENAI_MODEL,
            desatualizado=desatualizado,
            ms_busca=ms_busca,
        )
        resultado.duracao_ms = _ms(inicio)
        _registrar(data_dir, resultado, autor, origem, bool(turnos))
        return resultado

    exata = consulta_exata(
        indice.vigentes()[0], [acerto.trecho for acerto in acertos], reescrita or _texto_busca(pergunta, turnos)
    )
    mensagens = [
        {"role": "system", "content": _SISTEMA},
        {"role": "user", "content": _pedido(pergunta, acertos, turnos, exata, reescrita)},
    ]
    identificador = _chave_cache(mensagens)
    guardado = cache.ler(data_dir, identificador)
    if guardado is not None:
        dados = {chave: valor for chave, valor in guardado.items() if chave in _CAMPOS_GUARDADOS}
        resultado = Resultado(**{**dados, "ms_busca": ms_busca, "ms_gpt": 0}, cache=True, desatualizado=desatualizado)
        resultado.tokens_aux_prompt, resultado.tokens_aux_resposta = uso_aux
        resultado.modelo_aux = AUX_MODEL if any(uso_aux) else ""
        if not resultado.sinais:
            resultado.sinais = _sinais(pergunta, acertos, arquivos, resultado, None)
        _aplicar_confianca(data_dir, resultado)
        resultado.duracao_ms = _ms(inicio)
        _registrar(data_dir, resultado, autor, origem, bool(turnos))
        return resultado

    inicio_gpt = time.perf_counter()
    saida = gpt.Saida(*(gerador or gerar_resposta)(mensagens))
    ms_gpt = _ms(inicio_gpt)
    resultado = _interpretar(
        pergunta, saida.texto, acertos, saida.tokens_prompt, saida.tokens_resposta, ordem=_ordem(acertos), extra=exata
    )
    resultado.tokens_aux_prompt, resultado.tokens_aux_resposta = uso_aux
    resultado.modelo_aux = AUX_MODEL if any(uso_aux) else ""
    resultado.pergunta_busca = reescrita
    if resultado.status == "ausente" and resultado.resposta != _CORTADA:
        resultado = _segunda_tentativa(
            resultado, saida, pergunta, turnos, indice, embedder, reordenador, reescrita, plano, acertos, exata, gerador
        )
    resultado.tokens_cache = saida.tokens_cache
    resultado.desatualizado = desatualizado
    resultado.ms_busca = ms_busca
    resultado.ms_gpt = ms_gpt
    resultado.sinais = _sinais(pergunta, acertos, arquivos, resultado, saida.logprob)
    _aplicar_confianca(data_dir, resultado)
    resultado.duracao_ms = _ms(inicio)
    if saida.fim == "stop" and _objeto_json(saida.texto):
        _lembrar(data_dir, identificador, resultado)
    _registrar(data_dir, resultado, autor, origem, bool(turnos))
    return resultado


def buscar_contexto(
    indice: Indice,
    pergunta: str,
    turnos: list[tuple[str, str]] | None = None,
    embedder=None,
    reordenador=None,
    modo: str = "trechos",
    reescrita: str = "",
    plano: dict | None = None,
) -> tuple[list[Acerto], set[str]]:
    """A busca do chat: limita ao documento citado (na pergunta ou na conversa) e devolve os trechos e esses arquivos.

    Com `reescrita` (a pergunta completa, feita pelo GPT a partir da conversa), ela substitui a soma das perguntas antigas."""
    turnos = turnos or []
    vigentes = indice.vigentes()[0]
    citados, _ = escopo(pergunta, vigentes)
    no_historico, texto_busca = escopo(reescrita or _texto_busca(pergunta, turnos), vigentes)
    arquivos = citados or no_historico
    acertos = _acertos(indice, texto_busca, embedder, reordenador, modo, arquivos)
    plano = plano or {}
    if plano.get("consultas"):
        acertos = _com_consultas(indice, acertos, plano["consultas"], embedder, arquivos)
    if arquivos and (plano.get("tipo") == "geral" or _GERAL.search(reescrita or pergunta)):
        acertos = _com_abertura(vigentes, acertos, arquivos)
    return acertos, arquivos


def _com_consultas(indice: Indice, acertos: list[Acerto], consultas: list[str], embedder, arquivos: set[str]) -> list[Acerto]:
    """Cada reformulação do planejador busca de novo; o que ela traz de novo entra depois dos acertos da pergunta.

    A pessoa pergunta com uma palavra e o normativo escreve com outra. Várias formulações cobrem o que uma só perde."""
    vistos = {id(acerto.trecho) for acerto in acertos}
    novos: list[Acerto] = []
    for consulta in consultas[:3]:
        for acerto in _acertos(indice, consulta, embedder, None, "trechos", arquivos)[:3]:
            if id(acerto.trecho) not in vistos:
                vistos.add(id(acerto.trecho))
                novos.append(acerto)
    return [*acertos, *novos[:_EXTRAS_MAXIMO]]


def _segunda_tentativa(
    resultado: Resultado,
    saida: gpt.Saida,
    pergunta: str,
    turnos: list[tuple[str, str]],
    indice: Indice,
    embedder,
    reordenador,
    reescrita: str,
    plano: dict,
    acertos: list[Acerto],
    exata: str,
    gerador,
) -> Resultado:
    """Recusou: tenta de novo com as seções inteiras dos trechos achados, se isso traz texto que ele não viu.

    Resposta cortada no meio de uma seção é a causa mais comum de recusa com a seção certa em mãos. Só vale se a nova
    resposta tiver fonte válida; senão fica a primeira. Custa uma chamada a mais, e só quando o assistente recusou."""
    try:
        mais, _ = buscar_contexto(indice, pergunta, turnos, embedder, reordenador, "secao", reescrita, plano)
    except Exception:
        return resultado
    ja = {id(acerto.trecho) for acerto in acertos}
    if not any(id(acerto.trecho) not in ja for acerto in mais):
        return resultado
    mensagens = [
        {"role": "system", "content": _SISTEMA},
        {"role": "user", "content": _pedido(pergunta, mais, turnos, exata, reescrita)},
    ]
    try:
        segunda = gpt.Saida(*(gerador or gerar_resposta)(mensagens))
    except Exception:
        return resultado
    nova = _interpretar(
        pergunta, segunda.texto, mais, segunda.tokens_prompt, segunda.tokens_resposta, ordem=_ordem(mais), extra=exata
    )
    # O custo das duas chamadas conta, aceita ou não a segunda resposta.
    nova.tokens_prompt += resultado.tokens_prompt
    nova.tokens_resposta += resultado.tokens_resposta
    resultado.tokens_prompt, resultado.tokens_resposta = nova.tokens_prompt, nova.tokens_resposta
    if nova.status == "ausente":
        return resultado
    nova.ampliado = True
    nova.pergunta_busca = resultado.pergunta_busca
    return nova


def _com_abertura(vigentes: list[Trecho], acertos: list[Acerto], arquivos: set[str]) -> list[Acerto]:
    """Pergunta geral sobre um documento citado: a visão geral e a seção de objetivo vão na frente do contexto.

    A busca por significado acha o glossário ou qualquer seção que repita a palavra; a resposta está nessas duas."""
    ja = {(acerto.trecho.arquivo, acerto.trecho.secao) for acerto in acertos}
    frente: list[Acerto] = []
    for arquivo in sorted(arquivos):
        doc = [trecho for trecho in vigentes if trecho.arquivo == arquivo]
        visao = [trecho for trecho in doc if secao_base(trecho.secao) == SECAO_VISAO_GERAL][:1]
        abertura = [trecho for trecho in doc if _SECAO_ABERTURA.match(secao_base(trecho.secao))][:2]
        for trecho in [*visao, *abertura]:
            if (trecho.arquivo, trecho.secao) not in ja:
                ja.add((trecho.arquivo, trecho.secao))
                frente.append(Acerto(trecho=trecho, coseno=1.0, bm25=1.0, rrf=1.0, termos=1, bm25_base=1.0))
    return [*frente, *acertos]


def _sinais(pergunta: str, acertos: list[Acerto], arquivos: set[str], resultado: Resultado, logprob: float | None) -> dict:
    citadas = {(citacao["arquivo"], citacao["secao"]) for citacao in resultado.citacoes}
    posicoes = [
        posicao
        for posicao, acerto in enumerate(acertos)
        if (acerto.trecho.arquivo, secao_base(acerto.trecho.secao)) in citadas
    ]
    return {
        "coseno_max": round(max((acerto.coseno for acerto in acertos), default=0.0), 4),
        "bm25_max": round(max((acerto.bm25_base for acerto in acertos), default=0.0), 4),
        "termos": max((acerto.termos for acerto in acertos), default=0),
        "cit_coseno": round(max((acertos[posicao].coseno for posicao in posicoes), default=0.0), 4),
        "cit_posicao": min(posicoes, default=len(acertos)),
        "citacoes": len(resultado.citacoes),
        "documentos": len({acerto.trecho.arquivo for acerto in acertos}),
        "doc_citado": bool(arquivos),
        "pergunta_palavras": len(pergunta.split()),
        "resposta_caracteres": len(resultado.resposta),
        "logprob": logprob,
    }


def _aplicar_confianca(data_dir, resultado: Resultado) -> None:
    resultado.confianca = confianca.avaliar(data_dir, resultado.sinais) if resultado.coberto else {}


def _chave_cache(mensagens: list[dict]) -> str:
    conteudo = json.dumps(mensagens, ensure_ascii=False, sort_keys=True).casefold()
    return hashlib.sha256(f"{OPENAI_MODEL}\n{conteudo}".encode("utf-8")).hexdigest()


def _objeto_json(texto: str) -> bool:
    try:
        return isinstance(json.loads(texto), dict)
    except (TypeError, json.JSONDecodeError):
        return False


def _ms(inicio: float) -> int:
    return round((time.perf_counter() - inicio) * 1000)


def formatar(resultado: Resultado, autor: str | None = None) -> str:
    cabecalho = f"Pergunta de {autor}: {resultado.pergunta}\n\n" if autor else ""
    aviso_indice = (
        "\n\nO acervo mudou desde a última indexação. Rode python -m app indexar para incluir a versão atual."
        if resultado.desatualizado
        else ""
    )
    if not resultado.coberto or not resultado.citacoes:
        nucleo = resultado.resposta or "Esse ponto não está no normativo indexado neste PC."
        return f"{cabecalho}{nucleo}\n\nSiga o fluxo humano do COG.{aviso_indice}\n\n{AVISO_OPERADOR}"
    fontes = "\n".join(f"- {_linha_citacao(citacao)}" for citacao in resultado.citacoes)
    parcial = "Resposta parcial. " if resultado.status == "parcial" else ""
    alertas = "".join(f"\n\nAtenção: {alerta}" for alerta in resultado.alertas)
    fluxo = "\n\nPara o que falta, siga o fluxo humano do COG." if resultado.status == "parcial" else ""
    return (
        f"{cabecalho}{parcial}{resultado.resposta.strip()}{alertas}{fluxo}\n\nFontes:\n{fontes}"
        f"{aviso_indice}\n\n{AVISO_OPERADOR}"
    )


def gerar_resposta(mensagens: list[dict]) -> gpt.Saida:
    return gerar_openai(mensagens, OPENAI_MODEL, _ESQUEMA if PROMPT_VERSAO == "v2" else None)


def gerar_openai(mensagens: list[dict], modelo: str = OPENAI_MODEL, esquema: dict | None = None) -> gpt.Saida:
    """Chama o GPT pedindo JSON. Com esquema usa saída estruturada; se o modelo não aceitar, cai para json_object e depois texto."""
    cliente = gpt.cliente("Defina OPENAI_API_KEY no arquivo .env. A indexação não usa essa chave.")

    def chamar(formato: str, logprobs: bool):
        argumentos_chamada = {
            "model": modelo,
            "temperature": 0,
            "messages": mensagens,
            "max_tokens": _MAXIMO_RESPOSTA,
        }
        if formato == "esquema":
            argumentos_chamada["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "resposta", "strict": True, "schema": esquema},
            }
        elif formato == "objeto":
            argumentos_chamada["response_format"] = {"type": "json_object"}
        if logprobs:
            argumentos_chamada["logprobs"] = True
        return cliente.chat.completions.create(**argumentos_chamada)

    formato = "esquema" if esquema is not None else "objeto"
    logprobs = True
    while True:
        try:
            resposta = chamar(formato, logprobs)
            break
        except Exception as erro:
            texto_erro = str(erro).casefold()
            if logprobs and "logprobs" in texto_erro:
                logprobs = False
            elif formato == "esquema" and any(chave in texto_erro for chave in ("json_schema", "response_format", "schema")):
                formato = "objeto"
            elif formato == "objeto" and ("response_format" in texto_erro or "json_object" in texto_erro):
                formato = "texto"
            else:
                raise
    uso = resposta.usage
    escolha = resposta.choices[0]
    return gpt.Saida(
        escolha.message.content or "",
        int(uso.prompt_tokens if uso else 0),
        int(uso.completion_tokens if uso else 0),
        gpt.tokens_cache(uso),
        escolha.finish_reason or "",
        gpt.logprob_medio(escolha),
    )


def reescrever_openai(mensagens: list[dict]) -> gpt.Saida:
    return gpt.json_simples(mensagens, AUX_MODEL)


def _planejar(data_dir, pergunta: str, turnos: list[tuple[str, str]], reescritor, uso: list[int]) -> dict:
    """O GPT barato entende a pergunta: completa a conversa, diz se é geral e a reformula com o vocabulário do normativo.

    Guardado em disco: repetir a pergunta não repete a chamada. Qualquer falha devolve vazio e a busca segue sem plano."""
    conversa = "\n\n".join(f"Usuário: {anterior}\nAssistente: {_resumo(resposta)}" for anterior, resposta in turnos)
    mensagens = [
        {"role": "system", "content": _PLANO},
        {"role": "user", "content": f"Conversa anterior:\n{conversa or '(nenhuma)'}\n\nPergunta atual:\n{pergunta}"},
    ]
    chave = hashlib.sha256(f"{AUX_MODEL}\n{_PLANO}\n{conversa}\n{pergunta}".encode("utf-8")).hexdigest()
    arquivo = data_dir / "reescrita" / f"{chave}.json"
    guardado = ler_json(arquivo)
    if isinstance(guardado, dict):
        return guardado
    try:
        saida = gpt.Saida(*reescritor(mensagens))
        dados = json.loads(saida.texto)
    except Exception:
        return {}
    uso[0] += saida.tokens_prompt
    uso[1] += saida.tokens_resposta
    if not isinstance(dados, dict):
        return {}
    consultas = [" ".join(str(item).split()) for item in dados.get("consultas") or [] if isinstance(item, str)]
    plano = {
        "pergunta": " ".join(str(dados.get("pergunta") or "").split()),
        "tipo": "geral" if str(dados.get("tipo") or "").strip().casefold() == "geral" else "especifica",
        "consultas": [consulta for consulta in consultas if consulta][:3],
    }
    if plano["pergunta"]:
        gravar_json(arquivo, plano)
    return plano


def estrategia(modo: str | None = None, reranker: str | None = None) -> tuple[str, str]:
    modo = (modo or MODO_CONTEXTO).strip().lower()
    if modo not in MODOS_CONTEXTO:
        raise ValueError(f"Modo de contexto desconhecido: {modo}.")
    reranker = (RERANKER_MODEL if reranker is None else reranker).strip()
    if reranker not in {"", RERANKER_MODEL, RERANKER_SUGERIDO}:
        raise ValueError(f"Reordenador não permitido: {reranker}. Use o do .env ou o sugerido.")
    return modo, reranker


def _acertos(
    indice: Indice, pergunta: str, embedder, reordenador=None, modo: str = "trechos", arquivos: set[str] | None = None
) -> list[Acerto]:
    pergunta = expandir(pergunta)
    if embedder is None and indice.modelo != "teste":
        consulta = embutir_pergunta(indice.modelo, pergunta)
    else:
        consulta = np.asarray(embedder([pergunta])[0], dtype=np.float32)
    return buscar(
        indice,
        pergunta,
        consulta,
        top_k=TOP_K,
        reordenador=reordenador,
        candidatos=CANDIDATOS,
        modo=modo,
        arquivos=arquivos,
    )


def _agrupar(acertos: list[Acerto]) -> list[list[Trecho]]:
    documentos: dict[str, list[Trecho]] = {}
    for acerto in acertos:
        documentos.setdefault(acerto.trecho.arquivo, []).append(acerto.trecho)
    return list(documentos.values())


def _ordem(acertos: list[Acerto]) -> list[Trecho]:
    """Os trechos na ordem em que aparecem no pedido, que é a numeração [n] que o GPT cita."""
    return [trecho for grupo in _agrupar(acertos) for trecho in grupo]


def _pedido(
    pergunta: str,
    acertos: list[Acerto],
    turnos: list[tuple[str, str]] | None = None,
    exata: str = "",
    reescrita: str = "",
) -> str:
    celulas_exatas = {linha.strip() for linha in exata.splitlines() if linha.startswith("Linha:")}
    blocos = []
    posicao = 0
    for grupo in _agrupar(acertos):
        primeiro = grupo[0]
        linha = f"Documento: arquivo={primeiro.arquivo} codigo={primeiro.codigo} revisao={primeiro.revisao or '-'}"
        titulo = _titulo_do_trecho(primeiro.texto)
        partes = [f"{linha} titulo={titulo}" if titulo else linha]
        anterior: Trecho | None = None
        for trecho in grupo:
            posicao += 1
            texto = _sem_linha_documento(trecho.texto)
            if anterior is not None and _parte_seguida(anterior.secao, trecho.secao):
                texto = _sem_sobreposicao(_sem_linha_documento(anterior.texto), texto)
            if celulas_exatas:
                texto = "\n".join(linha for linha in texto.splitlines() if linha.strip() not in celulas_exatas)
            partes.append(f"[{posicao}] {texto}")
            anterior = trecho
        blocos.append("\n\n".join(partes))
    pedido = "Trechos:\n\n" + "\n\n".join(blocos)
    if turnos:
        linhas = [f"Usuário: {anterior}\nAssistente: {_resumo(resposta)}" for anterior, resposta in turnos]
        pedido += "\n\nConversa deste chat:\n\n" + "\n\n".join(linhas)
    if exata:
        pedido += f"\n\nConsulta exata ao quadro:\n{exata}"
    if reescrita:
        pedido += f"\n\nPergunta completa (reescrita a partir da conversa):\n{reescrita}"
    return f"{pedido}\n\nPergunta atual:\n{pergunta}"


def _sem_sobreposicao(anterior: str, atual: str) -> str:
    cabecalho, corpo = separar_cabecalho(atual)
    if not cabecalho or not corpo:
        return atual
    for tamanho in range(min(_SOBREPOSICAO_MAXIMA, len(corpo), len(anterior)), _SOBREPOSICAO_MINIMA - 1, -1):
        if anterior.endswith(corpo[:tamanho]):
            return f"{cabecalho}\n{corpo[tamanho:].lstrip()}"
    return atual


def _sem_linha_documento(texto: str) -> str:
    primeira, quebra, resto = texto.partition("\n")
    return resto if quebra and primeira.startswith("Documento: ") else texto


def _titulo_do_trecho(texto: str) -> str:
    primeira = texto.partition("\n")[0]
    if not primeira.startswith("Documento: "):
        return ""
    return primeira.partition(" – ")[2].strip()


def _resumo(resposta: str) -> str:
    if len(resposta) <= _LIMITE_HISTORICO:
        return resposta
    return resposta[:_LIMITE_HISTORICO].rsplit(" ", 1)[0] + "…"


def _texto_busca(pergunta: str, turnos: list[tuple[str, str]]) -> str:
    anteriores = [anterior for anterior, _resposta in turnos]
    return " ".join([*anteriores, pergunta])


def _assinatura_resposta(indice: Indice, modo: str = "trechos", reranker: str = "") -> str:
    prompt = hashlib.sha256(_SISTEMA.encode("utf-8")).hexdigest()
    assinatura = f"{assinatura_conteudo(indice)}\n{prompt}"
    if modo != "trechos" or reranker:
        assinatura += f"\nmodo={modo}\nreranker={reranker}\ncandidatos={CANDIDATOS}\ntop_k={TOP_K}"
    return assinatura


def _sem_referencias(texto: str) -> str:
    """Tira da resposta a numeração interna dos trechos ("nos trechos [3] a [11]"), que quem lê não vê.

    O prompt já pede para não escrever isso. Se escapar, "o trecho [1] diz" vira "o documento diz" e o resto some."""
    def trocar(achado: re.Match) -> str:
        palavra = f"{_CONTRACAO[achado.group(1).casefold()]} documento"
        return palavra[0].upper() + palavra[1:] if achado.group(1)[0].isupper() else palavra

    texto = _ARTIGO_TRECHO.sub(trocar, texto)
    texto = _TRECHO_SOLTO.sub("documento", texto)
    texto = re.sub(rf"\s*\(\s*{_REFERENCIA}\s*\)", "", texto)
    texto = re.sub(rf"[ \t]*{_REFERENCIA}", "", texto)
    return re.sub(r"[ \t]+([.,;:])", r"\1", texto)


def _interpretar(
    pergunta: str,
    bruto: str,
    acertos: list[Acerto],
    prompt: int,
    resposta_tokens: int,
    ordem: list[Trecho] | None = None,
    extra: str = "",
) -> Resultado:
    trechos = [acerto.trecho for acerto in acertos]
    numerados = ordem if ordem is not None else trechos
    ilegivel = False
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        dados, ilegivel = {}, bool(str(bruto).strip())
    if not isinstance(dados, dict):
        dados = {}
    declarado = str(dados.get("status") or "").strip().casefold()
    status = declarado if declarado in _STATUS else ("coberto" if dados.get("coberto") else "ausente")
    texto = _sem_referencias(str(dados.get("resposta") or "")).strip()
    explicacao = texto if declarado == "ausente" else ""
    citacoes = _citacoes(dados.get("citacoes") or [], numerados, trechos)
    alertas: list[str] = []
    if status != "ausente" and citacoes and texto:
        citacoes, alertas = _conferir_valores(pergunta, texto, citacoes, trechos, extra)
    if status == "ausente" or not citacoes or not texto:
        # Resposta sem fonte válida não é aceita. Só a explicação de "ausente" do próprio modelo é mantida.
        status, citacoes, alertas = "ausente", [], []
        if ilegivel and not explicacao:
            # Não é "fora do normativo": o modelo respondeu, mas a resposta veio cortada ou malformada.
            texto = _CORTADA
        else:
            texto = (explicacao or _RECUSA_PADRAO) + _secoes_relacionadas(trechos)
    return Resultado(
        pergunta=pergunta,
        resposta=texto,
        coberto=status in ("coberto", "parcial"),
        citacoes=citacoes,
        tokens_prompt=prompt,
        tokens_resposta=resposta_tokens,
        modelo=OPENAI_MODEL,
        trechos=[
            {"arquivo": trecho.arquivo, "secao": trecho.secao, "codigo": trecho.codigo, "revisao": trecho.revisao}
            for trecho in trechos
        ],
        status=status,
        alertas=alertas,
    )


def _citacoes(itens: list, numerados: list[Trecho], trechos: list[Trecho]) -> list[dict]:
    """Cita pelo número do trecho enviado. Aceita também o formato antigo (arquivo e seção)."""
    citacoes: list[dict] = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        valida = None
        numero = item.get("trecho")
        if isinstance(numero, int) and not isinstance(numero, bool):
            if 1 <= numero <= len(numerados):
                valida = _citacao_do_trecho(numerados[numero - 1])
        elif numero is None:
            valida = _validar_citacao(item, trechos)
        if valida is not None and valida not in citacoes:
            citacoes.append(valida)
    return citacoes


def _citacao_do_trecho(trecho: Trecho) -> dict:
    return {
        "arquivo": trecho.arquivo,
        "codigo": trecho.codigo,
        "revisao": trecho.revisao,
        "secao": secao_base(trecho.secao),
    }


def _valores(texto: str) -> set[str]:
    achados: set[str] = set()
    for bruto in _NUMERO.findall(texto):
        achados.add(bruto)
        achados.add(re.sub(r"[.,]", "", bruto))
    return achados


def _conferir_valores(
    pergunta: str, resposta: str, citacoes: list[dict], trechos: list[Trecho], extra: str = ""
) -> tuple[list[dict], list[str]]:
    """Todo número da resposta deve aparecer nos trechos citados. Se está em outro trecho enviado, cita-o também."""
    citados = [
        trecho
        for trecho in trechos
        if any(secao_base(trecho.secao) == citacao["secao"] and trecho.arquivo == citacao["arquivo"] for citacao in citacoes)
    ]
    suportados = _valores(" ".join(trecho.texto for trecho in citados)) | _valores(pergunta) | _valores(extra)
    citacoes = list(citacoes)
    alertas: list[str] = []
    for bruto in dict.fromkeys(_NUMERO.findall(_MARCADOR_LISTA.sub("", resposta))):
        simples = re.sub(r"[.,]", "", bruto)
        if bruto in suportados or simples in suportados:
            continue
        outro = next((trecho for trecho in trechos if bruto in _valores(trecho.texto) or simples in _valores(trecho.texto)), None)
        if outro is not None:
            adicional = _citacao_do_trecho(outro)
            if adicional not in citacoes:
                citacoes.append(adicional)
            continue
        alertas.append(f"o valor {bruto} da resposta não foi encontrado literalmente nos trechos enviados. Confira no normativo.")
    return citacoes, alertas


def _secoes_relacionadas(trechos: list[Trecho]) -> str:
    por_documento: dict[str, list[str]] = {}
    total = 0
    for trecho in trechos:
        secao = secao_base(trecho.secao)
        if total >= _RELACIONADAS or secao in ("Documento", SECAO_VISAO_GERAL) or secao.startswith("Sumário"):
            continue
        secoes = por_documento.setdefault(codigo_limpo(trecho.codigo) or trecho.arquivo, [])
        if secao not in secoes:
            secoes.append(secao)
            total += 1
    grupos = [f"{', '.join(secoes)} ({codigo})" for codigo, secoes in por_documento.items() if secoes]
    if not grupos:
        return ""
    return "\nSeções relacionadas encontradas, para conferir no normativo: " + "; ".join(grupos) + "."


def _validar_citacao(item: dict, trechos) -> dict | None:
    arquivo = str(item.get("arquivo") or "").casefold()
    secao = str(item.get("secao") or "").casefold().strip()
    if not secao:
        return None
    candidatos = []
    for trecho in trechos:
        secao_trecho = secao_base(trecho.secao).casefold()
        mesma_secao = secao_trecho == secao or secao_trecho.startswith(secao) or secao.startswith(secao_trecho)
        if not mesma_secao:
            continue
        if arquivo and trecho.arquivo.casefold() != arquivo:
            continue
        candidatos.append(trecho)
    if not candidatos:
        return None
    bases = {secao_base(trecho.secao) for trecho in candidatos}
    arquivos = {trecho.arquivo for trecho in candidatos}
    if len(bases) != 1 or len(arquivos) != 1:
        return None
    trecho = candidatos[0]
    return {
        "arquivo": trecho.arquivo,
        "codigo": trecho.codigo,
        "revisao": trecho.revisao,
        "secao": secao_base(trecho.secao),
    }


def _linha_citacao(citacao: dict) -> str:
    revisao = f", revisão {citacao['revisao']}" if citacao.get("revisao") else ""
    codigo = f"{citacao['codigo']}{revisao}" if citacao.get("codigo") else citacao["arquivo"]
    return f"{codigo}, seção {citacao['secao']} ({citacao['arquivo']})"


def _lembrar(data_dir, identificador: str, resultado: Resultado) -> None:
    payload = {
        "pergunta": resultado.pergunta,
        "resposta": resultado.resposta,
        "coberto": resultado.coberto,
        "citacoes": resultado.citacoes,
        "tokens_prompt": resultado.tokens_prompt,
        "tokens_cache": resultado.tokens_cache,
        "tokens_resposta": resultado.tokens_resposta,
        "modelo": resultado.modelo,
        "trechos": resultado.trechos,
        "sinais": resultado.sinais,
        "status": resultado.status,
        "alertas": resultado.alertas,
        "pergunta_busca": resultado.pergunta_busca,
        "ampliado": resultado.ampliado,
    }
    cache.gravar(data_dir, identificador, payload)


def _documentos_da_consulta(resultado: Resultado) -> tuple[list[str], list[str]]:
    """Documentos citados e documentos consultados. A pergunta pertence ao primeiro citado; recusada, ao mais provável."""
    citados = list(dict.fromkeys(c.get("codigo") or c.get("arquivo") or "" for c in resultado.citacoes))
    consultados = list(dict.fromkeys(t.get("codigo") or t.get("arquivo") or "" for t in resultado.trechos))
    return [d for d in consultados if d], [d for d in (citados or consultados[:1]) if d]


def _registrar(data_dir, resultado: Resultado, autor: str, origem: str, continuacao: bool = False) -> None:
    consultados, principal = _documentos_da_consulta(resultado)
    resultado.consulta = registro.registrar(
        data_dir,
        {
            "autor": autor,
            "origem": origem,
            "pergunta": resultado.pergunta,
            "documentos": consultados,
            "documento_principal": principal,
            "coberto": resultado.coberto,
            "resposta": resultado.resposta,
            "citacoes": resultado.citacoes,
            "tokens_prompt": resultado.tokens_prompt,
            "tokens_cache": resultado.tokens_cache,
            "tokens_resposta": resultado.tokens_resposta,
            "cache": resultado.cache,
            "modelo": resultado.modelo,
            "continuacao": continuacao,
            "duracao_ms": resultado.duracao_ms,
            "ms_busca": resultado.ms_busca,
            "ms_gpt": resultado.ms_gpt,
            "sinais": resultado.sinais,
            "confianca": resultado.confianca,
            "status": resultado.status,
            "alertas": resultado.alertas,
            "pergunta_busca": resultado.pergunta_busca,
            "ampliado": resultado.ampliado,
            "tokens_aux_prompt": resultado.tokens_aux_prompt,
            "tokens_aux_resposta": resultado.tokens_aux_resposta,
            "modelo_aux": resultado.modelo_aux,
        },
    )
