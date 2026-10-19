import { useEffect, useMemo, useState } from "react"
import { agruparPorDocumento, custoDaConsulta, dolares, formatarQuando } from "./formato"
import Carregando from "./Carregando"
import Leitura from "./Leitura"
import { invalidarRegistro, useConsultas, useMedidasOns, useUso } from "./registro"
import Ranking from "./Ranking"
import { GraficosUso, ResumoUso } from "./Uso"
import { somarTodoOCusto, zerarCustos } from "./api"

const LIMITE = 10

export default function Historico() {
  const [busca, setBusca] = useState("")
  const [termo, setTermo] = useState("")
  const [soNaoUteis, setSoNaoUteis] = useState(false)
  const [documento, setDocumento] = useState("")
  const { dados, erro, carregando, atualizar } = useConsultas({
    limite: documento ? 50 : LIMITE,
    busca: termo,
    naoUteis: soNaoUteis,
    documento,
  })
  const naoUteis = dados?.nao_uteis ?? 0
  const uteis = dados?.uteis ?? 0
  const grupos = useMemo(() => agruparPorDocumento(dados?.consultas ?? []), [dados])
  const uso = useUso()
  const ons = useMedidasOns()

  useEffect(() => {
    const espera = setTimeout(() => setTermo(busca.trim()), 300)
    return () => clearTimeout(espera)
  }, [busca])

  return (
    <main className="conteudo">
      <section className="resumo">
        <div>
          <h2>Histórico de consultas e custo</h2>
          <p>
            O ranking mostra quais normativos mais geram perguntas e custo. Abaixo, as últimas {LIMITE} perguntas, agrupadas
            pelo normativo citado, cada uma com o custo estimado da IA. A busca procura em todo o registro.
          </p>
        </div>
        <button
          type="button"
          className="primario"
          onClick={() => {
            atualizar()
            uso.atualizar()
            ons.atualizar()
          }}
          disabled={carregando || uso.carregando}
        >
          {carregando || uso.carregando ? "Atualizando…" : "Atualizar"}
        </button>
      </section>

      {uso.erro ? <p className="aviso faixa">{uso.erro}</p> : null}
      <ResumoUso
        uso={uso.dados}
        onZerar={async () => {
          if (!window.confirm("Zerar a contagem de custo a partir de agora? Nada é apagado: o histórico e o custo de cada pergunta continuam, e dá para voltar a somar tudo.")) return
          await zerarCustos()
          invalidarRegistro()
        }}
        onSomarTudo={async () => {
          await somarTodoOCusto()
          invalidarRegistro()
        }}
      />
      {uso.dados ? <Ranking linhas={uso.dados.ranking_documentos ?? []} filtro={documento} onFiltrar={setDocumento} /> : null}
      {documento ? (
        <p className="alerta-resposta">
          Mostrando só as perguntas de {documento}.{" "}
          <button type="button" className="link" onClick={() => setDocumento("")}>
            Ver todas
          </button>
        </p>
      ) : null}
      {uso.dados ? (
        <details className="detalhes-uso">
          <summary>Ver custo por origem, por dia e por documento</summary>
          <GraficosUso uso={uso.dados} ons={ons} />
        </details>
      ) : null}

      <label className="busca">
        Buscar pergunta ou resposta
        <input
          value={busca}
          placeholder="Ex.: requisitos do RAI"
          onChange={(evento) => setBusca(evento.target.value)}
        />
      </label>

      {uteis + naoUteis > 0 ? (
        <div className="filtros" role="group" aria-label="Filtrar pelo retorno dos operadores">
          <button type="button" className={soNaoUteis ? "" : "ativo"} onClick={() => setSoNaoUteis(false)}>
            Todas · {uteis} úteis, {naoUteis} não úteis
          </button>
          <button type="button" className={soNaoUteis ? "ativo" : ""} onClick={() => setSoNaoUteis(true)}>
            Só as marcadas como não úteis ({naoUteis})
          </button>
        </div>
      ) : null}

      {erro ? <p className="aviso faixa">{erro}</p> : null}

      {carregando && !dados ? <Carregando texto="Lendo o registro de consultas…" /> : null}

      {grupos.map((grupo) => (
        <section key={grupo.codigo} className="grupo">
          <header>
            <h3>{grupo.codigo}</h3>
            <p className="mudo">
              {grupo.revisao ? `revisão ${grupo.revisao} · ` : ""}
              {grupo.consultas.length} {grupo.consultas.length === 1 ? "consulta" : "consultas"}
              {" · "}
              {dolares(grupo.consultas.reduce((soma, consulta) => soma + (consulta.cache ? 0 : consulta.custo_usd || 0), 0))}
            </p>
          </header>
          <div className="lista">
            {grupo.consultas.map((consulta, posicao) => (
              <article key={`${consulta.quando}-${posicao}`} className="cartao consulta-log">
                <p className="codigo">
                  {formatarQuando(consulta.quando)}
                  {consulta.retorno ? (
                    <span className={`selo-retorno ${consulta.retorno.util ? "util" : "nao-util"}`}>
                      {consulta.retorno.util ? "Útil" : "Não útil"}
                    </span>
                  ) : null}
                  <span
                    className={`selo-custo ${custoDaConsulta(consulta).cobrada ? "cobrada" : ""}`}
                    title="Estimativa pela tabela de preços do modelo, somando a reescrita da pergunta quando houve"
                  >
                    {custoDaConsulta(consulta).texto}
                  </span>
                </p>
                <h3>{consulta.pergunta}</h3>
                <Leitura texto={consulta.resposta} />
                <p className="mudo">{rotuloCitacao(consulta, grupo.codigo)}</p>
                {consulta.retorno?.comentario ? (
                  <p className="aviso faixa">Comentário do operador: {consulta.retorno.comentario}</p>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ))}

      {dados && grupos.length === 0 ? (
        <p className="mudo">{termo ? "Nenhuma consulta encontrada com esse texto." : "Nenhuma pergunta registrada ainda."}</p>
      ) : null}
    </main>
  )
}

function rotuloCitacao(consulta, codigo) {
  const citacao = (consulta.citacoes || []).find((item) => (item.codigo || "Sem código") === codigo)
  if (!citacao?.secao) return consulta.coberto ? "Citou o normativo." : "Sem seção citada."
  return citacao.secao
}
