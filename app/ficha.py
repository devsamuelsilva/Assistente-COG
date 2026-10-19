from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app import gpt
from app.arquivos import gravar_json, ler_json
from app.chunking import Trecho, codigo_limpo, secao_base, separar_cabecalho
from app.config import OPENAI_MODEL

SECAO = "Visão geral do documento"
_LIMITE = 25_000
_CAPA = 1500
_POR_SECAO = 700
_PEDIDO = """Você recebe a capa, os títulos e o início de cada seção de um normativo operacional do COG.
Escreva a ficha do documento usando só o que está no texto.
Regras:
- "titulo": o título do normativo como aparece na capa ou no cabeçalho, sem o código.
- "objetivo": uma ou duas frases sobre o objetivo do documento.
- "pontos": as seções principais na ordem do documento, no máximo 25, cada uma com uma frase curta, de até 20 palavras, sobre o assunto que ela trata.
- Pule sumário, histórico de revisões e cabeçalhos de página.
- Não escreva valores, prazos, etapas nem responsabilidades. Diga só sobre o que a seção trata.
Responda só com JSON válido neste formato:
{"titulo": "...", "objetivo": "...", "pontos": [{"secao": "número e nome da seção", "resumo": "..."}]}"""


def gerar_ficha(data_dir: Path, trechos: list[Trecho], gerador=None) -> dict | None:
    texto = _entrada(trechos)
    if not texto:
        return None
    chave = hashlib.sha256("\n".join([OPENAI_MODEL, _PEDIDO, texto]).encode("utf-8")).hexdigest()
    destino = data_dir / "fichas" / f"{chave}.json"
    guardado = ler_json(destino)
    if isinstance(guardado, dict):
        return guardado
    try:
        dados = json.loads((gerador or gerar_openai)(texto))
    except json.JSONDecodeError:
        raise ValueError("a resposta do GPT não veio em JSON") from None
    if not isinstance(dados, dict) or not (dados.get("objetivo") or dados.get("pontos")):
        raise ValueError("a resposta do GPT veio sem objetivo e sem pontos")
    gravar_json(destino, dados)
    return dados


def gerar_openai(texto: str) -> str:
    cliente = gpt.cliente("Defina OPENAI_API_KEY no arquivo .env para gerar a visão geral dos documentos.")
    resposta = cliente.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        max_tokens=3000,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _PEDIDO}, {"role": "user", "content": texto}],
    )
    if resposta.choices[0].finish_reason == "length":
        raise ValueError("a resposta do GPT foi cortada no limite de tamanho")
    return resposta.choices[0].message.content or ""


def trecho_da_ficha(modelo: Trecho, ficha: dict, linha_documento: str) -> Trecho:
    linhas = [
        linha_documento,
        f"Seção: {SECAO}",
        "Resumo gerado automaticamente a partir das seções do normativo. "
        "Para valor, prazo, etapa ou responsabilidade, vale o texto da seção original.",
    ]
    if ficha.get("titulo"):
        linhas.append(f"Título: {_texto(ficha['titulo'])}")
    if ficha.get("objetivo"):
        linhas.append(f"Objetivo: {_texto(ficha['objetivo'])}")
    pontos = [ponto for ponto in ficha.get("pontos") or [] if isinstance(ponto, dict) and ponto.get("secao")]
    if pontos:
        linhas.append("Principais pontos:")
        linhas.extend(f"- {_texto(ponto['secao'])}: {_texto(ponto.get('resumo') or '')}".rstrip(": ") for ponto in pontos)
    codigo = codigo_limpo(modelo.codigo)
    perguntas = [
        f"Quais os principais pontos do {codigo}?",
        f"Qual o objetivo do {codigo}?",
        f"Do que trata o {codigo}?",
        f"Resumo do {codigo}",
    ]
    if ficha.get("titulo"):
        perguntas.append(f"Do que trata o normativo {_texto(ficha['titulo'])}?")
    return Trecho(
        id=f"{modelo.arquivo}:visao-geral",
        texto="\n".join(linhas),
        arquivo=modelo.arquivo,
        codigo=modelo.codigo,
        revisao=modelo.revisao,
        vigente=modelo.vigente,
        titulo=modelo.titulo,
        secao=SECAO,
        pagina="1",
        perguntas=perguntas,
    )


def _entrada(trechos: list[Trecho]) -> str:
    partes: list[str] = []
    vistas: set[str] = set()
    tamanho = 0
    for trecho in trechos:
        secao = secao_base(trecho.secao)
        if secao in vistas or secao.startswith("Sumário"):
            continue
        vistas.add(secao)
        corpo = separar_cabecalho(trecho.texto)[1].strip()
        limite = _CAPA if secao == "Documento" else _POR_SECAO
        bloco = f"Seção: {secao}\n{corpo[:limite]}"
        if tamanho + len(bloco) > _LIMITE:
            break
        partes.append(bloco)
        tamanho += len(bloco)
    return "\n\n".join(partes)


def _texto(valor) -> str:
    return " ".join(str(valor).split())
