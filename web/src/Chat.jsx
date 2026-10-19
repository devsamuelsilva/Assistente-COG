import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { consultar, consultarOns, enviarRetorno } from "./api"
import { Progresso } from "./Carregando"
import { duracaoPrevista, lembrarDuracao, percentualEstimado } from "./duracao"
import Leitura from "./Leitura"
import Nuvem from "./Nuvem"
import { invalidarRegistro, usePerguntas } from "./registro"

const CANAIS = {
  operacional: {
    rotulo: "Operacional",
    titulo: "Consulta aos Documentos Operacionais Energimp",
    texto: "Responde só com o que está escrito no normativo vigente e cita a seção.",
    sugestoes: [
      "Qual o objetivo deste normativo?",
      "O que a Pós-Operação considera como ocorrência?",
      "Quais informações o relatório de análise deve conter?",
    ],
    comHistorico: "Perguntas já respondidas neste acervo. Escolha uma ou escreva a sua.",
    semHistorico: "Escreva a pergunta como no canal do COG.",
    placeholder: "Escreva a pergunta",
    carregando: "Consultando o normativo…",
  },
  ons: {
    rotulo: "ONS",
    titulo: "Dados abertos do ONS",
    texto:
      "Consulta os dados públicos do ONS pelo servidor oficial TIAGO: carga, geração, constrained-off, CMO e reservatórios. Responde com o número, a unidade e a fonte.",
    sugestoes: [
      "Qual foi a carga média do Nordeste em agosto de 2026?",
      "Qual foi a geração eólica verificada no Nordeste ontem?",
      "Quanto de constrained-off as usinas eólicas tiveram em agosto de 2026?",
      "Qual foi o CMO médio do Nordeste na última semana?",
    ],
    comHistorico: "Perguntas já feitas ao ONS. Escolha uma ou escreva a sua.",
    semHistorico: "Pergunte sobre carga, geração, constrained-off, CMO ou reservatórios. Informe período e subsistema.",
    placeholder: "Pergunte aos dados do ONS",
    carregando: "Consultando os dados do ONS. Pode levar até um minuto…",
  },
}

export default function Chat({ ativo, indice, carregando, onIrAoAcervo }) {
  const [canal, setCanal] = useState("operacional")
  const atual = CANAIS[canal]

  return (
    <main className="conteudo chat" hidden={!ativo}>
      <section className="resumo">
        <div>
          <h2>{atual.titulo}</h2>
          <p>{atual.texto}</p>
        </div>
        <div className="filtros canais" role="tablist" aria-label="Canal da consulta">
          {Object.entries(CANAIS).map(([id, dados]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={canal === id}
              className={canal === id ? "ativo" : ""}
              onClick={() => setCanal(id)}
            >
              {dados.rotulo}
            </button>
          ))}
        </div>
      </section>

      <Canal
        tipo="operacional"
        ativo={ativo && canal === "operacional"}
        indice={indice}
        semIndice={!carregando && !indice?.existe}
        onIrAoAcervo={onIrAoAcervo}
      />
      <Canal tipo="ons" ativo={ativo && canal === "ons"} />
    </main>
  )
}

