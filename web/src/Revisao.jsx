import { useEffect, useState } from "react"
import { carregarRevisao, desfazerRevisao, revisarPergunta } from "./api"
import Carregando from "./Carregando"
import { ROTULOS_STATUS as STATUS } from "./formato"
import Leitura from "./Leitura"

const VEREDITOS = {
  confere: "Confere",
  corrigida: "Corrigida",
  descartada: "Descartada",
}

export default function Revisao({ rodada, onMudou }) {
  const [dados, setDados] = useState(null)
  const [erro, setErro] = useState("")
  const [filtro, setFiltro] = useState("pendentes")

  useEffect(() => {
    carregarRevisao()
      .then(setDados)
      .catch((falha) => setErro(falha.message))
  }, [])

  async function salvar(acao) {
    setErro("")
    try {
      setDados(await acao())
      onMudou?.()
    } catch (falha) {
      setErro(falha.message)
    }
  }

  if (!dados) return erro ? <p className="aviso faixa">{erro}</p> : <Carregando texto="Sorteando a amostra…" />

  const obtidas = Object.fromEntries((rodada?.itens ?? []).map((item) => [item.id, item]))
  const pendentes = dados.itens.filter((item) => !item.revisao?.veredito)
  const feitas = dados.itens.filter((item) => item.revisao?.veredito)
  const lista = filtro === "pendentes" ? pendentes : filtro === "feitas" ? feitas : dados.itens

  return (
    <>
      <section className="cartao">
        <h3>Revisão do gabarito pelo COG</h3>
        <p className="mudo">
          {dados.total} perguntas sorteadas por categoria. Confira a resposta esperada no normativo: a corrigida vale nas
          próximas rodadas e a descartada sai da conta. A acertividade nas revisadas é o número para apresentar.
        </p>
        <span className="trilho" aria-hidden="true">
          <span style={{ width: `${Math.round((100 * dados.feitas) / (dados.total || 1))}%` }} />
        </span>
        <p className="mudo">
          {dados.feitas} de {dados.total} revisadas.
        </p>
      </section>

      {erro ? <p className="aviso faixa">{erro}</p> : null}

      <div className="filtros" role="group" aria-label="Filtrar revisão">
        <button type="button" className={filtro === "pendentes" ? "ativo" : ""} onClick={() => setFiltro("pendentes")}>
          Pendentes ({pendentes.length})
        </button>
        <button type="button" className={filtro === "feitas" ? "ativo" : ""} onClick={() => setFiltro("feitas")}>
          Revisadas ({feitas.length})
        </button>
        <button type="button" className={filtro === "todas" ? "ativo" : ""} onClick={() => setFiltro("todas")}>
          Todas ({dados.itens.length})
        </button>
      </div>

      <div className="lista">
        {lista.map((item) => (
          <ItemRevisao
            key={item.id}
            item={item}
            obtida={obtidas[item.id]}
            onRevisar={(revisao) => salvar(() => revisarPergunta({ id: item.id, ...revisao }))}
            onDesfazer={() => salvar(() => desfazerRevisao(item.id))}
          />
        ))}
        {lista.length === 0 ? (
          <p className="mudo">{filtro === "pendentes" ? "Todas as perguntas da amostra foram revisadas." : "Nada aqui."}</p>
        ) : null}
      </div>
    </>
  )
}

function ItemRevisao({ item, obtida, onRevisar, onDesfazer }) {
  const [corrigindo, setCorrigindo] = useState(false)
  const [texto, setTexto] = useState(item.resposta_esperada)
  const [observacao, setObservacao] = useState(item.revisao?.observacao ?? "")
  const feita = item.revisao?.veredito

  return (
    <article className={`cartao revisao ${feita ? "feita" : ""}`}>
      <header className="cartao-topo">
        <div>
          <p className="codigo">
            {item.id}
            {item.categoria ? <span> · {item.categoria}</span> : null}
            {item.referencia ? <span> · {item.referencia}</span> : item.secao ? <span> · seção {item.secao}</span> : null}
          </p>
          <h3>{item.pergunta}</h3>
        </div>
        {feita ? <span className={`selo-estado veredito-${feita}`}>{VEREDITOS[feita]}</span> : null}
      </header>
      <div className="comparacao">
        <div>
          <p className="rotulo">Esperada no gabarito</p>
          {item.deve_recusar ? (
            <p className="mudo">Deve recusar: o ponto não está no normativo.</p>
          ) : (
            <Leitura texto={item.resposta_esperada} />
          )}
          {feita === "corrigida" ? (
            <>
              <p className="rotulo">Corrigida pelo COG</p>
              <Leitura texto={item.revisao.resposta_corrigida} />
            </>
          ) : null}
        </div>
        <div>
          <p className="rotulo">
            Sistema na rodada escolhida
            {obtida ? ` · ${STATUS[obtida.status] ?? obtida.status}` : ""}
          </p>
          {obtida ? <Leitura texto={obtida.resposta || "Sem resposta."} /> : <p className="mudo">Sem rodada escolhida.</p>}
        </div>
      </div>
      {feita ? (
        <div className="botoes">
          {item.revisao.observacao ? <span className="mudo">Observação: {item.revisao.observacao}</span> : null}
          <button type="button" className="link" onClick={onDesfazer}>
            Desfazer revisão
          </button>
        </div>
      ) : (
        <>
          {corrigindo ? (
            <textarea
              rows={4}
              value={texto}
              aria-label="Resposta corrigida"
              onChange={(evento) => setTexto(evento.target.value)}
            />
          ) : null}
          <input
            value={observacao}
            maxLength={1000}
            placeholder="Observação (opcional): quem conferiu, item do normativo…"
            onChange={(evento) => setObservacao(evento.target.value)}
          />
          <div className="botoes">
            {corrigindo ? (
              <>
                <button
                  type="button"
                  className="primario"
                  disabled={!texto.trim()}
                  onClick={() => onRevisar({ veredito: "corrigida", resposta_corrigida: texto, observacao })}
                >
                  Salvar correção
                </button>
                <button type="button" className="link" onClick={() => setCorrigindo(false)}>
                  Cancelar
                </button>
              </>
            ) : (
              <>
                <button type="button" onClick={() => onRevisar({ veredito: "confere", observacao })}>
                  Confere
                </button>
                <button type="button" onClick={() => setCorrigindo(true)}>
                  Corrigir resposta
                </button>
                <button type="button" onClick={() => onRevisar({ veredito: "descartada", observacao })}>
                  Descartar pergunta
                </button>
              </>
            )}
          </div>
        </>
      )}
    </article>
  )
}
