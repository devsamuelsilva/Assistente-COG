// O chat não sabe quanto a resposta vai demorar. A barra usa a mediana das últimas respostas deste navegador
// e se apresenta como estimativa ("≈"), não como contagem.
const PADRAO = { operacional: 6000, ons: 30000 }
const GUARDADAS = 10

function chave(tipo) {
  return `cog-duracoes-${tipo}`
}

export function duracaoPrevista(tipo) {
  try {
    const lista = JSON.parse(localStorage.getItem(chave(tipo)) || "[]").filter((valor) => Number.isFinite(valor) && valor > 0)
    if (lista.length >= 3) {
      const ordenada = [...lista].sort((a, b) => a - b)
      return ordenada[Math.floor(ordenada.length / 2)]
    }
  } catch {
    // localStorage bloqueado ou com conteúdo inválido: usa o padrão.
  }
  return PADRAO[tipo] ?? 8000
}

export function lembrarDuracao(tipo, ms) {
  try {
    const lista = JSON.parse(localStorage.getItem(chave(tipo)) || "[]")
    localStorage.setItem(chave(tipo), JSON.stringify([...lista, Math.round(ms)].slice(-GUARDADAS)))
  } catch {
    // sem localStorage, a próxima estimativa usa o padrão.
  }
}

/** Percentual estimado: sobe em linha reta até 95% no tempo previsto e depois só se aproxima de 99%. */
export function percentualEstimado(decorrido, previsto) {
  const razao = decorrido / Math.max(previsto, 1)
  if (razao <= 1) return 95 * razao
  return Math.min(99, 95 + 4 * (1 - Math.exp(-(razao - 1) * 2)))
}
