import { useSyncExternalStore } from "react"

const pendentes = new Map()
const ouvintes = new Set()
let proximo = 0
let retrato = { ativo: false, rotulo: "" }

function avisar() {
  const rotulos = [...pendentes.values()]
  retrato = { ativo: rotulos.length > 0, rotulo: rotulos.at(-1) ?? "" }
  ouvintes.forEach((ouvinte) => ouvinte())
}

export async function acompanhar(rotulo, promessa) {
  const id = (proximo += 1)
  pendentes.set(id, rotulo)
  avisar()
  try {
    return await promessa
  } finally {
    pendentes.delete(id)
    avisar()
  }
}

function assinar(ouvinte) {
  ouvintes.add(ouvinte)
  return () => ouvintes.delete(ouvinte)
}

export function useCarga() {
  return useSyncExternalStore(assinar, () => retrato)
}