function Canal({ tipo, ativo, indice, semIndice = false, onIrAoAcervo }) {
  const dados = CANAIS[tipo]
  const chave = `cog-chat-${tipo}`
  const [mensagens, setMensagens] = useState(() => lerConversa(chave))
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState("")
  const lista = useRef(null)
  const seguindo = useRef(true)
  const [afastado, setAfastado] = useState(false)
  const anteriores = usePerguntas(tipo).dados?.perguntas ?? []
  const nuvem = anteriores.length > 0 ? anteriores : dados.sugestoes

  useEffect(() => {
    try {
      if (mensagens.length > 0) sessionStorage.setItem(chave, JSON.stringify(mensagens))
      else sessionStorage.removeItem(chave)
    } catch {
      // sessionStorage cheio ou bloqueado: a conversa segue só na memória.
    }
  }, [chave, mensagens])

  async function perguntar(pergunta) {
    const limpa = pergunta.trim()
    if (!limpa || enviando) return false
    const historico = turnosConcluidos(mensagens)
    const bolha = { papel: "usuario", texto: limpa }
    seguindo.current = true
    setAfastado(false)
    setErro("")
    setMensagens((atual) => [...atual, bolha])
    setEnviando(true)
    const inicio = performance.now()
    try {
      const resultado = await (tipo === "ons" ? consultarOns : consultar)(limpa, historico)
      lembrarDuracao(tipo, performance.now() - inicio)
      setMensagens((atual) => [...atual, { papel: "assistente", ...resultado }])
      invalidarRegistro()
      return true
    } catch (falha) {
      setMensagens((atual) => atual.filter((mensagem) => mensagem !== bolha))
      setErro(falha.message)
      return false
    } finally {
      setEnviando(false)
    }
  }

  function novaConversa() {
    seguindo.current = true
    setAfastado(false)
    setMensagens([])
    setErro("")
  }

  const votar = useCallback((consulta, util) => {
    setMensagens((atual) => atual.map((mensagem) => (mensagem.consulta === consulta ? { ...mensagem, voto: util } : mensagem)))
    invalidarRegistro()
  }, [])

  const fio = useMemo(
    () =>
      mensagens.map((mensagem, indiceMensagem) =>
        mensagem.papel === "usuario" ? (
          <article key={indiceMensagem} className="bolha usuario">
            <p>{mensagem.texto}</p>
          </article>
        ) : tipo === "ons" ? (
          <BolhaOns key={indiceMensagem} mensagem={mensagem} onVotar={votar} />
        ) : (
          <Bolha key={indiceMensagem} mensagem={mensagem} onVotar={votar} />
        ),
      ),
    [mensagens, tipo, votar],
  )

  useLayoutEffect(() => {
    const caixa = lista.current
    if (!caixa || !ativo || mensagens.length === 0 || !seguindo.current) return
    const mostrar = () => {
      const ultima = caixa.lastElementChild
      if (!ultima) return
      const caixaRetangulo = caixa.getBoundingClientRect()
      const ultimaRetangulo = ultima.getBoundingClientRect()
      if (ultimaRetangulo.height <= caixa.clientHeight) {
        caixa.scrollTop = caixa.scrollHeight
        return
      }
      caixa.scrollTop += ultimaRetangulo.top - caixaRetangulo.top
    }
    mostrar()
    const id = requestAnimationFrame(mostrar)
    return () => cancelAnimationFrame(id)
  }, [mensagens, enviando, ativo])

  // Acompanha a última resposta enquanto ela estiver à vista. Rolar para cima para reler solta a rolagem;
  // perguntar de novo, ou o botão, volta a acompanhar.
  function aoRolar() {
    const caixa = lista.current
    const ultima = caixa?.lastElementChild
    if (!caixa || !ultima || mensagens.length === 0) return
    const fim = caixa.getBoundingClientRect().bottom
    const acompanhando = ultima.getBoundingClientRect().top < fim - 24
    seguindo.current = acompanhando
    setAfastado(!acompanhando)
  }

  function irParaAUltima() {
    seguindo.current = true
    setAfastado(false)
    const caixa = lista.current
    if (caixa) caixa.scrollTop = caixa.scrollHeight
  }

  return (
    <div className="canal" hidden={!ativo}>
      <div className="conversa">
        {semIndice ? (
          <p className="aviso faixa">
            Sem índice, a consulta não responde.{" "}
            {onIrAoAcervo ? (
              <>
                <button type="button" className="link" onClick={onIrAoAcervo}>
                  Abra o Acervo e indexe
                </button>
                .
              </>
            ) : (
              "Avise o responsável pelo assistente."
            )}
          </p>
        ) : null}
        {indice?.desatualizado && indice.existe && onIrAoAcervo ? (
          <p className="aviso faixa">O acervo mudou. Indexe de novo para a consulta usar a revisão atual.</p>
        ) : null}

        <div className="fio" ref={lista} onScroll={aoRolar}>
          {mensagens.length === 0 ? (
            <div className="vazio">
              <p className="mudo">{anteriores.length > 0 ? dados.comHistorico : dados.semHistorico}</p>
              {ativo ? (
                <Nuvem perguntas={nuvem} onEscolher={perguntar} desabilitado={semIndice || enviando} />
              ) : null}
            </div>
          ) : (
            fio
          )}
          {enviando ? <ProgressoDaResposta texto={dados.carregando} tipo={tipo} /> : null}
        </div>

        {afastado && mensagens.length > 0 ? (
          <button type="button" className="ir-ao-fim" onClick={irParaAUltima}>
            Ir para a última resposta ↓
          </button>
        ) : null}

        {erro ? <p className="aviso faixa">{erro}</p> : null}
      </div>

      <Compositor
        placeholder={dados.placeholder}
        bloqueado={semIndice || enviando}
        onEnviar={perguntar}
        onNovaConversa={mensagens.length > 0 && !enviando ? novaConversa : null}
      />
    </div>
  )
}

