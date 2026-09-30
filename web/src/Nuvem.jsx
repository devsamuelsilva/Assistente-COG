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
  return (
    <div className="nuvem" aria-label="Perguntas anteriores">
      {perguntas.map((pergunta, indice) => {
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
