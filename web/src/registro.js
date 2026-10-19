import { useCallback, useEffect, useSyncExternalStore } from "react"
import { api } from "./api"

const ROTULO = "Lendo o registro de consultas…"
const entradas = new Map()

function entrada(caminho) {
  if (!entradas.has(caminho)) {
    entradas.set(caminho, { estado: { dados: null, erro: "", carregando: false }, ouvintes: new Set(), velho: true })
  }
  return entradas.get(caminho)
}

function publicar(atual, mudanca) {
  atual.estado = { ...atual.estado, ...mudanca }
  for (const ouvinte of atual.ouvintes) ouvinte()
}

async function buscar(caminho) {
  const atual = entrada(caminho)
  if (atual.pedido) return atual.pedido
  atual.velho = false
  publicar(atual, { carregando: true })
  atual.pedido = api(caminho, {}, atual.estado.dados ? null : ROTULO)
    .then((dados) => publicar(atual, { dados, erro: "", carregando: false }))
    .catch((falha) => publicar(atual, { erro: falha.message, carregando: false }))
    .finally(() => {
      atual.pedido = null
    })
  return atual.pedido
}

export function invalidarRegistro() {
  for (const [caminho, atual] of entradas) {
    atual.velho = true
    if (atual.ouvintes.size > 0) buscar(caminho)
  }
}

function useRegistroEm(caminho) {
  const assinar = useCallback(
    (ouvinte) => {
      const atual = entrada(caminho)
      atual.ouvintes.add(ouvinte)
      return () => atual.ouvintes.delete(ouvinte)
    },
    [caminho],
  )
  const estado = useSyncExternalStore(assinar, () => entrada(caminho).estado)

  useEffect(() => {
    if (entrada(caminho).velho) buscar(caminho)
  }, [caminho])

  const atualizar = useCallback(() => buscar(caminho), [caminho])
  return { ...estado, atualizar }
}

export function useUso() {
  return useRegistroEm("/api/uso")
}

export function usePerguntas(canal) {
  return useRegistroEm(`/api/uso/perguntas?canal=${encodeURIComponent(canal)}`)
}

export function useConsultas({ limite = 10, busca = "", naoUteis = false, documento = "" } = {}) {
  const parametros = new URLSearchParams({ limite: String(limite), busca, nao_uteis: String(naoUteis), documento })
  return useRegistroEm(`/api/uso/consultas?${parametros}`)
}

export function useMedidasOns() {
  return useRegistroEm("/api/uso/ons")
}
