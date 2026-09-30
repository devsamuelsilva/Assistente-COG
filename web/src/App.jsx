import { useCallback, useEffect, useState } from "react"
import { carregarAcervo } from "./api"
import Acervo from "./Acervo"
import Avaliacao from "./Avaliacao"
import Chat from "./Chat"
import Historico from "./Historico"
import Uso from "./Uso"

export default function App() {
  const [tela, setTela] = useState("acervo")
  const [estado, setEstado] = useState(null)
  const [erro, setErro] = useState("")

  const atualizar = useCallback(async () => {
    try {
      setEstado(await carregarAcervo())
      setErro("")
    } catch (falha) {
      setErro(falha.message)
    }
  }, [])

  useEffect(() => {
    atualizar()
  }, [atualizar])

  const indice = estado?.indice

  return (
    <div className={tela === "chat" ? "pagina consulta" : "pagina"}>
      <header className="topo">
        <div>
          <p className="selo">Centro de Operação da Geração</p>
          <h1>Assistente do normativo</h1>
        </div>
        <nav className="abas" aria-label="Ambientes">
          <button type="button" className={tela === "acervo" ? "ativo" : ""} onClick={() => setTela("acervo")}>
            Acervo
          </button>
          <button type="button" className={tela === "chat" ? "ativo" : ""} onClick={() => setTela("chat")}>
            Consulta
            {indice?.existe ? <span className="contagem">{indice.vigentes}</span> : null}
          </button>
          <button type="button" className={tela === "historico" ? "ativo" : ""} onClick={() => setTela("historico")}>
            Histórico
          </button>
          <button type="button" className={tela === "uso" ? "ativo" : ""} onClick={() => setTela("uso")}>
            Uso
          </button>
          <button type="button" className={tela === "avaliacao" ? "ativo" : ""} onClick={() => setTela("avaliacao")}>
            Avaliação
          </button>
        </nav>
      </header>

      {erro ? <p className="aviso faixa">{erro}</p> : null}

      {tela === "acervo" ? (
        <Acervo estado={estado} onEstado={setEstado} onErro={setErro} />
      ) : null}
      {tela === "chat" ? (
        <Chat indice={indice} carregando={estado === null} onIrAoAcervo={() => setTela("acervo")} />
      ) : null}
      {tela === "historico" ? <Historico /> : null}
      {tela === "uso" ? <Uso /> : null}
      {tela === "avaliacao" ? <Avaliacao /> : null}
    </div>
  )
}
