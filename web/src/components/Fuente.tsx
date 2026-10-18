/**
 * De donde sale un bloque: su ejecucion, su estado y su identificador completo. Cada numero de la
 * app lleva la suya a la vista.
 */
import type { ReactNode } from "react";

import { Copiable } from "./Copiar";
import { InsigniaEstado } from "./Estado";

export function Fuente({
  nombre,
  runId,
  estado,
  children,
}: {
  readonly nombre: string;
  readonly runId: string | null | undefined;
  readonly estado: string;
  readonly children?: ReactNode;
}) {
  return (
    <p className="fuente">
      <span className="fuente__nombre">Fuente: {nombre}</span> <InsigniaEstado estado={estado} />
      {runId !== null && runId !== undefined ? (
        <>
          {" "}
          <Copiable valor={runId} etiqueta={`el identificador de ${nombre}`} />
        </>
      ) : null}
      {children !== undefined ? <span className="fuente__detalle"> {children}</span> : null}
    </p>
  );
}

/** Lo que dice un bloque cuya ejecucion no publico: por que no hay numeros, sin un cero. */
export function SinFuente({
  estado,
  resultado,
  nombre,
}: {
  readonly estado: string;
  readonly resultado?: string | null | undefined;
  readonly nombre: string;
}) {
  const texto =
    estado === "NO_DECLARADA"
      ? `El contexto no declara ${nombre}: no se busca otra ejecución.`
      : estado === "EN_PROCESO"
        ? `${nombre} sigue en proceso: todavía no publica.`
        : `${nombre} terminó sin publicar${resultado ? ` (${resultado})` : ""}.`;
  return (
    <p className="sin-fuente">
      <InsigniaEstado estado={estado} /> {texto}
    </p>
  );
}
