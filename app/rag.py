from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

import numpy as np

from app import cache, registro
from app.buscar import Acerto, buscar, expandir, tem_base
from app.config import (
    ACERVO_DIR,
    AVISO_OPERADOR,
    DATA_DIR,
    EMBED_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    TOP_K,
)
from app.indice import Indice, _modelo, assinatura_conteudo, carregar, fingerprint_acervo

_SISTEMA = """Você é o assistente técnico do COG - Centro de Operação da Geração.
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
Perguntar quantos dias pede o valor escrito nessa célula, inclusive quando o texto for "Inferior a 2 dias úteis" ou "Imediato"."""


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
) -> Resultado:
    pergunta = " ".join(pergunta.split())
    if not pergunta:
        return Resultado(pergunta="", resposta="Envie a pergunta junto com a menção.", coberto=False)
    turnos = _turnos(historico)
    indice = indice if indice is not None else carregar(data_dir)
    desatualizado = acervo.exists() and fingerprint_acervo(acervo) != indice.fingerprint
    identificador = cache.chave(pergunta, _assinatura_resposta(indice), _texto_turnos(turnos))
    guardado = cache.ler(data_dir, identificador)
    if guardado is not None:
        guardado = dict(guardado)
        guardado.pop("desatualizado", None)
        guardado.pop("cache", None)
        resultado = Resultado(**guardado, cache=True, desatualizado=desatualizado)
        _registrar(data_dir, resultado, autor, origem)
        return resultado

    acertos = _acertos(indice, _texto_busca(pergunta, turnos), embedder)
    if not tem_base(acertos):
        resultado = Resultado(
            pergunta=pergunta,
            resposta="Esse ponto não está no normativo indexado neste PC.",
            coberto=False,
            citacoes=[],
            modelo=OPENAI_MODEL,
            desatualizado=desatualizado,
        )
        _lembrar(data_dir, identificador, resultado)
        _registrar(data_dir, resultado, autor, origem)
        return resultado

    mensagens = [
        {"role": "system", "content": _SISTEMA},
        {"role": "user", "content": _pedido(pergunta, acertos, turnos)},
    ]
    bruto, tokens_prompt, tokens_resposta = (gerador or gerar_openai)(mensagens)
    resultado = _interpretar(pergunta, bruto, acertos, tokens_prompt, tokens_resposta)
    resultado.desatualizado = desatualizado
    _lembrar(data_dir, identificador, resultado)
    _registrar(data_dir, resultado, autor, origem)
    return resultado


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
    return f"{cabecalho}{resultado.resposta.strip()}\n\nFontes:\n{fontes}{aviso_indice}\n\n{AVISO_OPERADOR}"


def gerar_openai(mensagens: list[dict]) -> tuple[str, int, int]:
    if not OPENAI_API_KEY:
        raise RuntimeError("Defina OPENAI_API_KEY no arquivo .env. A indexação não usa essa chave.")
    from openai import OpenAI

    argumentos = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        argumentos["base_url"] = OPENAI_BASE_URL
    cliente = OpenAI(**argumentos)

    def chamar(json_objeto: bool):
        argumentos_chamada = {
            "model": OPENAI_MODEL,
            "temperature": 0,
            "messages": mensagens,
        }
        if json_objeto:
            argumentos_chamada["response_format"] = {"type": "json_object"}
        return cliente.chat.completions.create(**argumentos_chamada)

    try:
        resposta = chamar(True)
    except Exception as erro:
        texto_erro = str(erro).casefold()
        if "response_format" not in texto_erro and "json_object" not in texto_erro:
            raise
        resposta = chamar(False)
    uso = resposta.usage
    texto = resposta.choices[0].message.content or ""
    return texto, int(uso.prompt_tokens if uso else 0), int(uso.completion_tokens if uso else 0)


def _acertos(indice: Indice, pergunta: str, embedder) -> list[Acerto]:
    pergunta = expandir(pergunta)
    if embedder is None and indice.modelo != "teste":
        vetor = next(iter(_modelo(EMBED_MODEL).embed([pergunta])))
        consulta = np.asarray(vetor, dtype=np.float32)
    else:
        consulta = np.asarray(embedder([pergunta])[0], dtype=np.float32)
    return buscar(indice, pergunta, consulta, top_k=TOP_K)


