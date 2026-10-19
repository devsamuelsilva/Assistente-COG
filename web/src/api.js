import { acompanhar } from "./carga"

export async function api(caminho, opcoes = {}, rotulo = "Carregando…") {
  const pedido = pedir(caminho, opcoes)
  return rotulo ? acompanhar(rotulo, pedido) : pedido
}

async function pedir(caminho, opcoes) {
  const resposta = await fetch(caminho, opcoes)
  const corpo = await resposta.json().catch(() => ({}))
  if (!resposta.ok) {
    const detalhe = corpo.detail
    throw new Error(typeof detalhe === "string" ? detalhe : "A operação falhou.")
  }
  return corpo
}

export function carregarAcervo() {
  return api("/api/acervo", {}, "Lendo o acervo…")
}

export function enviarArquivo(arquivo, aoProgredir) {
  // fetch não informa o andamento do envio; o XMLHttpRequest informa.
  const envio = new Promise((resolve, reject) => {
    const dados = new FormData()
    dados.append("arquivo", arquivo)
    const pedido = new XMLHttpRequest()
    pedido.open("POST", "/api/acervo")
    pedido.upload.onprogress = (evento) => {
      if (evento.lengthComputable && aoProgredir) aoProgredir(Math.round((100 * evento.loaded) / evento.total))
    }
    pedido.onload = () => {
      let corpo = {}
      try {
        corpo = JSON.parse(pedido.responseText)
      } catch {
        // resposta sem JSON: segue com o status
      }
      if (pedido.status >= 200 && pedido.status < 300) {
        if (aoProgredir) aoProgredir(100)
        resolve(corpo)
      } else {
        reject(new Error(typeof corpo.detail === "string" ? corpo.detail : "A operação falhou."))
      }
    }
    pedido.onerror = () => reject(new Error("Falha de rede ao enviar o arquivo."))
    pedido.send(dados)
  })
  return acompanhar(`Enviando ${arquivo.name}…`, envio)
}

export function salvarMeta(meta) {
  return api(
    "/api/acervo/meta",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(meta),
    },
    "Salvando os dados do documento…",
  )
}

export function removerArquivo(arquivo) {
  return api(
    "/api/acervo",
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arquivo }),
    },
    "Removendo o documento…",
  )
}

export function zerarCustos() {
  return api("/api/uso/zerar", { method: "POST" }, "Zerando a contagem de custo…")
}

export function somarTodoOCusto() {
  return api("/api/uso/zerar", { method: "DELETE" }, "Voltando a somar todo o histórico…")
}

export function indexarAcervo() {
  return api("/api/indexar", { method: "POST" }, "Iniciando a indexação…")
}

export function estadoIndexacao() {
  return api("/api/indexar", {}, null)
}

export function consultar(pergunta, historico = []) {
  return api(
    "/api/chat",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pergunta, historico }),
    },
    "Consultando o normativo…",
  )
}

export function consultarOns(pergunta, historico = []) {
  return api(
    "/api/ons/chat",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pergunta, historico }),
    },
    "Consultando os dados do ONS…",
  )
}

export function carregarAvaliacao({ silencioso = false } = {}) {
  return api("/api/avaliacao", {}, silencioso ? null : "Lendo a avaliação…")
}

export function carregarProgresso() {
  return api("/api/avaliacao/progresso", {}, null)
}

export function enviarGabarito(arquivo) {
  const dados = new FormData()
  dados.append("arquivo", arquivo)
  return api("/api/avaliacao/gabarito", { method: "POST", body: dados }, "Importando o gabarito…")
}

export function rodarAvaliacao(opcoes = {}) {
  return api(
    "/api/avaliacao/rodar",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(opcoes),
    },
    "Iniciando a rodada…",
  )
}

export function carregarRevisao() {
  return api("/api/avaliacao/revisao", {}, "Carregando a revisão…")
}

export function revisarPergunta(revisao) {
  return api(
    "/api/avaliacao/revisao",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(revisao),
    },
    "Salvando a revisão…",
  )
}

export function desfazerRevisao(identificador) {
  return api(
    `/api/avaliacao/revisao/${encodeURIComponent(identificador)}`,
    { method: "DELETE" },
    "Desfazendo a revisão…",
  )
}

export function enviarRetorno(retorno) {
  return api(
    "/api/retorno",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(retorno),
    },
    "Registrando o retorno…",
  )
}

export function carregarTreino({ silencioso = false } = {}) {
  return api("/api/treino", {}, silencioso ? null : "Lendo o treinamento…")
}

export function treinarDocumento(arquivo) {
  return api(
    "/api/treino",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arquivo }),
    },
    "Agendando o treino…",
  )
}

export function carregarRodada(identificador) {
  return api(`/api/avaliacao/rodadas/${encodeURIComponent(identificador)}`, {}, "Abrindo a rodada…")
}
