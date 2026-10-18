/**
 * Un identificador o un hash largo: abreviado a la vista, completo al copiarlo. Sin dependencias:
 * la API del portapapeles del navegador, y si no esta, el texto completo queda seleccionable.
 */
import { useState } from "react";

import { abreviar } from "../lib/formato";

export function Copiable({
  valor,
  etiqueta,
  largo = 8,
}: {
  readonly valor: string;
  readonly etiqueta: string;
  readonly largo?: number;
}) {
  const [copiado, setCopiado] = useState<"si" | "no" | null>(null);

  async function copiar() {
    try {
      await navigator.clipboard.writeText(valor);
      setCopiado("si");
    } catch {
      setCopiado("no");
    }
    window.setTimeout(() => {
      setCopiado(null);
    }, 2_000);
  }

  return (
    <span className="copiable">
      <code className="copiable__valor" title={valor}>
        {abreviar(valor, largo)}
      </code>
      <button
        type="button"
        className="boton boton--icono"
        onClick={() => {
          void copiar();
        }}
        aria-label={`Copiar ${etiqueta}: ${valor}`}
      >
        <span aria-hidden="true">⧉</span>
      </button>
      <span className="copiable__aviso" role="status" aria-live="polite">
        {copiado === "si" ? "Copiado" : copiado === "no" ? "No se pudo copiar" : ""}
      </span>
    </span>
  );
}
