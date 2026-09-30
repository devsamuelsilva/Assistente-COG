const MARCA = "(?:[ivx]{2,4}|\\d{1,2}|[a-z])\\)"

export function blocosDeLeitura(texto) {
  const bruto = String(texto || "").replace(/\r\n/g, "\n").trim()
  if (!bruto) return []
  const linhas = bruto
    .replace(new RegExp(`[ \\t]+(?=${MARCA}\\s+\\S)`, "gi"), "\n")
    .split("\n")
    .map((linha) => linha.trim())
    .filter(Boolean)
  const partes = []
  let lista = null
  for (const linha of linhas) {
    const item = linha.match(new RegExp(`^(${MARCA})\\s+([\\s\\S]+)$`, "i"))
    if (item) {
      if (!lista) {
        lista = []
        partes.push({ tipo: "lista", itens: lista })
      }
      lista.push({ marca: item[1], texto: item[2] })
      continue
    }
    lista = null
    const ultimo = partes[partes.length - 1]
    if (ultimo?.tipo === "texto") ultimo.texto = `${ultimo.texto}\n${linha}`
    else partes.push({ tipo: "texto", texto: linha })
  }
  return partes
}

export function dolares(valor) {
  return Number(valor || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 6,
  })
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

export function serieDiaria(consultas) {
  const mapa = new Map()
  for (const consulta of [...consultas].reverse()) {
    const dia = (consulta.quando || "").slice(0, 10) || "sem data"
    const atual = mapa.get(dia) || { dia, custo: 0, cobradas: 0, cache: 0 }
    if (consulta.cache) atual.cache += 1
    else if (consulta.custo_usd != null) {
      atual.cobradas += 1
      atual.custo += consulta.custo_usd
    }
    mapa.set(dia, atual)
  }
  return [...mapa.values()]
}

export function custoPorDocumento(consultas) {
  const mapa = new Map()
  for (const consulta of consultas) {
    const custo = consulta.custo_usd || 0
    const documentos = documentosDaConsulta(consulta)
    const parte = custo / documentos.length
    for (const documento of documentos) {
      mapa.set(documento.codigo, (mapa.get(documento.codigo) || 0) + parte)
    }
  }
  return [...mapa.entries()]
    .map(([codigo, custo]) => ({ codigo, custo }))
    .sort((a, b) => b.custo - a.custo)
}
