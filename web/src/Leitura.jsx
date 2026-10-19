import { blocosDeLeitura } from "./formato"

export default function Leitura({ texto }) {
  const partes = blocosDeLeitura(texto)
  if (partes.length === 0) return null
  return (
    <div className="leitura">
      {partes.map((parte, indice) =>
        parte.tipo === "lista" ? (
          <ul key={indice}>
            {parte.itens.map((item, posicao) => (
              <li key={posicao} className={item.nivel ? "subitem" : ""}>
                <span>{item.marca}</span>
                <span>{item.texto}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p key={indice}>{parte.texto}</p>
        ),
      )}
    </div>
  )
}
