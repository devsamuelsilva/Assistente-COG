import { diaCurto, dolares, formatarQuando, numero } from "./formato"

export function ResumoUso({ uso, onZerar, onSomarTudo }) {
  const resumo = uso?.resumo
  if (!resumo) return null
  return (
    <>
      <p className="mudo">
        {uso.desde
          ? `Contando desde ${formatarQuando(uso.desde)}. As ${uso.antes_do_marco} consultas anteriores não entram nestes totais (todo o histórico somou ${dolares(uso.custo_total_historico)}). O custo de cada pergunta continua aparecendo abaixo.`
          : "Contando todo o histórico."}{" "}
        <button type="button" className="link" onClick={onZerar}>
          Zerar custos a partir de agora
        </button>
        {uso.desde ? (
          <>
            {" · "}
            <button type="button" className="link" onClick={onSomarTudo}>
              Voltar a somar tudo
            </button>
          </>
        ) : null}
      </p>
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
      <p className="mudo">
        {resumo.tokens_entrada.toLocaleString("pt-BR")} tokens de entrada
        {resumo.tokens_reaproveitados > 0
          ? ` (${resumo.tokens_reaproveitados.toLocaleString("pt-BR")} reaproveitados pela OpenAI)`
          : ""}{" "}
        e {resumo.tokens_saida.toLocaleString("pt-BR")} de saída nas chamadas cobradas. Estimativa pela tabela do{" "}
        {resumo.tabela.modelo}: US$ {numero(resumo.tabela.entrada_usd_por_milhao)} por milhão de tokens de entrada (US${" "}
        {numero(resumo.tabela.entrada_cache_usd_por_milhao)} na parte reaproveitada) e US${" "}
        {numero(resumo.tabela.saida_usd_por_milhao)} na saída. Pergunta repetida sai do cache, sem custo.
        {resumo.sem_tabela > 0
          ? ` ${resumo.sem_tabela} consulta(s) ficaram sem preço porque o modelo não está na tabela.`
          : ""}
      </p>
    </>
  )
}

export function GraficosUso({ uso, ons }) {
  const dias = uso?.dias ?? []
  const origens = uso?.origens ?? []
  const rotulos = Object.fromEntries(origens.map((origem) => [origem.origem, origem.rotulo]))
  return (
    <>
      {origens.length > 0 ? (
        <section className="cartao grafico">
          <h3>Custo por origem</h3>
          <Barras
            itens={origens.map((origem) => ({
              rotulo: `${origem.rotulo} · ${origem.consultas} ${origem.consultas === 1 ? "consulta" : "consultas"}`,
              custo: origem.custo_usd,
            }))}
            vazio="Nenhuma chamada cobrada ainda."
          />
        </section>
      ) : null}

      <section className="cartao grafico">
        <h3>Custo por dia</h3>
        <GraficoDias dias={dias} rotulos={rotulos} />
      </section>

      <section className="cartao">
        <h3>Chat ONS: tempo e passos</h3>
        <MedidasOns medidas={ons?.dados?.consultas ?? []} />
      </section>
    </>
  )
}

function segundos(ms) {
  return `${(ms / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} s`
}

function MedidasOns({ medidas }) {
  if (medidas.length === 0) {
    return <p className="mudo">As medidas começam a ser gravadas na próxima pergunta feita no chat ONS.</p>
  }
  return (
    <div className="rolagem">
      <table className="medidas">
        <thead>
          <tr>
            <th>Quando</th>
            <th>Pergunta</th>
            <th>Total</th>
            <th>GPT</th>
            <th>ONS</th>
            <th>Passos</th>
            <th>Entrada</th>
            <th>Reaproveitada</th>
            <th>Repetições</th>
            <th>Custo</th>
          </tr>
        </thead>
        <tbody>
          {medidas.map((item) => (
            <tr key={item.id || item.quando}>
              <td>{formatarQuando(item.quando)}</td>
              <td className="pergunta-curta" title={item.pergunta}>
                {item.pergunta}
              </td>
              <td>{segundos(item.duracao_ms)}</td>
              {item.cache ? (
                <td colSpan={6} className="mudo">
                  Do cache
                </td>
              ) : (
                <>
                  <td>{segundos(item.ms_gpt)}</td>
                  <td>{segundos(item.ms_ons)}</td>
                  <td>{item.passos}</td>
                  <td>{item.tokens_prompt.toLocaleString("pt-BR")}</td>
                  <td>
                    {item.tokens_prompt ? `${Math.round((item.tokens_cache / item.tokens_prompt) * 100)}%` : "—"}
                  </td>
                  <td>{item.repeticoes}</td>
                </>
              )}
              <td>{dolares(item.custo_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function GraficoDias({ dias, rotulos }) {
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
            <title>{`${diaCurto(dia.dia)}: ${dolares(dia.custo)}, ${dia.cobradas} cobradas, ${dia.cache} em cache${partesDoDia(dia, rotulos)}`}</title>
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

function partesDoDia(dia, rotulos) {
  const partes = Object.entries(dia.origens || {})
    .filter(([, custo]) => custo > 0)
    .map(([origem, custo]) => `${rotulos[origem] || origem} ${dolares(custo)}`)
  return partes.length > 0 ? ` (${partes.join(", ")})` : ""
}

function Barras({ itens, vazio }) {
  const visiveis = itens.filter((item) => item.custo > 0)
  if (visiveis.length === 0) return <p className="mudo">{vazio}</p>
  const maior = Math.max(...visiveis.map((item) => item.custo))
  return (
    <ul className="barras">
      {visiveis.map((item) => (
        <li key={item.rotulo}>
          <span>{item.rotulo}</span>
          <span className="trilho" aria-hidden="true">
            <span style={{ width: `${Math.max(6, (item.custo / maior) * 100)}%` }} />
          </span>
          <strong>{dolares(item.custo)}</strong>
        </li>
      ))}
    </ul>
  )
}
