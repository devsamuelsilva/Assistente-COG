import { useMemo, useState } from "react"
import { agruparPorDocumento, formatarQuando } from "./formato"
import Leitura from "./Leitura"
import { useRegistro } from "./registro"

const LIMITE = 10

export default function Historico() {
  const { uso, erro, carregando, atualizar } = useRegistro()
  const [busca, setBusca] = useState("")
  const termo = busca.trim().toLocaleLowerCase("pt-BR")
  const consultas = (uso?.consultas ?? []).filter((consulta) => consulta.origem !== "avaliacao")
  const base = termo
    ? consultas.filter((consulta) =>
        `${consulta.pergunta}\n${consulta.resposta}`.toLocaleLowerCase("pt-BR").includes(termo),
      )
    : consultas.slice(0, LIMITE)
  const grupos = useMemo(() => agruparPorDocumento(base), [base])

  return (
    <main className="conteudo">
      <section className="resumo">
        <div>
          <h2>Histórico</h2>
          <p>
            As últimas {LIMITE} perguntas, separadas pelo documento citado. Use a busca para ver se essa resposta já está
            no registro.
          </p>
        </div>
        <button type="button" className="primario" onClick={atualizar} disabled={carregando}>
          {carregando ? "Atualizando…" : "Atualizar"}
        </button>
      </section>

      <label className="busca">
        Buscar pergunta ou resposta
        <input
          value={busca}
          placeholder="Ex.: requisitos do RAI"
          onChange={(evento) => setBusca(evento.target.value)}
        />
      </label>

      {erro ? <p className="aviso faixa">{erro}</p> : null}

      {grupos.map((grupo) => (
        <section key={grupo.codigo} className="grupo">
          <header>
            <h3>{grupo.codigo}</h3>
            <p className="mudo">
              {grupo.revisao ? `revisão ${grupo.revisao} · ` : ""}
              {grupo.consultas.length} {grupo.consultas.length === 1 ? "consulta" : "consultas"}
            </p>
          </header>
          <div className="lista">
            {grupo.consultas.map((consulta, posicao) => (
              <article key={`${consulta.quando}-${posicao}`} className="cartao consulta-log">
                <p className="codigo">{formatarQuando(consulta.quando)}</p>
                <h3>{consulta.pergunta}</h3>
                <Leitura texto={consulta.resposta} />
                <p className="mudo">{rotuloCitacao(consulta, grupo.codigo)}</p>
              </article>
            ))}
          </div>
        </section>
      ))}

      {uso && grupos.length === 0 ? (
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
