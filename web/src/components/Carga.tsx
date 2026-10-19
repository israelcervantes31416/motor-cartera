/**
 * Los estados de una pantalla que espera a la API: cargando, error (de la API o de la red), vacio
 * y listo. Nunca una pantalla en blanco ni una espera infinita: un error dice que paso, con su
 * codigo y la ruta, sin un traceback, y se puede reintentar.
 */
import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ErrorDeApi, ErrorDeRed } from "../api/cliente";

export function Cargando({ que = "los datos" }: { readonly que?: string }) {
  return (
    <p className="cargando" role="status" aria-live="polite">
      <span className="cargando__giro" aria-hidden="true" />
      Cargando {que}…
    </p>
  );
}

export function mensajeDeError(error: unknown): { titulo: string; detalle: string } {
  if (error instanceof ErrorDeRed) {
    return {
      titulo:
        error.causa === "TIEMPO_AGOTADO"
          ? "La API tardó demasiado en responder"
          : "La API no está disponible",
      detalle: `${error.message} (${error.ruta})`,
    };
  }
  if (error instanceof ErrorDeApi && error.estado === 403) {
    return {
      titulo: "Tu sesión no tiene permiso para ver esto",
      detalle: `${error.message} (HTTP 403, ${error.ruta})`,
    };
  }
  if (error instanceof ErrorDeApi) {
    return {
      titulo:
        error.estado >= 500 ? "La API no está disponible" : `La API respondió ${error.codigo}`,
      detalle: `${error.message} (HTTP ${error.estado}, ${error.ruta})`,
    };
  }
  return { titulo: "Algo salió mal", detalle: "La pantalla no se pudo mostrar." };
}

export function ErrorDeCarga({
  error,
  reintentar,
}: {
  readonly error: unknown;
  readonly reintentar?: () => void;
}) {
  const { titulo, detalle } = mensajeDeError(error);
  const detalles = error instanceof ErrorDeApi ? error.detalles : [];
  return (
    <div className="alerta alerta--error" role="alert">
      <p className="alerta__titulo">
        <span aria-hidden="true">✕ </span>
        {titulo}
      </p>
      <p>{detalle}</p>
      {detalles.length > 0 ? (
        <ul>
          {detalles.map((d, i) => (
            <li key={i}>
              {d.campo !== null ? <code>{d.campo}</code> : null} {d.problema}
            </li>
          ))}
        </ul>
      ) : null}
      {reintentar !== undefined ? (
        <button type="button" className="boton" onClick={reintentar}>
          Reintentar
        </button>
      ) : null}
    </div>
  );
}

export function Vacio({ children }: { readonly children: ReactNode }) {
  return (
    <p className="vacio">
      <span aria-hidden="true">∅ </span>
      {children}
    </p>
  );
}

/** Lo que una consulta muestra en cada estado. `que` dice que se carga, para el lector de pantalla. */
export function Resultado<T>({
  consulta,
  que,
  children,
}: {
  readonly consulta: UseQueryResult<T>;
  readonly que?: string;
  readonly children: (datos: T) => ReactNode;
}) {
  if (consulta.isPending) return <Cargando {...(que !== undefined ? { que } : {})} />;
  if (consulta.isError) {
    return (
      <ErrorDeCarga
        error={consulta.error}
        reintentar={() => {
          void consulta.refetch();
        }}
      />
    );
  }
  return <>{children(consulta.data)}</>;
}
