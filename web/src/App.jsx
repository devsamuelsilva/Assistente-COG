import { useCallback, useEffect, useState } from "react"
import { carregarAcervo } from "./api"
import Acervo from "./Acervo"
import Avaliacao from "./Avaliacao"
import { BarraGlobal } from "./Carregando"
import Chat from "./Chat"
import Historico from "./Historico"
import { ROTULOS_TEMA, useTema } from "./tema"

const TELAS = [
  ["acervo", "Acervo"],
  ["chat", "Chat"],
  ["historico", "Histórico e custo"],
  ["avaliacao", "Treinamento"],
]

export default function App() {
  const [tela, setTela] = useState("acervo")
  const [estado, setEstado] = useState(null)
  const [erro, setErro] = useState("")
  const tema = useTema()

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
      <BarraGlobal />
      <header className="topo">
        <div className="marca">
          <Aerogerador />
          <div>
            <p className="selo">COG · Centro de Operação da Geração</p>
            <h1>Assistente da Operação</h1>
          </div>
        </div>
        <div className="navegacao">
          <nav className="abas" aria-label="Ambientes">
            {TELAS.map(([id, rotulo]) => (
              <button key={id} type="button" className={tela === id ? "ativo" : ""} onClick={() => setTela(id)}>
                {rotulo}
                {id === "chat" && indice?.existe ? <span className="contagem">{indice.vigentes}</span> : null}
              </button>
            ))}
          </nav>
          <button
            type="button"
            className="tema"
            onClick={tema.alternar}
            title={`${ROTULOS_TEMA[tema.escolha]}. Clique para trocar.`}
            aria-label={`${ROTULOS_TEMA[tema.escolha]}. Clique para trocar.`}
          >
            <IconeTema escolha={tema.escolha} />
          </button>
        </div>
      </header>

      {erro ? <p className="aviso faixa">{erro}</p> : null}

      {tela === "acervo" ? (
        <Acervo estado={estado} onEstado={setEstado} onErro={setErro} />
      ) : null}
      <Chat
        ativo={tela === "chat"}
        indice={indice}
        carregando={estado === null}
        onIrAoAcervo={() => setTela("acervo")}
      />
      {tela === "historico" ? <Historico /> : null}
      {tela === "avaliacao" ? <Avaliacao /> : null}
    </div>
  )
}

function Aerogerador() {
  const pa = "M24 17c-2.4-4-2.2-10 0-14 2.2 4 2.4 10 0 14z"
  return (
    <svg className="aerogerador" viewBox="0 0 48 48" aria-hidden="true">
      <path className="torre" d="M22.6 18.5h2.8l1.6 26h-6z" />
      <g className="rotor">
        <path d={pa} />
        <path d={pa} transform="rotate(120 24 17)" />
        <path d={pa} transform="rotate(240 24 17)" />
      </g>
      <circle className="cubo" cx="24" cy="17" r="2.2" />
    </svg>
  )
}

function IconeTema({ escolha }) {
  if (escolha === "claro") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    )
  }
  if (escolha === "escuro") {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  )
}
