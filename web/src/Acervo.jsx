import { useEffect, useState } from "react"
import { enviarArquivo, indexarAcervo, removerArquivo, salvarMeta } from "./api"

export default function Acervo({ estado, onEstado, onErro }) {
  const [enviando, setEnviando] = useState(false)
  const [indexando, setIndexando] = useState(false)
  const documentos = estado?.documentos ?? []
  const indice = estado?.indice

  async function executar(acao) {
    onErro("")
    try {
      onEstado(await acao())
    } catch (falha) {
      onErro(falha.message)
    }
  }

  async function aoSoltar(evento) {
    evento.preventDefault()
    const arquivo = evento.dataTransfer.files?.[0]
    if (arquivo) await subir(arquivo)
  }

  async function subir(arquivo) {
    setEnviando(true)
    await executar(() => enviarArquivo(arquivo))
    setEnviando(false)
  }

  async function indexar() {
    setIndexando(true)
    await executar(indexarAcervo)
    setIndexando(false)
  }

  return (
    <main className="conteudo">
      <section className="resumo">
        <div>
          <h2>Pré-análise</h2>
          <p>
            O envio lê páginas, seções e vigência neste PC. A consulta só enxerga o acervo depois de indexar.
          </p>
        </div>
        <button type="button" className="primario" onClick={indexar} disabled={indexando || documentos.length === 0}>
          {indexando ? "Indexando…" : "Indexar para a consulta"}
        </button>
      </section>

      {indice ? <BarraIndice indice={indice} /> : <p className="mudo">Lendo o acervo…</p>}

      <label
        className="soltar"
        onDragOver={(evento) => evento.preventDefault()}
        onDrop={aoSoltar}
      >
        <input
          type="file"
          accept=".pdf,.docx,.txt,.md,application/pdf"
          onChange={(evento) => {
            const arquivo = evento.target.files?.[0]
            if (arquivo) subir(arquivo)
            evento.target.value = ""
          }}
        />
        <strong>{enviando ? "Lendo o arquivo…" : "Solte o normativo aqui"}</strong>
        <span>PDF, DOCX, TXT ou MD, até 25 MB</span>
      </label>

      <div className="lista">
        {documentos.map((documento) => (
          <Cartao
            key={documento.arquivo}
            documento={documento}
            onSalvar={(meta) => executar(() => salvarMeta(meta))}
            onRemover={() => executar(() => removerArquivo(documento.arquivo))}
          />
        ))}
      </div>
    </main>
  )
}

function BarraIndice({ indice }) {
  if (!indice.existe) {
    return <p className="aviso faixa">Nenhum índice ainda. A consulta responde depois da primeira indexação.</p>
  }
  if (indice.desatualizado) {
    return (
      <p className="aviso faixa">
        O acervo mudou desde a última indexação. {indice.vigentes} trechos vigentes estão na consulta antiga.
      </p>
    )
  }
  return <p className="ok faixa">{indice.vigentes} trechos vigentes na consulta.</p>
}

function Cartao({ documento, onSalvar, onRemover }) {
  const [codigo, setCodigo] = useState(documento.codigo)
  const [revisao, setRevisao] = useState(documento.revisao)
  const [titulo, setTitulo] = useState(documento.titulo)
  const [vigente, setVigente] = useState(documento.vigente)
  const [salvando, setSalvando] = useState(false)

  useEffect(() => {
    setCodigo(documento.codigo)
    setRevisao(documento.revisao)
    setTitulo(documento.titulo)
    setVigente(documento.vigente)
  }, [documento.arquivo, documento.codigo, documento.revisao, documento.titulo, documento.vigente])

  async function salvar(evento) {
    evento.preventDefault()
    setSalvando(true)
    await onSalvar({ arquivo: documento.arquivo, codigo, revisao, titulo, vigente })
    setSalvando(false)
  }

  return (
    <article className="cartao">
      <header className="cartao-topo">
        <div>
          <p className="codigo">
            {documento.codigo}
            {documento.revisao ? <span> revisão {documento.revisao}</span> : null}
          </p>
          <h3>{documento.titulo}</h3>
          <p className="mudo">
            {documento.arquivo} · {documento.paginas.length}{" "}
            {documento.paginas.length === 1 ? "página" : "páginas"} · {documento.trechos}{" "}
            {documento.trechos === 1 ? "trecho" : "trechos"}
          </p>
        </div>
        <Selo documento={documento} />
      </header>

      {documento.avisos.length > 0 ? (
        <ul className="avisos">
          {documento.avisos.map((aviso) => (
            <li key={aviso}>{aviso}</li>
          ))}
        </ul>
      ) : null}

      {documento.inicio ? <p className="inicio">{documento.inicio}</p> : null}

      {documento.secoes.length > 0 ? (
        <ol className="secoes">
          {documento.secoes.map((secao) => (
            <li key={`${secao.nome}-${secao.pagina}`}>
              <span>{secao.nome}</span>
              <em>p. {secao.pagina}</em>
            </li>
          ))}
        </ol>
      ) : null}

      <form className="meta" onSubmit={salvar}>
        <label>
          Código
          <input value={codigo} onChange={(evento) => setCodigo(evento.target.value)} required />
        </label>
        <label>
          Revisão
          <input value={revisao} onChange={(evento) => setRevisao(evento.target.value)} />
        </label>
        <label className="largo">
          Título
          <input value={titulo} onChange={(evento) => setTitulo(evento.target.value)} required />
        </label>
        <label className="cheque">
          <input type="checkbox" checked={vigente} onChange={(evento) => setVigente(evento.target.checked)} />
          Vigente
        </label>
        <button type="submit" disabled={salvando}>
          {salvando ? "Salvando…" : "Salvar metadados"}
        </button>
        <button type="button" className="perigo" onClick={onRemover}>
          Remover
        </button>
      </form>
    </article>
  )
}

function Selo({ documento }) {
  if (documento.responde) return <span className="selo-estado ok">Entra na consulta</span>
  if (!documento.vigente) return <span className="selo-estado aviso">Fora de vigência</span>
  return <span className="selo-estado mudo-selo">Fora do índice</span>
}
