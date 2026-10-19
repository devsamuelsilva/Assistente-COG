import { useCarga } from "./carga"

export function BarraGlobal() {
  const { ativo, rotulo } = useCarga()
  return (
    <div className={`carga-global ${ativo ? "ativa" : ""}`} role="status" aria-live="polite">
      <span className="carga-trilho" aria-hidden="true">
        <span />
      </span>
      {ativo ? <span className="carga-rotulo">{rotulo}</span> : null}
    </div>
  )
}

/** Barra de 0 a 100%. `estimado` avisa que o número vem do tempo das últimas vezes, não de uma contagem real. */
export function Progresso({ percentual, texto, detalhe = "", estimado = false }) {
  const valor = Math.max(0, Math.min(100, Math.round(percentual || 0)))
  return (
    <div className="progresso" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={valor} aria-label={texto}>
      <div className="progresso-topo">
        <span>{texto}</span>
        <strong>
          {estimado ? "≈ " : ""}
          {valor}%
        </strong>
      </div>
      <span className="progresso-trilho" aria-hidden="true">
        <span style={{ width: `${valor}%` }} />
      </span>
      {detalhe ? <p className="mudo progresso-detalhe">{detalhe}</p> : null}
    </div>
  )
}

export default function Carregando({ texto }) {
  return (
    <div className="carregando" role="status">
      <span className="mudo">{texto}</span>
      <span className="carga-trilho" aria-hidden="true">
        <span />
      </span>
    </div>
  )
}