function ProgressoDaResposta({ texto, tipo }) {
  const [decorrido, setDecorrido] = useState(0)
  const inicio = useRef(performance.now())
  const previsto = useRef(duracaoPrevista(tipo))
  useEffect(() => {
    const id = setInterval(() => setDecorrido(performance.now() - inicio.current), 150)
    return () => clearInterval(id)
  }, [])
  return (
    <Progresso
      percentual={percentualEstimado(decorrido, previsto.current)}
      texto={texto}
      estimado
      detalhe={`Estimativa pelo tempo das últimas respostas (cerca de ${Math.round(previsto.current / 1000)} s).`}
    />
  )
}

function Compositor({ placeholder, bloqueado, onEnviar, onNovaConversa }) {
  const [texto, setTexto] = useState("")

  async function enviar() {
    const atual = texto
    if (!atual.trim() || bloqueado) return
    setTexto("")
    if (!(await onEnviar(atual))) setTexto((escrito) => escrito || atual)
  }

  return (
    <form
      className="compositor"
      onSubmit={(evento) => {
        evento.preventDefault()
        enviar()
      }}
    >
      <textarea
        value={texto}
        rows={1}
        placeholder={placeholder}
        onChange={(evento) => setTexto(evento.target.value)}
        onKeyDown={(evento) => {
          if (evento.key === "Enter" && !evento.shiftKey) {
            evento.preventDefault()
            enviar()
          }
        }}
        disabled={bloqueado}
      />
      {onNovaConversa ? (
        <button type="button" className="link nova-conversa" onClick={onNovaConversa}>
          Nova conversa
        </button>
      ) : null}
      <button type="submit" className="primario" disabled={bloqueado || !texto.trim()}>
        Perguntar
      </button>
    </form>
  )
}

function lerConversa(chave) {
  try {
    const salvas = JSON.parse(sessionStorage.getItem(chave) || "[]")
    return Array.isArray(salvas) ? salvas : []
  } catch {
    return []
  }
}

function turnosConcluidos(mensagens) {
  const turnos = []
  for (let indice = 0; indice < mensagens.length - 1; indice += 1) {
    const atual = mensagens[indice]
    const seguinte = mensagens[indice + 1]
    if (atual.papel === "usuario" && seguinte.papel === "assistente") {
      turnos.push({ pergunta: atual.texto, resposta: seguinte.resposta || "" })
      indice += 1
    }
  }
  return turnos.slice(-4)
}

const CONFIANCA = {
  alta: ["ok", "Confiança alta"],
  media: ["mudo-selo", "Confiança média: confira a fonte"],
  baixa: ["aviso", "Confiança baixa: confira no normativo"],
}

