import { useCallback, useEffect, useState } from "react"
import { carregarAvaliacao, carregarProgresso, carregarRodada, enviarGabarito, rodarAvaliacao } from "./api"
import Carregando, { Progresso } from "./Carregando"
import { dolares, formatarQuando, ROTULOS_STATUS as ROTULOS } from "./formato"
import Leitura from "./Leitura"
import { invalidarRegistro } from "./registro"
import Revisao from "./Revisao"
import Treinamento from "./Treinamento"

const MODOS = {
  trechos: "Trechos",
  secao: "Seção inteira",
  documento: "Documento inteiro",
}

function siglaConfig(item) {
  const modo = { trechos: "trechos", secao: "seção", documento: "doc." }[item?.modo || "trechos"] ?? item?.modo
  return item?.reranker ? `${modo}+R` : modo
}

function rotuloConfig(item) {
  const modo = MODOS[item?.modo || "trechos"] ?? item?.modo
  return item?.reranker ? `${modo} + reordenador` : modo
}

export default function Avaliacao() {
  const [estado, setEstado] = useState(null)
  const [erro, setErro] = useState("")
  const [enviando, setEnviando] = useState(false)
  const [importacao, setImportacao] = useState(null)
  const [escolhida, setEscolhida] = useState("")
  const [rodada, setRodada] = useState(null)
  const [filtro, setFiltro] = useState("")
  const [categoria, setCategoria] = useState("")
  const [aba, setAba] = useState("rodadas")
  const [modo, setModo] = useState("")
  const [reranker, setReranker] = useState(null)

  const atualizar = useCallback(async (silencioso = false) => {
    try {
      setEstado(await carregarAvaliacao({ silencioso }))
      setErro("")
    } catch (falha) {
      setErro(falha.message)
    }
  }, [])

  useEffect(() => {
    atualizar()
  }, [atualizar])

  const rodando = Boolean(estado?.progresso?.rodando)
  const rodadas = estado?.rodadas ?? []
  const ultima = rodadas.at(-1)?.id ?? ""

  useEffect(() => {
    if (!rodando) return undefined
    let parado = false
    let espera = null
    const agendar = () => {
      clearTimeout(espera)
      if (!parado && !document.hidden) espera = setTimeout(consultarProgresso, 2000)
    }
    async function consultarProgresso() {
      try {
        const { progresso } = await carregarProgresso()
        if (parado) return
        if (progresso.rodando) {
          setEstado((atual) => (atual ? { ...atual, progresso } : atual))
          agendar()
        } else {
          invalidarRegistro()
          atualizar(true)
        }
      } catch {
        agendar()
      }
    }
    document.addEventListener("visibilitychange", agendar)
    agendar()
    return () => {
      parado = true
      clearTimeout(espera)
      document.removeEventListener("visibilitychange", agendar)
    }
  }, [rodando, atualizar])

  useEffect(() => {
    if (ultima) setEscolhida(ultima)
  }, [ultima])

  useEffect(() => {
    if (!escolhida) return
    let ativo = true
    carregarRodada(escolhida)
      .then((dados) => ativo && setRodada(dados))
      .catch((falha) => ativo && setErro(falha.message))
    return () => {
      ativo = false
    }
  }, [escolhida])

  async function importar(arquivo) {
    setEnviando(true)
    setErro("")
    setImportacao(null)
    try {
      const { importacao: resultado, ...dados } = await enviarGabarito(arquivo)
      setEstado(dados)
      setImportacao(resultado ?? null)
    } catch (falha) {
      setErro(falha.message)
    } finally {
      setEnviando(false)
    }
  }

  const opcoes = estado?.opcoes
  const modoEscolhido = modo || opcoes?.modo || "trechos"
  const rerankerEscolhido = reranker ?? opcoes?.reranker ?? ""

  async function rodar() {
    setErro("")
    try {
      setEstado(await rodarAvaliacao({ modo: modoEscolhido, reranker: rerankerEscolhido }))
    } catch (falha) {
      setErro(falha.message)
    }
  }

  const gabarito = estado?.gabarito
  const progresso = estado?.progresso
  const daCategoria = (rodada?.itens ?? []).filter((item) => !categoria || item.categoria === categoria)
  const itens = daCategoria.filter((item) => !filtro || item.status === filtro)
  const categorias = rodada?.resumo?.por_categoria ?? []

  return (
    <main className="conteudo">
      <Treinamento />
      <section className="resumo">
        <div>
          <h2>Prova com gabarito do COG</h2>
          <p>
            Medida independente do treino: faz as perguntas do gabarito pelo mesmo caminho da consulta e compara cada
            resposta com a esperada. Sem mudança no índice ou no prompt, a rodada sai do cache. Importar soma ao
            gabarito: o bloco novo só substitui as perguntas do mesmo documento.
          </p>
        </div>
        <div className="acoes">
          <label className={`importar ${enviando || rodando ? "desligado" : ""}`}>
            <input
              type="file"
              accept=".json,application/json"
              disabled={enviando || rodando}
              onChange={(evento) => {
                const arquivo = evento.target.files?.[0]
                if (arquivo) importar(arquivo)
                evento.target.value = ""
              }}
            />
            {enviando ? "Importando…" : "Importar bloco do gabarito"}
          </label>
          {opcoes ? (
            <span className="opcoes-rodada">
              <select
                aria-label="Contexto enviado ao GPT"
                value={modoEscolhido}
                disabled={rodando}
                onChange={(evento) => setModo(evento.target.value)}
              >
                {opcoes.modos.map((valor) => (
                  <option key={valor} value={valor}>
                    {MODOS[valor] ?? valor}
                  </option>
                ))}
              </select>
              <select
                aria-label="Reordenador local"
                value={rerankerEscolhido}
                disabled={rodando}
                onChange={(evento) => setReranker(evento.target.value)}
              >
                <option value="">Sem reordenador</option>
                <option value={opcoes.reranker || opcoes.reranker_sugerido}>Com reordenador local</option>
              </select>
            </span>
          ) : null}
          <button type="button" className="primario" onClick={rodar} disabled={rodando || !gabarito?.total}>
            {rodando ? "Rodando…" : "Rodar a prova"}
          </button>
        </div>
      </section>

      {erro ? <p className="aviso faixa">{erro}</p> : null}
      {importacao ? <p className="ok faixa">{textoImportacao(importacao)}</p> : null}
      {progresso?.erro ? <p className="aviso faixa">A última rodada parou: {progresso.erro}</p> : null}

      {gabarito ? <ResumoGabarito gabarito={gabarito} /> : <Carregando texto="Lendo a avaliação…" />}

      {rodando ? <ProgressoDaRodada progresso={progresso} /> : null}

      {gabarito?.total ? (
        <div className="filtros" role="tablist" aria-label="Parte da avaliação">
          <button type="button" className={aba === "rodadas" ? "ativo" : ""} onClick={() => setAba("rodadas")}>
            Rodadas
          </button>
          <button type="button" className={aba === "revisao" ? "ativo" : ""} onClick={() => setAba("revisao")}>
            Revisão do gabarito ({estado?.revisadas ?? 0} revisadas)
          </button>
        </div>
      ) : null}

      {aba === "revisao" ? <Revisao rodada={rodada} onMudou={atualizar} /> : null}

      {aba === "rodadas" && rodadas.length > 0 ? (
        <section className="cartao grafico">
          <h3>Acertividade por rodada</h3>
          <GraficoRodadas rodadas={rodadas} escolhida={escolhida} onEscolher={setEscolhida} />
        </section>
      ) : null}

      {aba === "rodadas" && escolhida && rodada?.id !== escolhida ? <Carregando texto="Abrindo a rodada…" /> : null}

      {aba === "rodadas" && rodada ? (
        <>
          <NumerosRodada rodada={rodada} />
          {categorias.length > 0 ? (
            <PorCategoria categorias={categorias} escolhida={categoria} onEscolher={setCategoria} />
          ) : null}
          <div className="filtros" role="group" aria-label="Filtrar por resultado">
            <button type="button" className={filtro === "" ? "ativo" : ""} onClick={() => setFiltro("")}>
              Todas ({daCategoria.length})
            </button>
            {Object.entries(ROTULOS).map(([status, rotulo]) => {
              const total = daCategoria.filter((item) => item.status === status).length
              if (total === 0) return null
              return (
                <button
                  key={status}
                  type="button"
                  className={filtro === status ? "ativo" : ""}
                  onClick={() => setFiltro(status)}
                >
                  {rotulo} ({total})
                </button>
              )
            })}
          </div>
          <div className="lista">
            {itens.map((item) => (
              <ItemAvaliado key={item.id} item={item} />
            ))}
          </div>
        </>
      ) : null}
    </main>
  )
}

