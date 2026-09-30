import { useLayoutEffect, useRef, useState } from "react"
import { consultar } from "./api"
import Leitura from "./Leitura"
import Nuvem from "./Nuvem"
import { useRegistro } from "./registro"

const SUGESTOES = [
  "Qual o objetivo deste normativo?",
  "O que a Pós-Operação considera como ocorrência?",
  "Quais informações o relatório de análise deve conter?",
]

export default function Chat({ indice, carregando, onIrAoAcervo }) {
  const [mensagens, setMensagens] = useState([])
  const [texto, setTexto] = useState("")
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState("")
  const lista = useRef(null)
  const registro = useRegistro()
  const semIndice = !carregando && !indice?.existe
  const anteriores = perguntasAnteriores(registro.uso?.consultas)
  const nuvem = anteriores.length > 0 ? anteriores : SUGESTOES

  async function perguntar(pergunta) {
    const limpa = pergunta.trim()
    if (!limpa || enviando) return
    const historico = turnosConcluidos(mensagens)
    setTexto("")
    setErro("")
    setMensagens((atual) => [...atual, { papel: "usuario", texto: limpa }])
    setEnviando(true)
    try {
      const resultado = await consultar(limpa, historico)
      setMensagens((atual) => [...atual, { papel: "assistente", ...resultado }])
    } catch (falha) {
      setErro(falha.message)
    } finally {
      setEnviando(false)
    }
  }

  useLayoutEffect(() => {
    const caixa = lista.current
    if (!caixa || mensagens.length === 0) return
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
  }, [mensagens, enviando])

  function aoTeclar(evento) {
    if (evento.key === "Enter" && !evento.shiftKey) {
      evento.preventDefault()
      perguntar(texto)
    }
  }

  return (
    <main className="conteudo chat">
      <section className="resumo">
        <div>
          <h2>Consulta</h2>
          <p>A resposta usa só os trechos indexados. Sem citação, o ponto não está no normativo.</p>
        </div>
      </section>

      <div className="conversa">
        {semIndice ? (
          <p className="aviso faixa">
            Ainda não há índice.{" "}
            <button type="button" className="link" onClick={onIrAoAcervo}>
              Abra o acervo e indexe
            </button>
            .
          </p>
        ) : null}
        {indice?.desatualizado && indice.existe ? (
          <p className="aviso faixa">O acervo mudou. Indexe de novo para a consulta usar a versão atual.</p>
        ) : null}

        <div className="fio" ref={lista}>
          {mensagens.length === 0 ? (
            <div className="vazio">
              <p className="mudo">
                {anteriores.length > 0
                  ? "Perguntas já feitas neste acervo. Escolha uma ou escreva a sua."
                  : "Pergunte como se estivesse no canal do COG."}
              </p>
              <Nuvem perguntas={nuvem} onEscolher={perguntar} desabilitado={semIndice || enviando} />
            </div>
          ) : (
            mensagens.map((mensagem, indiceMensagem) => <Bolha key={indiceMensagem} mensagem={mensagem} />)
          )}
          {enviando ? <p className="mudo digitando">Consultando o normativo…</p> : null}
        </div>

        {erro ? <p className="aviso faixa">{erro}</p> : null}
      </div>

      <form
        className="compositor"
        onSubmit={(evento) => {
          evento.preventDefault()
          perguntar(texto)
        }}
      >
        <textarea
          value={texto}
          rows={1}
          placeholder="Escreva a pergunta"
          onChange={(evento) => setTexto(evento.target.value)}
          onKeyDown={aoTeclar}
          disabled={semIndice || enviando}
        />
        <button type="submit" className="primario" disabled={semIndice || enviando || !texto.trim()}>
          Perguntar
        </button>
      </form>
    </main>
  )
}

function perguntasAnteriores(consultas, limite = 16) {
  const vistas = new Set()
  const lista = []
  for (const consulta of consultas || []) {
    if (!consulta.coberto || consulta.origem === "avaliacao") continue
    const pergunta = String(consulta.pergunta || "").trim()
    const chave = pergunta.toLocaleLowerCase("pt-BR")
    if (pergunta.length < 8 || vistas.has(chave)) continue
    vistas.add(chave)
    lista.push(pergunta)
    if (lista.length >= limite) break
  }
  return lista
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

function Bolha({ mensagem }) {
  if (mensagem.papel === "usuario") {
    return (
      <article className="bolha usuario">
        <p>{mensagem.texto}</p>
      </article>
    )
  }
  return (
    <article className={`bolha assistente ${mensagem.coberto ? "" : "descoberta"}`}>
      <Leitura texto={mensagem.resposta} />
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
      <p className="rodape-resposta">
        {mensagem.cache ? "Resposta já consultada neste acervo. " : ""}
        {mensagem.aviso}
      </p>
    </article>
  )
}
