/**
 * Indicadores y fichas: una lista de definiciones (dl) con su etiqueta y su valor. Un indicador sin
 * valor muestra su estado (components/Estado.tsx), nunca un cero.
 */
import type { ReactNode } from "react";

export function Indicadores({
  children,
  etiqueta,
}: {
  readonly children: ReactNode;
  readonly etiqueta: string;
}) {
  return (
    <dl className="indicadores" aria-label={etiqueta}>
      {children}
    </dl>
  );
}

export function Indicador({
  etiqueta,
  children,
  detalle,
}: {
  readonly etiqueta: string;
  readonly children: ReactNode;
  readonly detalle?: ReactNode;
}) {
  return (
    <div className="indicador">
      <dt className="indicador__etiqueta">{etiqueta}</dt>
      <dd className="indicador__valor">{children}</dd>
      {detalle !== undefined ? <dd className="indicador__detalle">{detalle}</dd> : null}
    </div>
  );
}

/** Pares de llave y valor, densos: los metadatos de una ejecucion, de una version o de una cuenta. */
export function Ficha({
  filas,
  etiqueta,
}: {
  readonly filas: readonly (readonly [string, ReactNode] | null | false)[];
  readonly etiqueta: string;
}) {
  return (
    <dl className="ficha" aria-label={etiqueta}>
      {filas
        .filter((fila): fila is readonly [string, ReactNode] => Boolean(fila))
        .map(([llave, valor]) => (
          <div className="ficha__fila" key={llave}>
            <dt>{llave}</dt>
            <dd>{valor}</dd>
          </div>
        ))}
    </dl>
  );
}