function Bolha({ mensagem, onVotar }) {
  const confianca = CONFIANCA[mensagem.confianca?.nivel]
  return (
    <article className={`bolha assistente ${mensagem.coberto ? "" : "descoberta"}`}>
      {confianca ? (
        <span
          className={`selo-estado ${confianca[0]}`}
          title={`Chance estimada de a resposta estar certa: ${Math.round(100 * mensagem.confianca.probabilidade)}%`}
        >
          {confianca[1]}
        </span>
      ) : null}
      {mensagem.status === "parcial" ? (
        <span className="selo-estado aviso" title="Os trechos trazem só parte do que foi perguntado">
          Resposta parcial
        </span>
      ) : null}
      <Leitura texto={mensagem.resposta} />
      {mensagem.alertas?.map((alerta) => (
        <p key={alerta} className="alerta-resposta">
          Atenção: {alerta}
        </p>
      ))}
      {mensagem.citacoes?.length > 0 ? (
        <ul className="citacoes">
          {mensagem.citacoes.map((citacao) => (
            <li key={`${citacao.arquivo}-${citacao.secao}`}>
              {`${citacao.codigo || citacao.arquivo}${citacao.revisao ? `, revisão ${citacao.revisao}` : ""} · ${citacao.secao}`}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mudo">Siga o fluxo humano do COG.</p>
      )}
      {mensagem.status === "parcial" ? <p className="mudo">Para o que falta, siga o fluxo humano do COG.</p> : null}
      <p className="rodape-resposta">
        {mensagem.cache ? "Resposta já consultada neste acervo. " : ""}
        {mensagem.aviso}
      </p>
      {mensagem.consulta ? <Retorno consulta={mensagem.consulta} voto={mensagem.voto} onVotar={onVotar} /> : null}
    </article>
  )
}

function BolhaOns({ mensagem, onVotar }) {
  const fontes = mensagem.fontes || []
  const consultas = mensagem.consultas || []
  const unidades = unidadesUnicas(consultas)
  return (
    <article className={`bolha assistente ${mensagem.coberto ? "" : "descoberta"}`}>
      <Leitura texto={mensagem.resposta} />
      {fontes.length > 0 ? (
        <ul className="citacoes">
          {fontes.map((fonte) => (
            <li key={fonte.dataset}>
              Fonte:{" "}
              {fonte.portal_url ? (
                <a href={fonte.portal_url} target="_blank" rel="noreferrer">
                  {fonte.dataset}
                </a>
              ) : (
                fonte.dataset
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mudo">Sem dado do ONS nesta resposta.</p>
      )}
      {unidades.length > 0 ? <p className="unidades">Unidade: {unidades.join(" · ")}</p> : null}
      {consultas.length > 0 ? (
        <details className="sql">
          <summary>{consultas.length === 1 ? "SQL executado" : `SQL executado (${consultas.length} consultas)`}</summary>
          {consultas.map((consulta, posicao) => (
            <pre key={posicao}>{consulta.sql}</pre>
          ))}
        </details>
      ) : null}
      <p className="rodape-resposta">
        {mensagem.cache ? "Resposta já consultada hoje. " : ""}
        {mensagem.aviso}
      </p>
      {mensagem.consulta ? <Retorno consulta={mensagem.consulta} voto={mensagem.voto} onVotar={onVotar} /> : null}
    </article>
  )
}

function unidadesUnicas(consultas) {
  const vistas = new Set()
  for (const consulta of consultas) {
    for (const item of consulta.unidades || []) {
      vistas.add(`${item.coluna} em ${item.unidade}`)
    }
  }
  return [...vistas]
}

function Retorno({ consulta, voto: registrado, onVotar }) {
  const [voto, setVoto] = useState(null)
  const [comentario, setComentario] = useState("")
  const [erro, setErro] = useState("")

  async function enviar(util, texto = "") {
    setErro("")
    try {
      await enviarRetorno({ consulta, util, comentario: texto })
      onVotar(consulta, util)
    } catch (falha) {
      setErro(falha.message)
    }
  }

  if (registrado != null) {
    return <p className="retorno mudo">{registrado ? "Marcada como útil." : "Registrado. O retorno aparece no Histórico."}</p>
  }
  if (voto === false) {
    return (
      <form
        className="retorno"
        onSubmit={(evento) => {
          evento.preventDefault()
          enviar(false, comentario)
        }}
      >
        <input
          value={comentario}
          maxLength={1000}
          placeholder="O que faltou ou está errado? (opcional)"
          onChange={(evento) => setComentario(evento.target.value)}
          autoFocus
        />
        <button type="submit">Enviar</button>
        <button type="button" className="link" onClick={() => setVoto(null)}>
          Cancelar
        </button>
        {erro ? <span className="aviso-texto">{erro}</span> : null}
      </form>
    )
  }
  return (
    <div className="retorno">
      <span className="mudo">A resposta ajudou?</span>
      <button
        type="button"
        onClick={() => {
          setVoto(true)
          enviar(true)
        }}
      >
        Útil
      </button>
      <button type="button" onClick={() => setVoto(false)}>
        Não útil
      </button>
      {erro ? <span className="aviso-texto">{erro}</span> : null}
    </div>
  )
}
