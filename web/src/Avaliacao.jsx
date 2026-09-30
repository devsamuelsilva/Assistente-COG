import { useCallback, useEffect, useState } from "react"
import { carregarAvaliacao, carregarRodada, enviarGabarito, rodarAvaliacao } from "./api"
import { dolares, formatarQuando } from "./formato"
import Leitura from "./Leitura"

const ROTULOS = {
  acerto: "Acerto",
  parcial: "Parcial",
  erro: "Erro",
  recusa_indevida: "Recusa indevida",
  invencao: "Invenção",
  falha: "Falha técnica",
}

export default function Avaliacao() {
  const [estado, setEstado] = useState(null)
  const [erro, setErro] = useState("")
  const [enviando, setEnviando] = useState(false)
  const [escolhida, setEscolhida] = useState("")
  const [rodada, setRodada] = useState(null)
  const [filtro, setFiltro] = useState("")
  const [categoria, setCategoria] = useState("")

  const atualizar = useCallback(async () => {
    try {
      setEstado(await carregarAvaliacao())
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
    const id = setInterval(atualizar, 2000)
    return () => clearInterval(id)
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
    try {
      setEstado(await enviarGabarito(arquivo))
    } catch (falha) {
      setErro(falha.message)
    } finally {
      setEnviando(false)
    }
  }

  async function rodar() {
    setErro("")
    try {
      setEstado(await rodarAvaliacao())
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
      <section className="resumo">
        <div>
          <h2>Avaliação</h2>
          <p>
            Roda o gabarito pelo mesmo caminho do chat e compara cada resposta com a esperada. Mede o sistema inteiro:
            índice, quadros, busca e prompt. Uma rodada sem mudança no índice ou no prompt sai quase toda do cache.
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
            {enviando ? "Importando…" : "Importar gabarito"}
          </label>
          <button type="button" className="primario" onClick={rodar} disabled={rodando || !gabarito?.total}>
            {rodando ? "Rodando…" : "Rodar avaliação"}
          </button>
        </div>
      </section>

      {erro ? <p className="aviso faixa">{erro}</p> : null}
      {progresso?.erro ? <p className="aviso faixa">A última rodada parou: {progresso.erro}</p> : null}

      {gabarito ? <ResumoGabarito gabarito={gabarito} /> : <p className="mudo">Lendo a avaliação…</p>}

      {rodando ? <Progresso progresso={progresso} /> : null}

      {rodadas.length > 0 ? (
        <section className="cartao grafico">
          <h3>Acertividade por rodada</h3>
          <GraficoRodadas rodadas={rodadas} escolhida={escolhida} onEscolher={setEscolhida} />
        </section>
      ) : null}

      {rodada ? (
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
  const grupos = gabarito.categorias?.length
    ? gabarito.categorias.map((item) => `${item.categoria}: ${item.perguntas}`)
    : gabarito.documentos.map((item) => `${item.documento}: ${item.perguntas}`)
  return (
    <p className="ok faixa">
      {gabarito.total} perguntas no gabarito, {gabarito.recusar} que devem ser recusadas. {grupos.join(" · ")}
    </p>
  )
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

function Progresso({ progresso }) {
  const total = progresso.total || 1
  const parte = Math.round((100 * progresso.feitos) / total)
  return (
    <section className="cartao">
      <p className="mudo">
        {progresso.feitos} de {progresso.total || "…"} perguntas avaliadas.
      </p>
      <span className="trilho" aria-hidden="true">
        <span style={{ width: `${parte}%` }} />
      </span>
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
          <span>Custo da rodada</span>
          <strong>{dolares(resumo.custo_usd)}</strong>
        </article>
      </section>
      <p className="mudo">
        Rodada de {formatarQuando(rodada.quando)}, versão {rodada.versao}. {resumo.parciais} parciais, {resumo.erros}{" "}
        erros, {resumo.recusas_indevidas} recusas indevidas
        {resumo.falhas ? `, ${resumo.falhas} falhas técnicas` : ""}. {resumo.do_cache} respostas vieram do cache.
      </p>
    </>
  )
}

function GraficoRodadas({ rodadas, escolhida, onEscolher }) {
  const largura = 640
  const altura = 190
  const margem = 28
  const passo = (largura - margem) / rodadas.length
  const barra = Math.min(40, passo * 0.55)
  return (
    <svg className="desenho" viewBox={`0 0 ${largura} ${altura}`} role="img" aria-label="Acertividade por rodada">
      {rodadas.map((item, indice) => {
        const valor = item.resumo?.acertividade ?? 0
        const alto = Math.max(4, ((altura - 60) * valor) / 100)
        const x = margem + indice * passo + (passo - barra) / 2
        const y = altura - 28 - alto
        return (
          <g key={item.id} className="clicavel" onClick={() => onEscolher(item.id)}>
            <title>{`${formatarQuando(item.quando)}: ${valor}% · versão ${item.versao}`}</title>
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
            <text x={x + barra / 2} y={altura - 10} textAnchor="middle" className="eixo">
              {formatarQuando(item.quando).slice(0, 5)}
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
