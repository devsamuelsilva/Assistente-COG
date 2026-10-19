from __future__ import annotations

import argparse
import sys

from app.chunking import conferir_sumario
from app.config import ACERVO_DIR, DATA_DIR, HOST, PORT
from app.indice import indexar
from app.painel import aviso_sumario
from app.rag import formatar, responder


def main(argv: list[str] | None = None) -> int:
    for saida in (sys.stdout, sys.stderr):
        reconfigure = getattr(saida, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass
    parser = argparse.ArgumentParser(prog="python -m app", description="Assistente local do COG")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("indexar", help="Lê a pasta acervo e grava o índice em disco")
    perguntar = sub.add_parser("perguntar", help="Consulta o índice pelo terminal")
    perguntar.add_argument("pergunta", nargs="+")
    sub.add_parser("servir", help="Sobe a API local do ambiente web e do Teams")
    avaliar = sub.add_parser("avaliar", help="Roda a prova e falha (código 1) se a acertividade cair")
    avaliar.add_argument("--minimo", type=float, default=0.0, help="Acertividade mínima (%%) para aprovar")
    avaliar.add_argument("--permitir-regressao", action="store_true", help="Não reprova por perguntas que pioraram")
    transcricoes = sub.add_parser("transcricoes", help="Lista as transcrições de figuras e quais já foram revisadas")
    transcricoes.add_argument("--revisar", metavar="HASH", help="Marca a transcrição como revisada")
    treinar = sub.add_parser("treinar", help="Indexa e treina os documentos novos ou alterados")
    treinar.add_argument("arquivos", nargs="*", help="Arquivos para treinar de novo mesmo sem mudança")
    args = parser.parse_args(argv)

    if args.comando == "treinar":
        from app import treino

        avisos = []
        resultado = treino.preparar(
            DATA_DIR,
            ACERVO_DIR,
            forcar=set(args.arquivos),
            avisos=avisos,
            progresso=lambda etapa, feitos=0, total=0: print(f"{etapa} {feitos}/{total}" if total else etapa, flush=True),
        )
        for documento in treino.estado(DATA_DIR)["documentos"]:
            if documento["arquivo"] not in resultado["treinados"]:
                continue
            busca, conferencia, amostra = documento["busca"], documento.get("conferencia"), documento.get("amostra")
            linha = f"{documento['codigo']}: busca {busca['ok']}/{busca['total']}, aprendidas {documento['aprendidas']}"
            if conferencia:
                linha += f", paráfrases {conferencia['ok']}/{conferencia['total']}"
            if amostra:
                linha += f", amostra {amostra['acertos']}/{amostra['total']}"
            print(f"{linha}, custo US$ {sum(documento['custo_usd'].values()):.4f}")
        for aviso in avisos:
            print(f"ATENÇÃO {aviso}")
        return 0

    if args.comando == "indexar":
        avisos: list[str] = []
        indice = indexar(ACERVO_DIR, DATA_DIR, avisos=avisos)
        vigentes = sum(1 for trecho in indice.trechos if trecho.vigente)
        print(f"Índice gravado em {DATA_DIR / 'indice'}")
        print(f"Trechos: {len(indice.trechos)} ({vigentes} vigentes)")
        for aviso in avisos:
            print(f"ATENÇÃO {aviso}")
        for arquivo, conferencia in conferir_sumario(indice.trechos).items():
            if conferencia["ausentes"]:
                print(f"ATENÇÃO {arquivo}: {aviso_sumario(conferencia)}")
            else:
                print(f"{arquivo}: as {conferencia['no_sumario']} seções do sumário estão no índice.")
        return 0
    if args.comando == "perguntar":
        try:
            resultado = responder(" ".join(args.pergunta), autor="terminal", origem="terminal")
        except RuntimeError as erro:
            print(erro)
            return 1
        print(formatar(resultado))
        print(
            f"\nTokens: prompt={resultado.tokens_prompt} resposta={resultado.tokens_resposta} cache={resultado.cache}"
        )
        return 0
    if args.comando == "avaliar":
        from app import avaliacao

        rodada = avaliacao.rodar()
        resumo = rodada["resumo"]
        print(f"Rodada {rodada['id']}: acertividade {resumo['acertividade']}% ({resumo['acertos']}/{resumo['total']})")
        print(f"Busca: recall {resumo['recall_busca']}%  MRR {resumo['mrr_busca']}")
        comparacao = rodada.get("comparacao")
        if comparacao:
            print(
                f"Contra {comparacao['anterior']}: {comparacao['variacao_acertividade']:+} pontos, "
                f"{len(comparacao['melhorias'])} melhoraram e {len(comparacao['regressoes'])} pioraram"
            )
        aprovada, motivos = avaliacao.aprovada(rodada, args.minimo, args.permitir_regressao)
        for motivo in motivos:
            print(f"REPROVADA: {motivo}")
        return 0 if aprovada else 1
    if args.comando == "transcricoes":
        from app import visao

        if args.revisar:
            visao.revisar(DATA_DIR, args.revisar)
            print("Marcada como revisada.")
            return 0
        itens = visao.listar(DATA_DIR)
        for item in itens:
            marca = "revisada" if item["revisado"] else "A REVISAR"
            print(f"{item['hash']}  {item['arquivo']} p.{item['pagina']}  {marca}")
        print(f"{sum(1 for item in itens if not item['revisado'])} de {len(itens)} sem revisão.")
        return 0
    if args.comando == "servir":
        import uvicorn

        uvicorn.run("app.server:app", host=HOST, port=PORT, reload=False)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
