const MARCA = "(?:[ivx]{2,4}|\\d{1,2}|[a-z])\\)"

export const ROTULOS_STATUS = {
  acerto: "Acerto",
  parcial: "Parcial",
  erro: "Erro",
  recusa_indevida: "Recusa indevida",
  invencao: "Invenção",
  falha: "Falha técnica",
}

const ITEM = new RegExp(`^(?:(${MARCA})|(\\d{1,2}\\.)|([-•*–]))\\s+(\\S[\\s\\S]*)$`, "i")
const PARAGRAFO_LONGO = 420
const PARAGRAFO_ALVO = 300

/**
 * Divide um texto corrido em parágrafos de duas ou três frases. Só mexe em texto longo escrito numa linha só:
 * quem já quebrou as linhas decidiu como quer o texto.
 */
function dividirParagrafoLongo(texto) {
  if (texto.length <= PARAGRAFO_LONGO || texto.includes("\n")) return [texto]
  const frases = texto.split(/(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ"(])/)
  const paragrafos = []
  let atual = ""
  for (const frase of frases) {
    atual = atual ? `${atual} ${frase}` : frase
    if (atual.length >= PARAGRAFO_ALVO) {
      paragrafos.push(atual)
      atual = ""
    }
  }
  if (atual) {
    // sobra curta cola no parágrafo anterior, para não ficar uma frase solta no fim
    if (paragrafos.length > 0 && atual.length < 100) paragrafos[paragrafos.length - 1] += ` ${atual}`
    else paragrafos.push(atual)
  }
  return paragrafos
}

/**
 * Transforma a resposta em blocos para a tela: parágrafos (separados por linha em branco) e listas
 * (linhas com "- ", "•", "1." ou "a)"). Itens "a) … b) …" escritos na mesma linha também viram lista.
 */
export function blocosDeLeitura(texto) {
  const bruto = String(texto || "").replace(/\r\n/g, "\n").trim()
  if (!bruto) return []
  const linhas = bruto
    .replace(new RegExp(`[ \\t]+(?=${MARCA}\\s+\\S)`, "gi"), "\n")
    .split("\n")
    .map((linha) => ({ recuo: linha.length - linha.trimStart().length, texto: linha.trim() }))
  const partes = []
  let lista = null
  let paragrafo = null
  for (const { recuo, texto: linha } of linhas) {
    if (!linha) {
      lista = null
      paragrafo = null
      continue
    }
    const item = linha.match(ITEM)
    if (item) {
      if (!lista) {
        lista = []
        partes.push({ tipo: "lista", itens: lista })
      }
      paragrafo = null
      lista.push({ marca: item[1] || item[2] || "•", texto: item[4], nivel: recuo >= 2 ? 1 : 0 })
      continue
    }
    lista = null
    if (paragrafo) paragrafo.texto = `${paragrafo.texto}\n${linha}`
    else {
      paragrafo = { tipo: "texto", texto: linha }
      partes.push(paragrafo)
    }
  }
  return partes.flatMap((parte) =>
    parte.tipo === "texto" ? dividirParagrafoLongo(parte.texto).map((texto) => ({ tipo: "texto", texto })) : [parte],
  )
}

export function dolares(valor) {
  return Number(valor || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  })
}

const ETAPAS = {
  indexando: ["Indexando o acervo", ""],
  "lendo os documentos": ["Lendo os documentos", "documentos"],
  "transcrevendo quadros": ["Transcrevendo quadros e figuras", "figuras"],
  "visão geral": ["Gerando a visão geral", "documentos"],
  "perguntas de busca": ["Gerando as perguntas de busca", "trechos"],
  vetores: ["Calculando os vetores de busca", "trechos"],
  "perguntas de treino": ["Treinando os documentos", "documentos"],
  "reindexando com as perguntas aprendidas": ["Reindexando com as perguntas aprendidas", ""],
  "conferindo com paráfrases": ["Conferindo com paráfrases", "documentos"],
  "amostra com resposta completa": ["Conferindo uma amostra de respostas", "documentos"],
}

export function descricaoEtapa(situacao) {
  if (!situacao?.etapa) return "Começando…"
  const [rotulo, unidade] = ETAPAS[situacao.etapa] || [situacao.etapa, ""]
  const contagem = situacao.total && unidade ? ` (${situacao.feitos} de ${situacao.total} ${unidade})` : ""
  return `${rotulo}${contagem}`
}

export function custoDaConsulta(consulta) {
  if (consulta.cache) return { texto: "Do cache · sem custo", cobrada: false }
  if (consulta.custo_usd === null || consulta.custo_usd === undefined) {
    return { texto: "Sem preço na tabela", cobrada: false }
  }
  const tokens = `${consulta.tokens_prompt.toLocaleString("pt-BR")} de entrada, ${consulta.tokens_resposta.toLocaleString("pt-BR")} de saída`
  return { texto: `${dolares(consulta.custo_usd)} · ${tokens}`, cobrada: true }
}

export function numero(valor) {
  return Number(valor).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatarQuando(quando) {
  if (!quando) return "sem data"
  const data = new Date(quando)
  if (Number.isNaN(data.getTime())) return quando
  return data.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" })
}

export function diaCurto(dia) {
  const partes = String(dia).split("-")
  if (partes.length !== 3) return dia
  return `${partes[2]}/${partes[1]}`
}

export function documentosDaConsulta(consulta) {
  const vistos = new Set()
  const documentos = []
  for (const citacao of consulta.citacoes || []) {
    const codigo = citacao.codigo || "Sem código"
    if (vistos.has(codigo)) continue
    vistos.add(codigo)
    documentos.push({ codigo, revisao: citacao.revisao || "" })
  }
  if (documentos.length === 0) {
    return [{ codigo: "Sem documento citado", revisao: "" }]
  }
  return documentos
}

export function agruparPorDocumento(consultas) {
  const grupos = new Map()
  for (const consulta of consultas) {
    for (const documento of documentosDaConsulta(consulta)) {
      const atual = grupos.get(documento.codigo) || {
        codigo: documento.codigo,
        revisao: documento.revisao,
        consultas: [],
      }
      if (!atual.revisao && documento.revisao) atual.revisao = documento.revisao
      atual.consultas.push(consulta)
      grupos.set(documento.codigo, atual)
    }
  }
  return [...grupos.values()]
}
