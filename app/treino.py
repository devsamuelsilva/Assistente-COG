"""Treino automático por documento.

1. O GPT escreve perguntas de operador para cada seção (e algumas que o documento não responde).
2. A busca é conferida sem GPT: a seção esperada chega aos trechos que iriam para a resposta?
3. As perguntas que a busca não achou viram perguntas de busca da seção (aprendidas) e o índice é refeito.
4. Uma paráfrase nova de cada falha confere se o aprendizado generaliza.
5. Uma amostra pequena passa pela resposta completa com juiz, para medir acerto e alimentar o modelo de confiança.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from app import avaliacao, confianca, gpt
from app.arquivos import gravar_json, ler_json
from app.buscar import escopo, tem_base
from app.chunking import Trecho, codigo_limpo, secao_base, separar_cabecalho
from app.config import ACERVO_DIR, DATA_DIR, OPENAI_MODEL
from app.ficha import SECAO as SECAO_FICHA
from app.indice import Indice, caminho_aprendidas, chave_secao, indexar
from app.rag import _SECAO_ABERTURA, buscar_contexto, estrategia
from app.reranker import reordenar
from app.uso import custo_usd

PARALELO = 4
POR_SECAO = 2
RECUSAS = 3
GERAIS = 3
AMOSTRA_NORMAIS = 12
MAXIMO_APRENDIDAS = 10
SEMELHANCA = 0.7
_MINIMO_TEXTO = 200
_LIMITE_SECAO = 6000
_LIMITE_FICHA = 3000
_TRAVA_APRENDIDAS = threading.Lock()

_PEDIDO_SECAO = """Você recebe uma seção de um normativo operacional do COG.
Escreva até 2 perguntas que um operador faria no plantão e que esta seção responde.
Regras:
- Escreva como o operador fala: direto, com siglas e termos do campo. Não copie o título da seção nem frases inteiras do texto.
- Cada pergunta precisa ser respondida só com esta seção. Não cite o código do documento.
- Para cada pergunta, dê a resposta esperada em uma ou duas frases, tirada do texto.
- Em "valores", liste até 2 números, prazos, limites ou nomes curtos (até 3 palavras) que a resposta precisa conter, copiados do texto. Lista vazia se não houver.
- Se a seção não tiver conteúdo operacional (só histórico de revisões, referências, lista de siglas, assinaturas), devolva a lista vazia.
Responda só com JSON válido: {"perguntas": [{"pergunta": "...", "resposta": "...", "valores": ["..."]}]}"""

_PEDIDO_RECUSA = """Você recebe a visão geral e a lista de seções de um normativo operacional do COG.
Escreva 3 perguntas que um operador poderia fazer sobre o mesmo assunto geral, mas que este normativo NÃO responde: nenhuma seção trata do ponto perguntado.
Regras:
- Perguntas plausíveis no plantão e específicas (valor, prazo, responsável, procedimento).
- Não cite o código do documento.
Responda só com JSON válido: {"perguntas": ["...", "...", "..."]}"""

_PEDIDO_GERAL = """Você recebe a visão geral e a lista de seções de um normativo operacional do COG.
Escreva 3 perguntas GERAIS que um operador faria sobre o documento como um todo: a finalidade ou o objetivo, o assunto, a quem ou quando se aplica, o que ele estabelece.
Regras:
- Varie as palavras entre as perguntas (finalidade, objetivo, serve para, trata de, para que existe).
- Não cite o código do documento.
- Para cada pergunta, dê a resposta esperada em uma ou duas frases, tirada da visão geral.
Responda só com JSON válido: {"perguntas": [{"pergunta": "...", "resposta": "..."}]}"""

_PEDIDO_PARAFRASE = """Reescreva a pergunta de um operador com outras palavras, mantendo exatamente o mesmo sentido e o mesmo nível de detalhe.
Não cite códigos de documento.
Responda só com JSON válido: {"pergunta": "..."}"""


def pasta(data_dir: Path) -> Path:
    return data_dir / "treino"


def assinatura_documento(trechos: list[Trecho]) -> str:
    """Muda quando o texto do documento muda; as perguntas de busca não entram."""
    resumo = hashlib.sha256()
    for trecho in trechos:
        resumo.update(trecho.texto.encode("utf-8"))
        resumo.update(b"\n")
    return resumo.hexdigest()


def registro(data_dir: Path, arquivo: str) -> dict:
    guardado = ler_json(_caminho(data_dir, arquivo), {})
    return guardado if isinstance(guardado, dict) else {}


def estado(data_dir: Path = DATA_DIR) -> dict:
    documentos = []
    for arquivo in sorted(pasta(data_dir).glob("*.json")):
        dados = ler_json(arquivo, {})
        if not isinstance(dados, dict) or not dados.get("arquivo"):
            continue
        documentos.append({chave: valor for chave, valor in dados.items() if chave != "perguntas"})
    return {"documentos": documentos, "confianca": confianca.estado(data_dir)}


def gerar_openai(sistema: str, texto: str) -> gpt.Saida:
    cliente = gpt.cliente("Defina OPENAI_API_KEY no arquivo .env para gerar as perguntas de treino.")
    resposta = cliente.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        max_tokens=700,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": texto}],
    )
    uso = resposta.usage
    escolha = resposta.choices[0]
    return gpt.Saida(
        escolha.message.content or "",
        int(uso.prompt_tokens if uso else 0),
        int(uso.completion_tokens if uso else 0),
        gpt.tokens_cache(uso),
        escolha.finish_reason or "",
    )


def preparar(
    data_dir: Path = DATA_DIR,
    acervo: Path = ACERVO_DIR,
    forcar: set[str] | None = None,
    avisos: list[str] | None = None,
    progresso=None,
    indexador=None,
    gerador=None,
    respondedor=None,
    juiz=None,
    embedder=None,
) -> dict:
    """Ciclo completo depois de anexar ou remover documento. Só treina de novo o documento que mudou (ou o forçado)."""
    avisos = avisos if avisos is not None else []
    forcar = forcar or set()
    avisar = progresso or (lambda etapa, feitos=0, total=0: None)
    indexador = indexador or (lambda: indexar(acervo, data_dir, avisos=avisos, progresso=avisar))

    avisar("indexando")
    indice = indexador()
    modo, reordenador = _estrategia_busca(indice)
    trechos_por_arquivo: dict[str, list[Trecho]] = {}
    for trecho in indice.trechos:
        if trecho.vigente:
            trechos_por_arquivo.setdefault(trecho.arquivo, []).append(trecho)
    _remover_ausentes(data_dir, set(trechos_por_arquivo))

    pendentes = [
        arquivo
        for arquivo, trechos in trechos_por_arquivo.items()
        if arquivo in forcar or registro(data_dir, arquivo).get("assinatura") != assinatura_documento(trechos)
    ]
    treinados: list[dict] = []
    for posicao, arquivo in enumerate(pendentes):
        avisar("perguntas de treino", posicao, len(pendentes))
        try:
            treinados.append(
                treinar_documento(
                    data_dir,
                    indice,
                    arquivo,
                    gerador,
                    embedder,
                    reordenador,
                    modo,
                    progresso=lambda feitas, total, posicao=posicao: avisar(
                        "perguntas de treino", posicao + feitas / max(total, 1), len(pendentes)
                    ),
                )
            )
        except Exception as erro:
            logging.exception("Falha no treino de %s.", arquivo)
            avisos.append(f"{arquivo}: treino não concluído ({str(erro) or erro.__class__.__name__}).")

    if any(dados["aprendidas"] for dados in treinados):
        avisar("reindexando com as perguntas aprendidas")
        indice = indexador()
    com_falhas = [dados for dados in treinados if dados["busca"]["total"] > dados["busca"]["ok"]]
    for posicao, dados in enumerate(com_falhas):
        avisar("conferindo com paráfrases", posicao, len(com_falhas))
        try:
            conferir(data_dir, indice, dados, gerador, embedder, reordenador, modo)
        except Exception as erro:
            logging.exception("Falha na conferência de %s.", dados["arquivo"])
            avisos.append(f"{dados['arquivo']}: conferência não concluída ({str(erro) or erro.__class__.__name__}).")

    for posicao, dados in enumerate(treinados):
        avisar("amostra com resposta completa", posicao, len(treinados))
        try:
            amostrar(
                data_dir,
                acervo,
                indice,
                dados,
                respondedor,
                juiz,
                embedder,
                progresso=lambda feitas, total, posicao=posicao: avisar(
                    "amostra com resposta completa", posicao + feitas / max(total, 1), len(treinados)
                ),
            )
        except Exception as erro:
            logging.exception("Falha na amostra de %s.", dados["arquivo"])
            avisos.append(f"{dados['arquivo']}: amostra não concluída ({str(erro) or erro.__class__.__name__}).")
    avisar("concluído")
    return {"treinados": [dados["arquivo"] for dados in treinados], "avisos": avisos}


def treinar_documento(
    data_dir: Path,
    indice: Indice,
    arquivo: str,
    gerador=None,
    embedder=None,
    reordenador=None,
    modo: str = "trechos",
    progresso=None,
) -> dict:
    trechos = [trecho for trecho in indice.trechos if trecho.arquivo == arquivo and trecho.vigente]
    if not trechos:
        raise ValueError("documento fora do índice ou não vigente")
    codigo = codigo_limpo(trechos[0].codigo)
    secoes = _secoes(trechos)
    custo = 0.0
    feitas = [0]

    def gerar_secao(item: tuple[str, list[Trecho]]) -> tuple[str, list[dict], float]:
        secao, partes = item
        texto = f"Seção: {secao}\n" + "\n".join(separar_cabecalho(parte.texto)[1] for parte in partes)
        dados, gasto = _pedir(data_dir, _PEDIDO_SECAO, texto[:_LIMITE_SECAO], gerador)
        if progresso is not None:
            with _TRAVA_APRENDIDAS:
                feitas[0] += 1
                progresso(feitas[0], len(secoes))
        return secao, _perguntas_validas(dados), gasto

    with ThreadPoolExecutor(max_workers=PARALELO) as executor:
        geradas = list(executor.map(gerar_secao, secoes.items()))
    itens: list[dict] = []
    for secao, perguntas, gasto in geradas:
        custo += gasto
        existentes = [pergunta for parte in secoes[secao] for pergunta in parte.perguntas]
        for gerada in perguntas[:POR_SECAO]:
            if _parecida(gerada["pergunta"], existentes):
                continue
            existentes.append(gerada["pergunta"])
            itens.append(_item(arquivo, codigo, secao, gerada["pergunta"], gerada["resposta"], gerada["valores"]))
    for posicao, item in enumerate(itens):
        if posicao % 2:
            item["pergunta"] = f"{codigo} - {item['pergunta']}"

    recusas, gasto = _pedir(data_dir, _PEDIDO_RECUSA, _contexto_recusa(trechos, secoes), gerador)
    custo += gasto
    for pergunta in _lista_textos(recusas)[:RECUSAS]:
        itens.append(_item(arquivo, codigo, "", f"{codigo} - {pergunta}", "", [], deve_recusar=True))

    esperadas = _secoes_de_abertura(trechos)
    if esperadas:
        gerais, gasto = _pedir(data_dir, _PEDIDO_GERAL, _contexto_recusa(trechos, secoes), gerador)
        custo += gasto
        for geral in _perguntas_validas(gerais)[:GERAIS]:
            item = _item(arquivo, codigo, esperadas[0], f"{codigo} - {geral['pergunta']}", geral["resposta"], [])
            item["categoria"] = "geral"
            item["secoes_ok"] = esperadas
            itens.append(item)

    conferir_busca(indice, itens, embedder, reordenador, modo)
    normais = [item for item in itens if not item["deve_recusar"]]
    falhas = [item for item in normais if not item["busca_ok"]]
    assinatura = assinatura_documento(trechos)
    mudou = registro(data_dir, arquivo).get("assinatura") != assinatura
    aprendidas = aprender(data_dir, indice, arquivo, falhas, limpar=mudou)
    dados = {
        "arquivo": arquivo,
        "codigo": codigo,
        "titulo": trechos[0].titulo,
        "assinatura": assinatura,
        "quando": _agora(),
        "secoes": len(secoes),
        "busca": {"ok": len(normais) - len(falhas), "total": len(normais)},
        "aprendidas": aprendidas,
        "conferencia": None,
        "amostra": None,
        "custo_usd": {"perguntas": round(custo, 6), "conferencia": 0.0, "amostra": 0.0},
        "perguntas": itens,
    }
    gravar_json(_caminho(data_dir, arquivo), dados)
    return dados


def conferir_busca(indice: Indice, itens: list[dict], embedder=None, reordenador=None, modo: str = "trechos") -> None:
    """Sem GPT: a seção esperada está entre os trechos que iriam para a resposta, e a busca não recusaria antes?"""
    for item in itens:
        if item["deve_recusar"]:
            continue
        acertos, arquivos = buscar_contexto(indice, item["pergunta"], [], embedder, reordenador, modo)
        esperadas = item.get("secoes_ok") or [item["secao"]]
        posicao = next(
            (
                lugar
                for lugar, acerto in enumerate(acertos)
                if acerto.trecho.arquivo == item["arquivo"] and secao_base(acerto.trecho.secao) in esperadas
            ),
            None,
        )
        item["posicao"] = posicao
        item["busca_ok"] = posicao is not None and (bool(arquivos) or tem_base(acertos))


def aprender(data_dir: Path, indice: Indice, arquivo: str, falhas: list[dict], limpar: bool = False) -> int:
    """Soma as falhas às aprendidas do documento; com limpar (o texto mudou), descarta as antigas antes.

    As antigas ficam porque é graças a elas que essas perguntas passaram a ser achadas. Guarda a pergunta sem o código
    do documento, que a busca tira de qualquer forma."""
    vigentes = indice.vigentes()[0]
    with _TRAVA_APRENDIDAS:
        guardadas = ler_json(caminho_aprendidas(data_dir), {})
        guardadas = guardadas if isinstance(guardadas, dict) else {}
        if limpar:
            guardadas = {chave: valor for chave, valor in guardadas.items() if not chave.startswith(f"{arquivo}\n")}
        total = 0
        for item in falhas:
            chave = chave_secao(arquivo, item["secao"])
            lista = guardadas.setdefault(chave, [])
            pergunta = escopo(item["pergunta"], vigentes)[1]
            if len(lista) < MAXIMO_APRENDIDAS and pergunta not in lista:
                lista.append(pergunta)
                total += 1
        gravar_json(caminho_aprendidas(data_dir), guardadas)
    return total


def conferir(
    data_dir: Path, indice: Indice, dados: dict, gerador=None, embedder=None, reordenador=None, modo: str = "trechos"
) -> dict:
    """Uma paráfrase nova de cada falha da busca: mede se a pergunta aprendida ajuda além da frase exata."""
    falhas = [item for item in dados["perguntas"] if not item["deve_recusar"] and not item.get("busca_ok")]
    custo = 0.0
    parafrases: list[dict] = []
    for item in falhas:
        resposta, gasto = _pedir(data_dir, _PEDIDO_PARAFRASE, escopo(item["pergunta"], indice.vigentes()[0])[1], gerador)
        custo += gasto
        texto = " ".join(str(resposta.get("pergunta") or "").split()) if isinstance(resposta, dict) else ""
        if texto:
            parafrases.append({**item, "pergunta": texto})
    conferir_busca(indice, parafrases, embedder, reordenador, modo)
    dados["conferencia"] = {
        "ok": sum(1 for item in parafrases if item["busca_ok"]),
        "total": len(parafrases),
        "falhas": [{"pergunta": item["pergunta"], "secao": item["secao"]} for item in parafrases if not item["busca_ok"]],
    }
    dados["custo_usd"]["conferencia"] = round(custo, 6)
    gravar_json(_caminho(data_dir, dados["arquivo"]), dados)
    return dados


def amostra(itens: list[dict], normais: int = AMOSTRA_NORMAIS, recusas: int = RECUSAS) -> list[dict]:
    """Sempre as mesmas perguntas para o mesmo treino (a resposta e o juiz vêm do cache), espalhadas pelas seções."""
    def ordem(item: dict) -> str:
        return hashlib.sha256(item["id"].encode("utf-8")).hexdigest()

    por_secao: dict[str, list[dict]] = {}
    for item in sorted((item for item in itens if not item["deve_recusar"]), key=ordem):
        por_secao.setdefault(item["secao"], []).append(item)
    filas = [por_secao[secao] for secao in sorted(por_secao, key=lambda secao: ordem(por_secao[secao][0]))]
    escolhidos: list[dict] = []
    while len(escolhidos) < normais and any(filas):
        for fila in filas:
            if fila and len(escolhidos) < normais:
                escolhidos.append(fila.pop(0))
    escolhidos.extend(sorted((item for item in itens if item["deve_recusar"]), key=ordem)[:recusas])
    return escolhidos


def amostrar(
    data_dir: Path, acervo: Path, indice: Indice, dados: dict, respondedor=None, juiz=None, embedder=None, progresso=None
) -> dict:
    escolhidos = amostra(dados["perguntas"])
    if not escolhidos:
        return dados
    rodada = avaliacao.rodar(
        data_dir,
        acervo,
        gerador=respondedor,
        juiz=juiz,
        embedder=embedder,
        indice=indice,
        itens=escolhidos,
        origem="treino",
        documento=dados["codigo"],
        progresso=progresso,
    )
    resumo = rodada["resumo"]
    causas: dict[str, int] = {}
    for item in rodada["itens"]:
        if item.get("causa"):
            causas[item["causa"]] = causas.get(item["causa"], 0) + 1
    dados["amostra"] = {
        "rodada": rodada["id"],
        "total": resumo["total"],
        "acertos": resumo["acertos"],
        "acertividade": resumo["acertividade"],
        "causas": causas,
    }
    dados["custo_usd"]["amostra"] = resumo["custo_usd"]
    gravar_json(_caminho(data_dir, dados["arquivo"]), dados)
    return dados


def _estrategia_busca(indice: Indice):
    modo, reranker = estrategia()
    if reranker and indice.modelo != "teste":
        return modo, lambda consulta, textos: reordenar(reranker, consulta, textos)
    return modo, None


def _secoes(trechos: list[Trecho]) -> dict[str, list[Trecho]]:
    secoes: dict[str, list[Trecho]] = {}
    for trecho in trechos:
        secao = secao_base(trecho.secao)
        if secao.startswith("Sumário") or secao == SECAO_FICHA:
            continue
        secoes.setdefault(secao, []).append(trecho)
    return {
        secao: partes
        for secao, partes in secoes.items()
        if sum(len(separar_cabecalho(parte.texto)[1].strip()) for parte in partes) >= _MINIMO_TEXTO
    }


def _secoes_de_abertura(trechos: list[Trecho]) -> list[str]:
    """Seções que respondem a uma pergunta geral sobre o documento: a visão geral e o objetivo ou escopo."""
    nomes: list[str] = []
    for trecho in trechos:
        secao = secao_base(trecho.secao)
        if (secao == SECAO_FICHA or _SECAO_ABERTURA.match(secao)) and secao not in nomes:
            nomes.append(secao)
    return nomes


def _contexto_recusa(trechos: list[Trecho], secoes: dict[str, list[Trecho]]) -> str:
    ficha = next((trecho for trecho in trechos if trecho.secao == SECAO_FICHA), None)
    visao = separar_cabecalho(ficha.texto)[1][:_LIMITE_FICHA] if ficha else ""
    return f"Título: {trechos[0].titulo}\n\nVisão geral:\n{visao or '(sem visão geral)'}\n\nSeções:\n" + "\n".join(secoes)


def _pedir(data_dir: Path, sistema: str, texto: str, gerador) -> tuple[dict, float]:
    chave = hashlib.sha256("\n".join([OPENAI_MODEL, sistema, texto]).encode("utf-8")).hexdigest()
    destino = pasta(data_dir) / "cache" / f"{chave}.json"
    guardado = ler_json(destino)
    if isinstance(guardado, dict):
        return guardado, 0.0
    saida = gpt.Saida(*(gerador or gerar_openai)(sistema, texto))
    custo = custo_usd(OPENAI_MODEL, saida.tokens_prompt, saida.tokens_resposta, False, saida.tokens_cache) or 0.0
    try:
        dados = json.loads(saida.texto)
    except json.JSONDecodeError:
        raise ValueError("a resposta do GPT não veio em JSON") from None
    if not isinstance(dados, dict):
        raise ValueError("a resposta do GPT não veio em JSON")
    gravar_json(destino, dados)
    return dados, custo


def _perguntas_validas(dados: dict) -> list[dict]:
    """Os valores do GPT ficam de fora: ele resume ("mola carregada") ou a resposta escreve diferente ("0" e "zero"), e a
    regra literal reprovava resposta certa. No treino, quem decide é o juiz, que vê o trecho citado."""
    validas = []
    for bruto in dados.get("perguntas") or []:
        if not isinstance(bruto, dict):
            continue
        pergunta = " ".join(str(bruto.get("pergunta") or "").split())
        resposta = " ".join(str(bruto.get("resposta") or "").split())
        if pergunta and resposta:
            validas.append({"pergunta": pergunta, "resposta": resposta, "valores": []})
    return validas


def _lista_textos(dados: dict) -> list[str]:
    return [" ".join(str(item).split()) for item in dados.get("perguntas") or [] if isinstance(item, str) and item.strip()]


def _item(
    arquivo: str, codigo: str, secao: str, pergunta: str, resposta: str, valores: list[str], deve_recusar: bool = False
) -> dict:
    identificador = hashlib.sha256(f"{arquivo}\n{secao}\n{pergunta}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"treino-{identificador}",
        "pergunta": pergunta,
        "resposta_esperada": resposta,
        "documento": codigo,
        "arquivo": arquivo,
        "secao": secao,
        "referencia": "",
        "categoria": "recusa" if deve_recusar else "treino",
        "valores": valores,
        "deve_recusar": deve_recusar,
        "revisao": "",
        "busca_ok": None,
        "posicao": None,
    }


def _parecida(pergunta: str, outras: list[str]) -> bool:
    """Pergunta quase igual a uma pergunta de busca da seção não testa nada: a busca já tem esse texto."""
    palavras = _palavras(pergunta)
    if not palavras:
        return True
    for outra in outras:
        comparada = _palavras(outra)
        if comparada and len(palavras & comparada) / len(palavras | comparada) >= SEMELHANCA:
            return True
    return False


def _palavras(texto: str) -> set[str]:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").casefold()
    return {palavra for palavra in re.findall(r"\w+", sem_acento) if len(palavra) >= 3}


def _remover_ausentes(data_dir: Path, arquivos: set[str]) -> None:
    for caminho in pasta(data_dir).glob("*.json"):
        dados = ler_json(caminho, {})
        if isinstance(dados, dict) and dados.get("arquivo") and dados["arquivo"] not in arquivos:
            caminho.unlink(missing_ok=True)
    with _TRAVA_APRENDIDAS:
        guardadas = ler_json(caminho_aprendidas(data_dir), {})
        if not isinstance(guardadas, dict):
            return
        ficam = {chave: valor for chave, valor in guardadas.items() if chave.split("\n", 1)[0] in arquivos}
        if len(ficam) != len(guardadas):
            gravar_json(caminho_aprendidas(data_dir), ficam)


def _caminho(data_dir: Path, arquivo: str) -> Path:
    return pasta(data_dir) / f"{hashlib.sha256(arquivo.encode('utf-8')).hexdigest()[:16]}.json"


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")
