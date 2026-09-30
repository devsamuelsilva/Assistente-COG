export async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, opcoes)
  const corpo = await resposta.json().catch(() => ({}))
  if (!resposta.ok) {
    const detalhe = corpo.detail
    throw new Error(typeof detalhe === "string" ? detalhe : "A operação falhou.")
  }
  return corpo
}

export function carregarAcervo() {
  return api("/api/acervo")
}

export function enviarArquivo(arquivo) {
  const dados = new FormData()
  dados.append("arquivo", arquivo)
  return api("/api/acervo", { method: "POST", body: dados })
}

export function salvarMeta(meta) {
  return api("/api/acervo/meta", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(meta),
  })
}

export function removerArquivo(arquivo) {
  return api("/api/acervo", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arquivo }),
  })
}

export function indexarAcervo() {
  return api("/api/indexar", { method: "POST" })
}

export function consultar(pergunta, historico = []) {
  return api("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pergunta, historico }),
  })
}

export function carregarUso() {
  return api("/api/uso")
}

export function carregarAvaliacao() {
  return api("/api/avaliacao")
}

export function enviarGabarito(arquivo) {
  const dados = new FormData()
  dados.append("arquivo", arquivo)
  return api("/api/avaliacao/gabarito", { method: "POST", body: dados })
}

export function rodarAvaliacao() {
  return api("/api/avaliacao/rodar", { method: "POST" })
}

export function carregarRodada(identificador) {
  return api(`/api/avaliacao/rodadas/${encodeURIComponent(identificador)}`)
}
