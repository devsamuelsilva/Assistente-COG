import { useEffect, useState } from "react"

const CHAVE = "assistente-cog:tema"
export const TEMAS = ["sistema", "claro", "escuro"]
export const ROTULOS_TEMA = { sistema: "Tema do sistema", claro: "Tema claro", escuro: "Tema escuro" }

const consultaEscuro = () => window.matchMedia("(prefers-color-scheme: dark)")

function lerEscolha() {
  const salvo = localStorage.getItem(CHAVE)
  return TEMAS.includes(salvo) ? salvo : "sistema"
}

function aplicar(escolha) {
  const efetivo = escolha === "sistema" ? (consultaEscuro().matches ? "escuro" : "claro") : escolha
  document.documentElement.dataset.tema = efetivo
}

export function useTema() {
  const [escolha, setEscolha] = useState(lerEscolha)

  useEffect(() => {
    aplicar(escolha)
    localStorage.setItem(CHAVE, escolha)
    if (escolha !== "sistema") return undefined
    const consulta = consultaEscuro()
    const mudou = () => aplicar("sistema")
    consulta.addEventListener("change", mudou)
    return () => consulta.removeEventListener("change", mudou)
  }, [escolha])

  function alternar() {
    setEscolha((atual) => TEMAS[(TEMAS.indexOf(atual) + 1) % TEMAS.length])
  }

  return { escolha, alternar }
}