def _pedido(pergunta: str, acertos: list[Acerto], turnos: list[tuple[str, str]] | None = None) -> str:
    blocos = []
    for posicao, acerto in enumerate(acertos, start=1):
        trecho = acerto.trecho
        blocos.append(
            "\n".join(
                [
                    f"[{posicao}] arquivo={trecho.arquivo} codigo={trecho.codigo} "
                    f"revisao={trecho.revisao or '-'} secao={trecho.secao}",
                    trecho.texto,
                ]
            )
        )
    conversa = ""
    if turnos:
        linhas = [f"Usuário: {anterior}\nAssistente: {resposta}" for anterior, resposta in turnos]
        conversa = "Conversa deste chat:\n\n" + "\n\n".join(linhas) + "\n\n"
    return f"{conversa}Pergunta atual:\n{pergunta}\n\nTrechos:\n\n" + "\n\n".join(blocos)


def _turnos(historico: list[dict] | None) -> list[tuple[str, str]]:
    turnos: list[tuple[str, str]] = []
    for item in (historico or [])[-4:]:
        if not isinstance(item, dict):
            continue
        pergunta = " ".join(str(item.get("pergunta") or "").split())[:1500]
        resposta = " ".join(str(item.get("resposta") or "").split())[:1500]
        if pergunta:
            turnos.append((pergunta, resposta))
    return turnos


def _texto_turnos(turnos: list[tuple[str, str]]) -> str:
    return "\n".join(f"{pergunta}\n{resposta}" for pergunta, resposta in turnos)


def _texto_busca(pergunta: str, turnos: list[tuple[str, str]]) -> str:
    anteriores = [anterior for anterior, _resposta in turnos]
    return " ".join([*anteriores, pergunta])


def _assinatura_resposta(indice: Indice) -> str:
    prompt = hashlib.sha256(_SISTEMA.encode("utf-8")).hexdigest()
    return f"{assinatura_conteudo(indice)}\n{prompt}"


def _interpretar(pergunta: str, bruto: str, acertos: list[Acerto], prompt: int, resposta_tokens: int) -> Resultado:
    trechos = [acerto.trecho for acerto in acertos]
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        dados = {}
    citacoes = []
    for item in dados.get("citacoes") or []:
        if not isinstance(item, dict):
            continue
        valida = _validar_citacao(item, trechos)
        if valida is not None:
            citacoes.append(valida)
    coberto = bool(dados.get("coberto")) and bool(citacoes)
    if coberto:
        texto = str(dados.get("resposta") or "").strip()
    else:
        texto = "Esse ponto não está no normativo indexado neste PC."
    return Resultado(
        pergunta=pergunta,
        resposta=texto,
        coberto=coberto,
        citacoes=citacoes,
        tokens_prompt=prompt,
        tokens_resposta=resposta_tokens,
        modelo=OPENAI_MODEL,
        trechos=[
            {"arquivo": trecho.arquivo, "secao": trecho.secao, "codigo": trecho.codigo, "revisao": trecho.revisao}
            for trecho in trechos
        ],
    )


def _validar_citacao(item: dict, trechos) -> dict | None:
    arquivo = str(item.get("arquivo") or "").casefold()
    secao = str(item.get("secao") or "").casefold().strip()
    if not secao:
        return None
    candidatos = []
    for trecho in trechos:
        secao_trecho = _secao_base(trecho.secao).casefold()
        mesma_secao = secao_trecho == secao or secao_trecho.startswith(secao) or secao.startswith(secao_trecho)
        if not mesma_secao:
            continue
        if arquivo and trecho.arquivo.casefold() != arquivo:
            continue
        candidatos.append(trecho)
    if not candidatos:
        return None
    bases = {_secao_base(trecho.secao) for trecho in candidatos}
    arquivos = {trecho.arquivo for trecho in candidatos}
    if len(bases) != 1 or len(arquivos) != 1:
        return None
    trecho = candidatos[0]
    return {
        "arquivo": trecho.arquivo,
        "codigo": trecho.codigo,
        "revisao": trecho.revisao,
        "secao": _secao_base(trecho.secao),
    }


def _secao_base(secao: str) -> str:
    return re.sub(r" \(parte \d+\)$", "", secao)


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
        "tokens_resposta": resultado.tokens_resposta,
        "modelo": resultado.modelo,
        "trechos": resultado.trechos,
        "desatualizado": resultado.desatualizado,
    }
    cache.gravar(data_dir, identificador, payload)


def _registrar(data_dir, resultado: Resultado, autor: str, origem: str) -> None:
    registro.registrar(
        data_dir,
        {
            "autor": autor,
            "origem": origem,
            "pergunta": resultado.pergunta,
            "coberto": resultado.coberto,
            "resposta": resultado.resposta,
            "citacoes": resultado.citacoes,
            "trechos": resultado.trechos,
            "tokens_prompt": resultado.tokens_prompt,
            "tokens_resposta": resultado.tokens_resposta,
            "cache": resultado.cache,
            "modelo": resultado.modelo,
        },
    )
