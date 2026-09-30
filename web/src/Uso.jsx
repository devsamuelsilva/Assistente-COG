import { custoPorDocumento, diaCurto, dolares, numero, serieDiaria } from "./formato"
import { useRegistro } from "./registro"

export default function Uso() {
  const { uso, erro, carregando, atualizar } = useRegistro()
  const resumo = uso?.resumo
  const dias = serieDiaria(uso?.consultas ?? [])
  const porDocumento = custoPorDocumento(uso?.consultas ?? [])

  return (
    <main className="conteudo">
      <section className="resumo">
        <div>
          <h2>Uso da IA</h2>
          <p>
            Estimativa pela tabela padrão do {resumo?.tabela.modelo ?? "modelo"}: US${" "}
            {resumo ? numero(resumo.tabela.entrada_usd_por_milhao) : "0,40"} por milhão de tokens de entrada e US${" "}
            {resumo ? numero(resumo.tabela.saida_usd_por_milhao) : "1,60"} na saída. A indexação local não entra. Pergunta
            repetida em cache custa zero.
          </p>
        </div>
        <button type="button" className="primario" onClick={atualizar} disabled={carregando}>
          {carregando ? "Atualizando…" : "Atualizar"}
        </button>
      </section>

      {erro ? <p className="aviso faixa">{erro}</p> : null}

      {resumo ? (
        <section className="numeros">
          <article>
            <span>Consultas</span>
            <strong>{resumo.consultas}</strong>
          </article>
          <article>
            <span>Chamadas cobradas</span>
            <strong>{resumo.cobradas}</strong>
          </article>
          <article>
            <span>Do cache</span>
            <strong>{resumo.cache}</strong>
          </article>
          <article>
            <span>Custo estimado</span>
            <strong>{dolares(resumo.custo_usd)}</strong>
          </article>
        </section>
      ) : null}

      {resumo ? (
        <p className="mudo">
          {resumo.tokens_entrada.toLocaleString("pt-BR")} tokens de entrada e{" "}
          {resumo.tokens_saida.toLocaleString("pt-BR")} de saída nas chamadas cobradas.
          {resumo.sem_tabela > 0
            ? ` ${resumo.sem_tabela} consulta(s) ficaram sem preço porque o modelo não está na tabela.`
            : ""}
        </p>
      ) : null}

      <section className="cartao grafico">
        <h3>Custo por dia</h3>
        <GraficoDias dias={dias} />
      </section>

      <section className="cartao grafico">
        <h3>Custo por documento</h3>
        <Barras documentos={porDocumento} />
      </section>
    </main>
  )
}

function GraficoDias({ dias }) {
  if (dias.length === 0) return <p className="mudo">Nenhuma consulta registrada ainda.</p>
  const maior = Math.max(...dias.map((dia) => dia.custo), 0.000001)
  const largura = 640
  const altura = 180
  const margem = 28
  const passo = (largura - margem) / dias.length
  const barra = Math.min(36, passo * 0.55)
  return (
    <svg className="desenho" viewBox={`0 0 ${largura} ${altura}`} role="img" aria-label="Custo estimado por dia">
      {dias.map((dia, indice) => {
        const alto = Math.max(4, ((altura - 46) * dia.custo) / maior)
        const x = margem + indice * passo + (passo - barra) / 2
        const y = altura - 28 - alto
        return (
          <g key={dia.dia}>
            <title>{`${diaCurto(dia.dia)}: ${dolares(dia.custo)}, ${dia.cobradas} cobradas, ${dia.cache} em cache`}</title>
            <rect x={x} y={y} width={barra} height={alto} rx="6" className="barra" />
            <text x={x + barra / 2} y={altura - 10} textAnchor="middle" className="eixo">
              {diaCurto(dia.dia)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function Barras({ documentos }) {
  const visiveis = documentos.filter((item) => item.custo > 0)
  if (visiveis.length === 0) return <p className="mudo">Ainda não há custo associado a um documento.</p>
  const maior = Math.max(...visiveis.map((item) => item.custo))
  return (
    <ul className="barras">
      {visiveis.map((item) => (
        <li key={item.codigo}>
          <span>{item.codigo}</span>
          <span className="trilho" aria-hidden="true">
            <span style={{ width: `${Math.max(6, (item.custo / maior) * 100)}%` }} />
          </span>
          <strong>{dolares(item.custo)}</strong>
        </li>
      ))}
    </ul>
  )
}