function ResumoGabarito({ gabarito }) {
  if (!gabarito.total) {
    return (
      <p className="aviso faixa">
        Nenhum gabarito ainda. Importe o JSON gerado no NotebookLM, com pergunta, resposta esperada, seção e valores.
      </p>
    )
  }
  const grupos =
    gabarito.documentos.length === 1 && gabarito.categorias?.length
      ? gabarito.categorias.map((item) => `${item.categoria}: ${item.perguntas}`)
      : gabarito.documentos.map((item) => `${item.documento}: ${item.perguntas}`)
  return (
    <p className="ok faixa">
      {gabarito.total} perguntas no gabarito, {gabarito.recusar} que devem ser recusadas. {grupos.join(" · ")}
    </p>
  )
}

function textoImportacao({ importadas, substituidas, documentos, total }) {
  const origem = documentos.length === 1 ? `da ${documentos[0]}` : `de ${documentos.join(", ")}`
  const troca = substituidas ? ` (substituíram ${substituidas} do mesmo documento)` : ""
  const quantas = importadas === 1 ? "1 pergunta" : `${importadas} perguntas`
  return `${quantas} ${origem} ${importadas === 1 ? "importada" : "importadas"}${troca}. Gabarito com ${total} perguntas.`
}

function PorCategoria({ categorias, escolhida, onEscolher }) {
  return (
    <section className="cartao categorias">
      <h3>Acertividade por categoria</h3>
      <ul>
        {categorias.map((item) => (
          <li key={item.categoria}>
            <button
              type="button"
              className={item.categoria === escolhida ? "ativo" : ""}
              onClick={() => onEscolher(item.categoria === escolhida ? "" : item.categoria)}
            >
              <span className="nome">{item.categoria}</span>
              <span className="trilho" aria-hidden="true">
                <span style={{ width: `${item.acertividade}%` }} />
              </span>
              <span className="valor">
                {item.acertividade.toLocaleString("pt-BR")}% · {item.acertos} de {item.total}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ProgressoDaRodada({ progresso }) {
  const parte = progresso.total ? (100 * progresso.feitos) / progresso.total : 0
  return (
    <section className="cartao">
      <Progresso
        percentual={parte}
        texto="Avaliando o assistente"
        detalhe={`${progresso.feitos} de ${progresso.total || "…"} perguntas avaliadas.`}
      />
    </section>
  )
}

function NumerosRodada({ rodada }) {
  const resumo = rodada.resumo
  return (
    <>
      <section className="numeros">
        <article>
          <span>Acertividade</span>
          <strong>{resumo.acertividade.toLocaleString("pt-BR")}%</strong>
        </article>
        <article>
          <span>Acertos</span>
          <strong>
            {resumo.acertos} de {resumo.total - resumo.falhas}
          </strong>
        </article>
        <article>
          <span>Invenções</span>
          <strong>{resumo.invencoes}</strong>
        </article>
        <article>
          <span>Nas perguntas revisadas</span>
          <strong>{rodada.revisadas?.total ? `${rodada.revisadas.acertividade.toLocaleString("pt-BR")}%` : "—"}</strong>
        </article>
        <article>
          <span>Custo da rodada</span>
          <strong>{dolares(resumo.custo_usd)}</strong>
        </article>
      </section>
      <p className="mudo">
        Rodada de {formatarQuando(rodada.quando)}, {rotuloConfig(rodada).toLocaleLowerCase("pt-BR")}, versão{" "}
        {rodada.versao}.{" "}
        {rodada.revisadas?.total
          ? `${rodada.revisadas.acertos} acertos em ${rodada.revisadas.total} perguntas revisadas pelo COG. `
          : "Nenhuma pergunta desta rodada foi revisada ainda. "}
        {resumo.parciais} parciais, {resumo.erros}{" "}
        erros, {resumo.recusas_indevidas} recusas indevidas
        {resumo.falhas ? `, ${resumo.falhas} falhas técnicas` : ""}. {resumo.do_cache} respostas vieram do cache.
        {resumo.recall_busca != null
          ? ` Busca: a seção esperada chegou ao GPT em ${resumo.recall_busca.toLocaleString("pt-BR")}% das perguntas (posição média, MRR ${resumo.mrr_busca.toLocaleString("pt-BR")}).`
          : ""}
      </p>
      {rodada.comparacao ? (
        <p className={rodada.comparacao.regressoes.length ? "alerta-resposta" : "mudo"}>
          Contra a rodada {rodada.comparacao.anterior}: {rodada.comparacao.variacao_acertividade > 0 ? "+" : ""}
          {rodada.comparacao.variacao_acertividade.toLocaleString("pt-BR")} pontos, {rodada.comparacao.melhorias.length}{" "}
          melhoraram e {rodada.comparacao.regressoes.length} pioraram
          {rodada.comparacao.regressoes.length ? ` (${rodada.comparacao.regressoes.slice(0, 8).join(", ")})` : ""}.
        </p>
      ) : null}
    </>
  )
}

function GraficoRodadas({ rodadas, escolhida, onEscolher }) {
  const largura = 640
  const altura = 206
  const margem = 28
  const passo = (largura - margem) / rodadas.length
  const barra = Math.min(40, passo * 0.55)
  return (
    <svg className="desenho" viewBox={`0 0 ${largura} ${altura}`} role="img" aria-label="Acertividade por rodada">
      {rodadas.map((item, indice) => {
        const valor = item.resumo?.acertividade ?? 0
        const alto = Math.max(4, ((altura - 76) * valor) / 100)
        const x = margem + indice * passo + (passo - barra) / 2
        const y = altura - 44 - alto
        return (
          <g key={item.id} className="clicavel" onClick={() => onEscolher(item.id)}>
            <title>{`${formatarQuando(item.quando)}: ${valor}% · ${rotuloConfig(item)} · versão ${item.versao}`}</title>
            <rect
              x={x}
              y={y}
              width={barra}
              height={alto}
              rx="6"
              className={item.id === escolhida ? "barra escolhida" : "barra"}
            />
            <text x={x + barra / 2} y={y - 6} textAnchor="middle" className="eixo">
              {`${valor}%`}
            </text>
            <text x={x + barra / 2} y={altura - 26} textAnchor="middle" className="eixo">
              {formatarQuando(item.quando).slice(0, 5)}
            </text>
            <text x={x + barra / 2} y={altura - 10} textAnchor="middle" className="eixo">
              {siglaConfig(item)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function ItemAvaliado({ item }) {
  return (
    <article className={`cartao avaliado ${item.status}`}>
      <header className="cartao-topo">
        <div>
          <p className="codigo">
            {item.id}
            {item.categoria ? <span> · {item.categoria}</span> : null}
            {item.referencia ? (
              <span> · {item.referencia}</span>
            ) : (
              <>
                {item.documento ? <span> · {item.documento}</span> : null}
                {item.secao ? <span> · seção {item.secao}</span> : null}
              </>
            )}
          </p>
          <h3>{item.pergunta}</h3>
        </div>
        <span className={`selo-estado status-${item.status}`}>{ROTULOS[item.status] ?? item.status}</span>
      </header>
      <div className="comparacao">
        <div>
          <p className="rotulo">Esperada</p>
          {item.deve_recusar ? (
            <p className="mudo">Deve recusar: o ponto não está no normativo.</p>
          ) : (
            <Leitura texto={item.resposta_esperada} />
          )}
        </div>
        <div>
          <p className="rotulo">Obtida</p>
          <Leitura texto={item.resposta || "Sem resposta."} />
          {item.citacoes?.length ? (
            <p className="mudo">Citou: {item.citacoes.map((citacao) => citacao.secao).join("; ")}</p>
          ) : null}
        </div>
      </div>
      <ul className="conferencia">
        {item.motivo ? <li>{item.motivo}</li> : null}
        {item.secao_recuperada === false ? <li>A seção esperada não estava nos trechos recuperados.</li> : null}
        {item.secao_ok === false && item.coberto ? <li>A seção citada não é a esperada.</li> : null}
        {item.valores_ausentes?.length ? <li>Faltou: {item.valores_ausentes.join("; ")}.</li> : null}
      </ul>
    </article>
  )
}
