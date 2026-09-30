import { useCallback, useEffect, useState } from "react"
import { carregarUso } from "./api"

export function useRegistro() {
  const [uso, setUso] = useState(null)
  const [erro, setErro] = useState("")
  const [carregando, setCarregando] = useState(true)

  const atualizar = useCallback(async () => {
    setCarregando(true)
    try {
      setUso(await carregarUso())
      setErro("")
    } catch (falha) {
      setErro(falha.message)
    } finally {
      setCarregando(false)
    }
  }, [])

  useEffect(() => {
    atualizar()
  }, [atualizar])

  return { uso, erro, carregando, atualizar }
}
