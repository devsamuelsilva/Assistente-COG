import { Fragment, useEffect, useRef, useState } from "react"
import { carregarAcervo, enviarArquivo, estadoIndexacao, indexarAcervo, removerArquivo, salvarMeta } from "./api"
import Carregando, { Progresso } from "./Carregando"
import { descricaoEtapa } from "./formato"

const FILTROS = {
  todos: { rotulo: "Todos", aceita: () => true },
  consulta: { rotulo: "Na consulta", aceita: (documento) => documento.responde },
  aviso: { rotulo: "Com aviso", aceita: (documento) => documento.avisos.length > 0 },
  fora: { rotulo: "Fora da consulta", aceita: (documento) => !documento.responde },
}

export default function Acervo({ estado, onEstado, onErro }) {
  const [enviando, setEnviando] = useState(false)
  const [indexando, setIndexando] = useState(false)
  const [situacaoIndexacao, setSituacaoIndexacao] = useState(null)
  const [envio, setEnvio] = useState(0)
  const [arrastando, setArrastando] = useState(false)
  const [busca, setBusca] = useState("")
  const [filtro, setFiltro] = useState("todos")
  const [aberto, setAberto] = useState({ arquivo: "", painel: "" })
  const [avisosIndexacao, setAvisosIndexacao] = useState([])
  const documentos = estado?.documentos ?? []
  const indice = estado?.indice
  const termo = busca.trim().toLocaleLowerCase("pt-BR")
  const visiveis = documentos.filter(
    (documento) =>
      FILTROS[filtro].aceita(documento) &&
      (!termo ||
        `${documento.codigo}\n${documento.titulo}\n${documento.arquivo}`.toLocaleLowerCase("pt-BR").includes(termo)),
  )

  async function executar(acao) {
    onErro("")
    try {
      onEstado(await acao())
      return true
    } catch (falha) {
      onErro(falha.message)
      return false
    }
  }

  async function mudar(acao) {
    const ok = await executar(acao)
    if (ok) setIndexando(true)
    return ok
  }

  async function subir(arquivo) {
    setEnviando(true)
    setEnvio(0)
    onErro("")
    try {
      const novo = await enviarArquivo(arquivo, setEnvio)
      onEstado(novo)
      // Os dados do documento abrem antes de tudo: a indexação só começa quando o usuário os confirma.
      setAberto({ arquivo: novo.enviado || arquivo.name, painel: "editar" })
    } catch (falha) {
      onErro(falha.message)
    }
    setEnviando(false)
  }

  useEffect(() => {
    let ativo = true
    estadoIndexacao()
      .then((situacao) => {
        if (!ativo) return
        if (situacao.rodando) {
          setSituacaoIndexacao(situacao)
          setIndexando(true)
        } else setAvisosIndexacao(situacao.avisos ?? [])
      })
      .catch(() => {})
    return () => {
      ativo = false
    }
  }, [])

  useEffect(() => {
    if (!indexando) return undefined
    let parado = false
    let espera = null
    async function acompanharIndexacao() {
      try {
        const situacao = await estadoIndexacao()
        if (parado) return
        if (situacao.rodando) {
          setSituacaoIndexacao(situacao)
          espera = setTimeout(acompanharIndexacao, 1000)
          return
        }
        setSituacaoIndexacao(null)
        setIndexando(false)
        setAvisosIndexacao(situacao.avisos ?? [])
        await executar(carregarAcervo)
        if (situacao.erro) onErro(situacao.erro)
      } catch {
        if (!parado) espera = setTimeout(acompanharIndexacao, 3000)
      }
    }
    espera = setTimeout(acompanharIndexacao, 600)
    return () => {
      parado = true
      clearTimeout(espera)
    }
    // executar e onErro mudam a cada render; o acompanhamento depende só de estar indexando.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indexando])

  async function indexar() {
    onErro("")
    try {
      await indexarAcervo()
      setIndexando(true)
    } catch (falha) {
      onErro(falha.message)
    }
  }

  async function remover(documento) {
    const confirmado = window.confirm(
      `Remover "${documento.arquivo}" do acervo?\n\nO arquivo é apagado da pasta acervo e a indexação roda de novo sem ele.`,
    )
    if (confirmado && (await mudar(() => removerArquivo(documento.arquivo)))) {
      setAberto({ arquivo: "", painel: "" })
    }
  }

  function alternar(arquivo, painel) {
    setAberto((atual) =>
      atual.arquivo === arquivo && atual.painel === painel ? { arquivo: "", painel: "" } : { arquivo, painel },
    )
  }

  return (
    <main className="conteudo">
      <section className="resumo">
        <div>
          <h2>Acervo de normativos</h2>
          <p>
            Normativos e ITOMs que a consulta usa. Ao enviar um documento, a tela de dados abre primeiro; a indexação e o
            treino começam quando você salva. Ao editar ou remover, rodam sozinhos.
          </p>
        </div>
        <button type="button" className="primario" onClick={indexar} disabled={indexando || documentos.length === 0}>
          {indexando ? "Preparando…" : "Indexar e treinar de novo"}
        </button>
      </section>

      {indexando ? (
        <Progresso
          percentual={situacaoIndexacao?.percentual ?? 0}
          texto="Preparando o acervo"
          detalhe={`${descricaoEtapa(situacaoIndexacao)}. Na primeira vez de um documento pode levar alguns minutos. A consulta já usa o índice assim que a indexação (primeira metade da barra) termina.${situacaoIndexacao?.pendente ? " Há outra preparação na fila, por mudança feita durante esta." : ""}`}
        />
      ) : null}

      {!indexando && avisosIndexacao.length > 0 ? (
        <section className="cartao">
          <h3>Avisos da última indexação</h3>
          <ul className="avisos">
            {avisosIndexacao.map((aviso) => (
              <li key={aviso}>{aviso}</li>
            ))}
          </ul>
        </section>
      ) : null}

      {estado ? <Situacao documentos={documentos} indice={indice} /> : <Carregando texto="Lendo o acervo…" />}

      <section className="cartao">
        <ol className="passos">
          <li>
            <strong>Envie o arquivo</strong>
            <span>A pré-análise lê páginas, seções e vigência neste PC.</span>
          </li>
          <li>
            <strong>Confira os dados</strong>
            <span>A tela de dados abre sozinha. Código, revisão e título vêm do arquivo: corrija e salve.</span>
          </li>
          <li>
            <strong>Acompanhe o treino</strong>
            <span>A consulta enxerga o documento quando a indexação termina. O resultado do treino fica em Treinamento.</span>
          </li>
        </ol>
        <p className="nota">
          Trecho é a parte do texto que a busca entrega ao assistente. Documento fora de vigência não entra nas respostas.
        </p>
      </section>

      <label
        className={`envio ${arrastando ? "arrastando" : ""}`}
        onDragOver={(evento) => {
          evento.preventDefault()
          setArrastando(true)
        }}
        onDragLeave={() => setArrastando(false)}
        onDrop={(evento) => {
          evento.preventDefault()
          setArrastando(false)
          const arquivo = evento.dataTransfer.files?.[0]
          if (arquivo) subir(arquivo)
        }}
      >
        <input
          type="file"
          accept=".pdf,.docx,.txt,.md,application/pdf"
          disabled={enviando}
          onChange={(evento) => {
            const arquivo = evento.target.files?.[0]
            if (arquivo) subir(arquivo)
            evento.target.value = ""
          }}
        />
        <span className="secundario">{enviando ? "Lendo o arquivo…" : "Escolher arquivo"}</span>
        <span className="texto">
          <strong>Ou solte o normativo aqui</strong>
          <span>PDF, DOCX, TXT ou MD, até 25 MB.</span>
        </span>
      </label>

      {enviando ? (
        <Progresso
          percentual={envio}
          texto="Enviando o arquivo"
          detalhe={envio >= 100 ? "Arquivo recebido. Fazendo a pré-análise…" : "O envio termina antes de a pré-análise começar."}
        />
      ) : null}

      {documentos.length > 0 ? (
        <div className="ferramentas">
          <input
            value={busca}
            placeholder="Buscar por código, título ou arquivo"
            aria-label="Buscar documento"
            onChange={(evento) => setBusca(evento.target.value)}
          />
          <div className="filtros" role="group" aria-label="Filtrar documentos">
            {Object.entries(FILTROS).map(([id, item]) => (
              <button key={id} type="button" className={filtro === id ? "ativo" : ""} onClick={() => setFiltro(id)}>
                {item.rotulo} ({documentos.filter(item.aceita).length})
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="grade">
        {visiveis.map((documento) => {
          const aberta = aberto.arquivo === documento.arquivo ? aberto.painel : ""
          return (
            <Fragment key={documento.arquivo}>
              <Cartao documento={documento} aberta={aberta} onAbrir={(painel) => alternar(documento.arquivo, painel)} />
              {aberta ? (
                <Detalhe
                  documento={documento}
                  painel={aberta}
                  onFechar={() => setAberto({ arquivo: "", painel: "" })}
                  onSalvar={async (meta) => {
                    const novo = !documento.meta
                    const ok = await mudar(() => salvarMeta(meta))
                    if (ok && novo) setAberto({ arquivo: "", painel: "" })
                  }}
                  onRemover={() => remover(documento)}
                />
              ) : null}
            </Fragment>
          )
        })}
      </div>
      {estado && documentos.length > 0 && visiveis.length === 0 ? (
        <p className="mudo">Nenhum documento com esse filtro.</p>
      ) : null}
    </main>
  )
}

function Situacao({ documentos, indice }) {
  const naConsulta = documentos.filter((documento) => documento.responde).length
  let estado = <p className="estado ok">Índice atualizado</p>
  if (!indice?.existe) estado = <p className="estado aviso">Sem índice. Indexe para liberar a consulta.</p>
  else if (indice.desatualizado) estado = <p className="estado aviso">O acervo mudou. Indexe de novo.</p>
  return (
    <section className="situacao">
      <article className="numeros-item cartao">
        <span className="mudo">Documentos no acervo</span>
        <strong>{documentos.length}</strong>
      </article>
      <article className="numeros-item cartao">
        <span className="mudo">Na consulta</span>
        <strong>{naConsulta}</strong>
      </article>
      <article className="numeros-item cartao">
        <span className="mudo">Trechos na consulta</span>
        <strong>{indice?.vigentes ?? 0}</strong>
      </article>
      {estado}
    </section>
  )
}

function Cartao({ documento, aberta, onAbrir }) {
  const [verAvisos, setVerAvisos] = useState(false)
  const avisoSumario = documento.avisos.find((aviso) => aviso.startsWith("O sumário"))
  const outros = documento.avisos.filter((aviso) => aviso !== avisoSumario)
  const secoes = documento.secoes.length
  const paginas = documento.paginas.length

  return (
    <article className={`cartao doc ${aberta ? "aberto" : ""}`}>
      <header className="cartao-topo">
        <p className="codigo">
          {documento.codigo}
          {documento.revisao ? <span> · revisão {documento.revisao}</span> : null}
        </p>
        {documento.meta ? <Selo documento={documento} /> : <span className="selo-estado aviso">Confirme os dados</span>}
      </header>
      <h3 className="doc-titulo" title={documento.titulo}>
        {documento.titulo}
      </h3>
      <p className="doc-numeros">
        <span>
          <strong>{paginas}</strong> {paginas === 1 ? "página" : "páginas"}
        </span>
        <span>
          <strong>{secoes}</strong> {secoes === 1 ? "seção" : "seções"}
        </span>
        <span>
          <strong>{documento.trechos}</strong> {documento.trechos === 1 ? "trecho" : "trechos"}
        </span>
      </p>

      {avisoSumario ? <p className="faixa aviso doc-aviso">{avisoSumario}</p> : null}
      {!avisoSumario && documento.sumario ? (
        <p className="nota">Sumário conferido: as {documento.sumario.no_sumario} seções estão no índice.</p>
      ) : null}

      {outros.length > 0 ? (
        <div>
          <button type="button" className="link" onClick={() => setVerAvisos((atual) => !atual)}>
            {verAvisos ? "Ocultar avisos" : `${outros.length} ${outros.length === 1 ? "aviso" : "avisos"}`}
          </button>
          {verAvisos ? (
            <ul className="avisos">
              {outros.map((aviso) => (
                <li key={aviso}>{aviso}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <div className="doc-acoes">
        <button
          type="button"
          className={`secundario ${aberta === "secoes" ? "ativo" : ""}`}
          onClick={() => onAbrir("secoes")}
          disabled={secoes === 0}
        >
          Seções
        </button>
        <button
          type="button"
          className={`secundario ${aberta === "editar" ? "ativo" : ""}`}
          onClick={() => onAbrir("editar")}
        >
          {documento.meta ? "Editar dados" : "Confirmar dados"}
        </button>
      </div>
      <p className="nota" title={documento.arquivo}>
        {documento.arquivo}
      </p>
    </article>
  )
}

function Detalhe({ documento, painel, onFechar, onSalvar, onRemover }) {
  const novo = !documento.meta
  const secao = useRef(null)
  useEffect(() => {
    if (novo && painel === "editar") secao.current?.scrollIntoView({ behavior: "smooth", block: "center" })
  }, [novo, painel, documento.arquivo])
  return (
    <section className="cartao doc-detalhe" ref={secao}>
      <header className="cartao-topo">
        <div>
          <p className="codigo">{documento.codigo}</p>
          <h3>{painel === "secoes" ? "Seções encontradas no documento" : novo ? "Confirme os dados do documento" : "Dados do documento"}</h3>
        </div>
        <button type="button" className="secundario" onClick={onFechar}>
          Fechar
        </button>
      </header>
      {painel === "secoes" ? <Secoes documento={documento} /> : null}
      {painel === "editar" ? <Dados documento={documento} novo={novo} onSalvar={onSalvar} onRemover={onRemover} /> : null}
    </section>
  )
}

function Secoes({ documento }) {
  return (
    <div className="colunas">
      <div>
        <h4>Seção e página no PDF</h4>
        <ol className="secoes">
          {documento.secoes.map((secao) => (
            <li key={`${secao.nome}-${secao.pagina}`}>
              <span>{secao.nome}</span>
              <em>p. {secao.pagina}</em>
            </li>
          ))}
        </ol>
      </div>
      <div>
        <h4>Como conferir</h4>
        <p className="nota">
          Compare esta lista com o sumário do PDF. Seção que falta aqui ficou colada na anterior e a resposta pode citar
          a seção errada. Depois de corrigir o arquivo, indexe de novo.
        </p>
        {documento.inicio ? (
          <details className="previa">
            <summary>Ver o início do texto extraído</summary>
            <p className="inicio">{documento.inicio}</p>
          </details>
        ) : null}
      </div>
    </div>
  )
}

function Dados({ documento, novo, onSalvar, onRemover }) {
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
    <div className="colunas">
      <form className="meta" onSubmit={salvar}>
        <div className="linha">
          <label>
            Código
            <input value={codigo} onChange={(evento) => setCodigo(evento.target.value)} required />
          </label>
          <label>
            Revisão
            <input value={revisao} onChange={(evento) => setRevisao(evento.target.value)} />
          </label>
        </div>
        <label>
          Título
          <input value={titulo} onChange={(evento) => setTitulo(evento.target.value)} required />
        </label>
        <label className="cheque">
          <input type="checkbox" checked={vigente} onChange={(evento) => setVigente(evento.target.checked)} />
          Vigente: entra nas respostas
        </label>
        <div className="botoes">
          <button type="submit" className={novo ? "primario" : ""} disabled={salvando}>
            {salvando ? "Salvando…" : novo ? "Salvar e processar" : "Salvar dados"}
          </button>
          <button type="button" className="perigo" onClick={onRemover}>
            Remover do acervo
          </button>
        </div>
      </form>
      <div>
        <h4>Para que servem</h4>
        <p className="nota">
          {novo
            ? "O documento foi recebido e analisado, mas ainda não foi indexado. Confira o código, a revisão e o título (eles aparecem na citação de cada resposta) e clique em Salvar e processar: a indexação e o treino começam com o nome certo."
            : "O código e a revisão aparecem na citação de cada resposta. Os dados ficam num arquivo .meta.json ao lado do normativo. Ao salvar, a indexação e o treino rodam de novo com os dados novos."}
        </p>
        {novo ? <p className="nota">Arquivo: {documento.arquivo}</p> : null}
      </div>
    </div>
  )
}

function Selo({ documento }) {
  if (documento.responde) return <span className="selo-estado ok">Na consulta</span>
  if (!documento.vigente) return <span className="selo-estado aviso">Fora de vigência</span>
  return <span className="selo-estado mudo-selo">Fora do índice</span>
}
