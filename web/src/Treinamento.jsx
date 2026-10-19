import { useCallback, useEffect, useState } from "react"
import { carregarTreino, treinarDocumento } from "./api"
import { Progresso } from "./Carregando"
import { descricaoEtapa, dolares, formatarQuando } from "./formato"

const CAUSAS = {
  busca: "a busca não trouxe a seção",
  recusa: "recusou com a seção em mãos",
  conteudo: "errou o conteúdo",
  invencao: "respondeu o que devia recusar",
}

export default function Treinamento() {
  const [estado, setEstado] = useState(null)
  const [erro, setErro] = useState("")

  const atualizar = useCallback(async (silencioso = false) => {
    try {
      setEstado(await carregarTreino({ silencioso }))
      setErro("")
    } catch (falha) {
      setErro(falha.message)
    }
  }, [])

  useEffect(() => {
    atualizar()
  }, [atualizar])

  const preparacao = estado?.preparacao
  const rodando = Boolean(preparacao?.rodando)

  useEffect(() => {
    if (!rodando) return undefined
    const espera = setTimeout(() => atualizar(true), 3000)
    return () => clearTimeout(espera)
  }, [rodando, estado, atualizar])

  async function treinar(arquivo) {
    setErro("")
    try {
      setEstado(await treinarDocumento(arquivo))
    } catch (falha) {
      setErro(falha.message)
    }
  }

  const documentos = estado?.documentos ?? []
  return (
    <>
      <section className="resumo">
        <div>
          <h2>Treinamento do assistente</h2>
          <p>
            Roda sozinho ao anexar, editar ou remover documento. Para cada seção, o GPT escreve perguntas de operador; a
            busca é conferida sem custo; as perguntas que ela não achava passam a fazer parte da busca da seção; e uma
            amostra de 15 perguntas por documento passa pela resposta completa com juiz. Só o documento que mudou é
            treinado de novo.
          </p>
        </div>
      </section>

      {erro ? <p className="aviso faixa">{erro}</p> : null}
      {rodando ? <Preparacao preparacao={preparacao} /> : null}
      {!rodando && preparacao?.erro ? <p className="aviso faixa">A última preparação parou: {preparacao.erro}</p> : null}
      {!rodando && preparacao?.avisos?.length ? (
        <section className="cartao">
          <h3>Avisos da última preparação</h3>
          <ul className="avisos">
            {preparacao.avisos.map((aviso) => (
              <li key={aviso}>{aviso}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {estado ? <Confianca confianca={estado.confianca} /> : null}

      {estado && documentos.length === 0 && !rodando ? (
        <p className="mudo faixa">Nenhum documento treinado ainda. Anexe um documento no Acervo.</p>
      ) : null}

      <div className="lista">
        {documentos.map((documento) => (
          <Documento key={documento.arquivo} documento={documento} rodando={rodando} onTreinar={treinar} />
        ))}
      </div>
    </>
  )
}

function Preparacao({ preparacao }) {
  return (
    <section className="cartao">
      <Progresso
        percentual={preparacao.percentual ?? 0}
        texto="Preparando o acervo"
        detalhe={`${descricaoEtapa(preparacao)}${preparacao.pendente ? ". Há outra preparação na fila, por mudança feita durante esta." : "."}`}
      />
    </section>
  )
}

function Confianca({ confianca }) {
  if (confianca?.ligado) {
    return (
      <p className="ok faixa">
        Selo de confiança ligado no chat: aprendido com {confianca.exemplos} respostas julgadas, separa acertos de erros
        com AUC {confianca.auc.toLocaleString("pt-BR")} na validação. Respostas com selo baixo merecem conferência no
        normativo.
      </p>
    )
  }
  return (
    <p className="mudo faixa">
      Selo de confiança desligado. {confianca?.motivo || "Ainda não treinado."} Ele aprende com as rodadas julgadas
      (amostras do treino e a prova abaixo).
    </p>
  )
}

function Documento({ documento, rodando, onTreinar }) {
  const { busca, conferencia, amostra } = documento
  const custo = Object.values(documento.custo_usd ?? {}).reduce((soma, valor) => soma + (valor || 0), 0)
  const causas = Object.entries(amostra?.causas ?? {})
  return (
    <article className="cartao">
      <header className="cartao-topo">
        <div>
          <p className="codigo">
            {documento.codigo}
            <span> · {documento.secoes} seções · treinado em {formatarQuando(documento.quando)}</span>
          </p>
          <h3>{documento.titulo}</h3>
        </div>
        <button type="button" onClick={() => onTreinar(documento.arquivo)} disabled={rodando}>
          Treinar de novo
        </button>
      </header>
      <section className="numeros">
        <article>
          <span>Busca acha a seção</span>
          <strong>{busca.total ? `${Math.round((100 * busca.ok) / busca.total)}%` : "—"}</strong>
          <span>
            {busca.ok} de {busca.total} perguntas
          </span>
        </article>
        <article>
          <span>Perguntas aprendidas</span>
          <strong>{documento.aprendidas}</strong>
        </article>
        <article>
          <span>Paráfrases das falhas</span>
          <strong>{conferencia?.total ? `${conferencia.ok} de ${conferencia.total}` : "—"}</strong>
        </article>
        <article>
          <span>Amostra com resposta</span>
          <strong>{amostra ? `${amostra.acertividade.toLocaleString("pt-BR")}%` : "—"}</strong>
          {amostra ? (
            <span>
              {amostra.acertos} de {amostra.total}
            </span>
          ) : null}
        </article>
        <article>
          <span>Custo do treino</span>
          <strong>{dolares(custo)}</strong>
        </article>
      </section>
      {causas.length > 0 ? (
        <p className="mudo">
          Erros da amostra: {causas.map(([causa, total]) => `${total} ${CAUSAS[causa] ?? causa}`).join("; ")}.
        </p>
      ) : null}
      {conferencia?.falhas?.length ? (
        <details>
          <summary>Perguntas que a busca ainda não acha ({conferencia.falhas.length})</summary>
          <ul className="avisos">
            {conferencia.falhas.map((falha) => (
              <li key={falha.pergunta}>
                {falha.pergunta} <span className="mudo">· esperada: {falha.secao}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </article>
  )
}
