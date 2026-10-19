from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import gpt
from app.arquivos import gravar_json, ler_json
from app.chunking import Trecho
from app.config import OPENAI_MODEL
from app.ficha import SECAO as SECAO_FICHA

PARALELO = 4
_MAXIMO = 5
_PEDIDO = """Você recebe um trecho de um normativo operacional do COG.
Escreva de 3 a 5 perguntas que um operador faria no dia a dia e que este trecho responde.
Regras:
- Use as palavras que o operador usaria: siglas, nomes de equipamentos, termos do campo.
- Cada pergunta precisa ser respondida pelo próprio trecho.
- Não coloque a resposta dentro da pergunta.
- Se o trecho não tiver informação útil (só cabeçalho, sumário ou histórico de revisões), devolva a lista vazia.
Responda só com JSON válido: {"perguntas": ["...", "..."]}"""


def preencher(
    data_dir: Path, trechos: list[Trecho], gerador=None, avisos: list[str] | None = None, progresso=None
) -> None:
    elegiveis = [trecho for trecho in trechos if not trecho.secao.startswith("Sumário") and trecho.secao != SECAO_FICHA]
    if not elegiveis:
        return

    feitos = [0]
    trava = threading.Lock()

    def gerar(trecho: Trecho) -> tuple[Trecho, list[str] | None, str]:
        try:
            return trecho, _perguntas(data_dir, trecho, gerador), ""
        except Exception as erro:
            return trecho, None, str(erro) or erro.__class__.__name__
        finally:
            if progresso is not None:
                with trava:
                    feitos[0] += 1
                    progresso("perguntas de busca", feitos[0], len(elegiveis))

    with ThreadPoolExecutor(max_workers=PARALELO) as executor:
        resultados = list(executor.map(gerar, elegiveis))
    falhas = [motivo for _, perguntas, motivo in resultados if perguntas is None]
    for trecho, perguntas, _ in resultados:
        trecho.perguntas = perguntas or []
    if falhas and avisos is not None:
        avisos.append(
            f"Perguntas de busca não geradas para {len(falhas)} de {len(elegiveis)} trechos ({falhas[0]}). "
            "A consulta funciona sem elas; rode a indexação de novo para completar."
        )


def gerar_openai(texto: str) -> str:
    cliente = gpt.cliente("Defina OPENAI_API_KEY no arquivo .env para gerar as perguntas de busca.")
    resposta = cliente.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        max_tokens=400,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": _PEDIDO}, {"role": "user", "content": texto}],
    )
    return resposta.choices[0].message.content or ""


def _perguntas(data_dir: Path, trecho: Trecho, gerador) -> list[str]:
    chave = hashlib.sha256("\n".join([OPENAI_MODEL, _PEDIDO, trecho.texto]).encode("utf-8")).hexdigest()
    destino = data_dir / "perguntas" / f"{chave}.json"
    guardado = ler_json(destino)
    if isinstance(guardado, dict) and isinstance(guardado.get("perguntas"), list):
        return guardado["perguntas"]
    try:
        dados = json.loads((gerador or gerar_openai)(trecho.texto))
    except json.JSONDecodeError:
        raise ValueError("a resposta do GPT não veio em JSON") from None
    bruto = dados.get("perguntas") if isinstance(dados, dict) else None
    if not isinstance(bruto, list):
        raise ValueError("a resposta do GPT veio sem a lista de perguntas")
    perguntas = [" ".join(str(item).split()) for item in bruto if str(item).strip()][:_MAXIMO]
    gravar_json(destino, {"arquivo": trecho.arquivo, "secao": trecho.secao, "perguntas": perguntas})
    return perguntas
