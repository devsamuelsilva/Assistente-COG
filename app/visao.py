from __future__ import annotations

import base64
import json
from pathlib import Path

from app.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from app.extrair import Figura

_SEM_CONTEUDO = "SEM_CONTEUDO"
_PEDIDO = """Transcreva esta figura de um normativo operacional para texto pesquisável.
Regras:
- Copie somente o que está visível.
- Se for tabela, escreva uma linha para cada combinação de linha e coluna que tenha valor visível, neste formato:
  Linha: <nome completo da linha> | Coluna: <nome completo da coluna> | Valor: <texto da célula>
- Se uma célula ocupa várias linhas ou várias colunas, repita o mesmo valor em cada combinação coberta. Não deixe a repetição implícita.
- Não junte vários valores na mesma linha.
- Mantenha nomes, prazos, siglas e classificações como estão escritos.
- Não resuma e não explique.
- Não invente célula, valor ou classificação.
- Se a imagem não tiver texto útil, responda apenas SEM_CONTEUDO."""


def transcrever_figuras(data_dir: Path, figuras: list[Figura], gerador=None) -> dict[int, str]:
    por_pagina: dict[int, list[str]] = {}
    for figura in figuras:
        texto = _texto(data_dir, figura, gerador or gerar_openai).strip()
        if not texto or texto.upper().startswith(_SEM_CONTEUDO):
            continue
        por_pagina.setdefault(figura.pagina, []).append(texto)
    return {pagina: "\n\n".join(partes) for pagina, partes in por_pagina.items()}


def gerar_openai(figura: Figura) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("Defina OPENAI_API_KEY no arquivo .env para ler as figuras do normativo.")
    from openai import OpenAI

    argumentos = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        argumentos["base_url"] = OPENAI_BASE_URL
    cliente = OpenAI(**argumentos)
    codificado = base64.b64encode(figura.dados).decode("ascii")
    resposta = cliente.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PEDIDO},
                    {"type": "image_url", "image_url": {"url": f"data:{figura.tipo};base64,{codificado}"}},
                ],
            }
        ],
    )
    return (resposta.choices[0].message.content or "").strip()


def _texto(data_dir: Path, figura: Figura, gerador) -> str:
    destino = data_dir / "visao" / f"{figura.hash}.json"
    if destino.is_file():
        guardado = json.loads(destino.read_text(encoding="utf-8"))
        return str(guardado.get("texto") or "")
    texto = gerador(figura).strip()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(
            {"hash": figura.hash, "arquivo": figura.arquivo, "pagina": figura.pagina, "texto": texto},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return texto
