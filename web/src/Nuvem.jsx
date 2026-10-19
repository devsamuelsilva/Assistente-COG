import { useLayoutEffect, useRef, useState } from "react"

const MOVIMENTO = [
  { dx: "5px", dy: "-8px", duracao: "9s", atraso: "0s" },
  { dx: "-6px", dy: "5px", duracao: "11s", atraso: "-1.4s" },
  { dx: "4px", dy: "7px", duracao: "8.5s", atraso: "-2.2s" },
  { dx: "-5px", dy: "-6px", duracao: "10s", atraso: "-0.6s" },
  { dx: "6px", dy: "4px", duracao: "12s", atraso: "-3s" },
  { dx: "-4px", dy: "-7px", duracao: "9.5s", atraso: "-1.8s" },
  { dx: "5px", dy: "6px", duracao: "10.5s", atraso: "-2.8s" },
  { dx: "-6px", dy: "-4px", duracao: "8s", atraso: "-0.2s" },
]

export default function Nuvem({ perguntas, onEscolher, desabilitado }) {
  const caixa = useRef(null)
  const [limite, setLimite] = useState(null)
  const [medida, setMedida] = useState(0)
  const conteudo = perguntas.join("\n")

  useLayoutEffect(() => {
    setLimite(null)
  }, [conteudo, medida])

  useLayoutEffect(() => {
    if (limite !== null || !caixa.current) return
    const elemento = caixa.current
    const fim = elemento.clientHeight - parseFloat(getComputedStyle(elemento).paddingBottom)
    let cabem = 0
    for (const floco of elemento.children) {
      if (floco.offsetTop + floco.offsetHeight > fim) break
      cabem += 1
    }
    setLimite(cabem)
  }, [limite])

  useLayoutEffect(() => {
    const elemento = caixa.current
    if (!elemento) return undefined
    let largura = elemento.clientWidth
    let altura = elemento.clientHeight
    const observador = new ResizeObserver(() => {
      if (elemento.clientWidth === largura && elemento.clientHeight === altura) return
      largura = elemento.clientWidth
      altura = elemento.clientHeight
      setMedida((valor) => valor + 1)
    })
    observador.observe(elemento)
    return () => observador.disconnect()
  }, [])

  const visiveis = limite === null ? perguntas : perguntas.slice(0, limite)

  return (
    <div className="nuvem" ref={caixa} aria-label="Perguntas anteriores">
      {visiveis.map((pergunta, indice) => {
        const movimento = MOVIMENTO[indice % MOVIMENTO.length]
        return (
          <button
            key={pergunta}
            type="button"
            className="floco"
            title={pergunta}
            disabled={desabilitado}
            style={{
              "--dx": movimento.dx,
              "--dy": movimento.dy,
              "--duracao": movimento.duracao,
              "--atraso": movimento.atraso,
            }}
            onClick={() => onEscolher(pergunta)}
          >
            {pergunta}
          </button>
        )
      })}
    </div>
  )
}
