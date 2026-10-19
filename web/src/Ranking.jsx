import { useMemo, useState } from "react"
import { dolares } from "./formato"

const ORDENS = [
  ["custo_usd", "Custo"],
  ["perguntas", "Perguntas"],
  ["fracao_sem_resposta", "Sem resposta"],
]

function porcento(fracao) {
  return `${Math.round(100 * fracao)}%`
}

export default function Ranking({ linhas, filtro, onFiltrar }) {
  const [ordem, setOrdem] = useState("custo_usd")
  const ordenadas = useMemo(
    () => [...linhas].sort((a, b) => b[ordem] - a[ordem] || b.perguntas - a.perguntas),
    [linhas, ordem],
  )
  if (linhas.length === 0) {
    return (
      <section className="cartao">
        <h3>Normativos que mais geram perguntas e custo</h3>
        <p className="mudo">Ainda não há perguntas do chat normativo na contagem atual.</p>
      </section>
    )
  }
  const candidatos = linhas.filter((linha) => linha.candidato_a_treino)
  return (
    <section className="cartao ranking">
      <header className="ranking-topo">
        <div>
          <h3>Normativos que mais geram perguntas e custo</h3>
          <p className="mudo">
            Cada pergunta conta para um só normativo: o primeiro citado na resposta ou, se ela foi recusada, o mais
            provável. Avaliações e chat ONS ficam de fora. Clique em um normativo para ver as perguntas dele.
          </p>
        </div>
        <div className="filtros" role="group" aria-label="Ordenar o ranking">
          {ORDENS.map(([chave, rotulo]) => (
            <button key={chave} type="button" className={ordem === chave ? "ativo" : ""} onClick={() => setOrdem(chave)}>
              {rotulo}
            </button>
          ))}
        </div>
      </header>
      {candidatos.length > 0 ? (
        <p className="alerta-resposta">
          Candidatos a treino (muitas perguntas sem resposta): {candidatos.map((linha) => linha.codigo).join(", ")}. Na
          aba Treinamento, treine esses documentos.
        </p>
      ) : null}
      <div className="rolagem">
        <table className="medidas">
          <thead>
            <tr>
              <th>Normativo</th>
              <th>Perguntas</th>
              <th>Custo</th>
              <th>% do custo</th>
              <th>Por pergunta</th>
              <th>Do cache</th>
              <th>Sem resposta</th>
              <th>Parciais</th>
              <th>Não úteis</th>
            </tr>
          </thead>
          <tbody>
            {ordenadas.map((linha) => (
              <tr key={linha.codigo} className={filtro === linha.codigo ? "linha-ativa" : ""}>
                <td>
                  <button
                    type="button"
                    className="link"
                    onClick={() => onFiltrar(filtro === linha.codigo ? "" : linha.codigo)}
                    title="Ver as perguntas deste normativo no histórico"
                  >
                    {linha.codigo}
                  </button>
                  {linha.candidato_a_treino ? <span className="selo-estado aviso">treinar</span> : null}
                </td>
                <td>{linha.perguntas}</td>
                <td>{dolares(linha.custo_usd)}</td>
                <td>{porcento(linha.parte_do_custo)}</td>
                <td>{dolares(linha.custo_por_pergunta)}</td>
                <td>{linha.cache}</td>
                <td>
                  {linha.sem_resposta} ({porcento(linha.fracao_sem_resposta)})
                </td>
                <td>{linha.parciais}</td>
                <td>{linha.nao_uteis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
