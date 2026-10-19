from __future__ import annotations

import base64
import re
from pathlib import Path

from app import gpt
from app.arquivos import gravar_json, ler_json
from app.config import OPENAI_MODEL
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
_PEDIDO_PAGINA = """Esta imagem é uma página inteira, digitalizada, de um normativo operacional. Faça a leitura (OCR) para texto pesquisável.
Regras:
- Copie o texto na ordem de leitura, mantendo a numeração dos itens (por exemplo 5.1.1) e os títulos como estão escritos.
- Se houver tabela, escreva uma linha para cada combinação de linha e coluna, neste formato:
  Linha: <nome completo da linha> | Coluna: <nome completo da coluna> | Valor: <texto da célula>
  Célula que ocupa várias linhas ou colunas repete o valor em cada combinação coberta.
- Não resuma, não explique e não invente texto ilegível: escreva [ilegível] no lugar.
- Se a imagem não tiver texto útil, responda apenas SEM_CONTEUDO."""


def transcricoes_guardadas(data_dir: Path, figuras: list[Figura]) -> dict[int, str]:
    return transcrever_figuras(data_dir, figuras, gerador=lambda figura: "", gravar=False)


def transcrever_figuras(
    data_dir: Path, figuras: list[Figura], gerador=None, gravar: bool = True, progresso=None
) -> dict[int, str]:
    por_pagina: dict[int, list[str]] = {}
    for posicao, figura in enumerate(figuras):
        if progresso is not None:
            progresso("transcrevendo quadros", posicao, len(figuras))
        texto = _texto(data_dir, figura, gerador or gerar_openai, gravar).strip()
        if not texto or texto.upper().startswith(_SEM_CONTEUDO):
            continue
        por_pagina.setdefault(figura.pagina, []).append(texto)
    return {pagina: "\n\n".join(partes) for pagina, partes in por_pagina.items()}


def gerar_openai(figura: Figura) -> str:
    cliente = gpt.cliente("Defina OPENAI_API_KEY no arquivo .env para ler as figuras do normativo.")
    codificado = base64.b64encode(figura.dados).decode("ascii")
    resposta = cliente.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PEDIDO_PAGINA if figura.sem_texto else _PEDIDO},
                    {"type": "image_url", "image_url": {"url": f"data:{figura.tipo};base64,{codificado}"}},
                ],
            }
        ],
    )
    return (resposta.choices[0].message.content or "").strip()


def listar(data_dir: Path) -> list[dict]:
    """Transcrições gravadas, para revisão humana. Quadro lido por visão deve ser conferido contra o PDF."""
    itens = []
    for arquivo in sorted((data_dir / "visao").glob("*.json")):
        dados = ler_json(arquivo)
        if not isinstance(dados, dict):
            continue
        itens.append(
            {
                "hash": dados.get("hash") or arquivo.stem,
                "arquivo": dados.get("arquivo", ""),
                "pagina": dados.get("pagina", 0),
                "revisado": bool(dados.get("revisado")),
                "texto": dados.get("texto", ""),
            }
        )
    return itens


def revisar(data_dir: Path, hash_figura: str, texto: str | None = None) -> dict:
    """Marca a transcrição como revisada e, se vier texto, grava a correção. Reindexe depois."""
    if not re.fullmatch(r"[0-9a-f]{64}", hash_figura):
        raise FileNotFoundError("Transcrição não encontrada.")
    arquivo = data_dir / "visao" / f"{hash_figura}.json"
    dados = ler_json(arquivo)
    if not isinstance(dados, dict):
        raise FileNotFoundError("Transcrição não encontrada.")
    if texto is not None:
        dados["texto"] = texto.strip()
    dados["revisado"] = True
    gravar_json(arquivo, dados)
    return dados


def _texto(data_dir: Path, figura: Figura, gerador, gravar: bool = True) -> str:
    destino = data_dir / "visao" / f"{figura.hash}.json"
    guardado = ler_json(destino)
    if isinstance(guardado, dict):
        return str(guardado.get("texto") or "")
    texto = gerador(figura).strip()
    if not gravar:
        return texto
    gravar_json(destino, {"hash": figura.hash, "arquivo": figura.arquivo, "pagina": figura.pagina, "texto": texto})
    return texto
