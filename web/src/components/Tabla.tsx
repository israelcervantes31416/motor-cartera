/**
 * Las tablas de la app: HTML semantico (caption, th con scope, aria-sort) sobre TanStack Table, con
 * el orden y la paginacion del lado del servidor. La tabla nunca ordena ni filtra las filas que
 * recibe: pide otra pagina u otro orden a la API.
 */
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowData,
} from "@tanstack/react-table";
import { useId, type ReactNode } from "react";

import { formatearEntero } from "../lib/formato";

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData extends RowData, TValue> {
    /** La llave de orden que la API acepta para esta columna, si se puede ordenar. */
    orden?: string;
    /** Alinea a la derecha: cantidades. */
    numerica?: boolean;
    /** Solo para que el compilador use los parametros de tipo. */
    _tipos?: [TData, TValue];
  }
}

export interface Orden {
  readonly columna: string;
  readonly descendente: boolean;
}

export function TablaDatos<T>({
  titulo,
  columnas,
  filas,
  orden,
  onOrden,
  claveDeFila,
  vacio = "Sin filas.",
  descripcion,
}: {
  readonly titulo: string;
  readonly columnas: ColumnDef<T>[];
  readonly filas: readonly T[];
  readonly orden?: Orden | undefined;
  readonly onOrden?: ((orden: Orden) => void) | undefined;
  readonly claveDeFila?: (fila: T, indice: number) => string;
  readonly vacio?: ReactNode;
  readonly descripcion?: ReactNode;
}) {
  // La app no usa React Compiler: el aviso es sobre la memoizacion del compilador, y la tabla se
  // rehace en cada render a proposito.
  // eslint-disable-next-line react-hooks/incompatible-library -- sin React Compiler
  const tabla = useReactTable<T>({
    data: filas as T[],
    columns: columnas,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    ...(claveDeFila !== undefined
      ? { getRowId: (fila: T, indice: number) => claveDeFila(fila, indice) }
      : {}),
  });
  const id = useId();

  return (
    <div className="tabla-contenedor" role="region" aria-labelledby={id} tabIndex={0}>
      <table className="tabla">
        <caption id={id}>
          {titulo}
          {descripcion !== undefined ? (
            <span className="tabla__descripcion">{descripcion}</span>
          ) : null}
        </caption>
        <thead>
          {tabla.getHeaderGroups().map((grupo) => (
            <tr key={grupo.id}>
              {grupo.headers.map((encabezado) => {
                const meta = encabezado.column.columnDef.meta;
                const llave = meta?.orden;
                const activa = llave !== undefined && orden?.columna === llave;
                const contenido = encabezado.isPlaceholder
                  ? null
                  : flexRender(encabezado.column.columnDef.header, encabezado.getContext());
                return (
                  <th
                    key={encabezado.id}
                    scope="col"
                    className={meta?.numerica === true ? "tabla__numerica" : undefined}
                    aria-sort={
                      activa ? (orden.descendente ? "descending" : "ascending") : undefined
                    }
                  >
                    {llave !== undefined && onOrden !== undefined ? (
                      <button
                        type="button"
                        className="tabla__orden"
                        onClick={() => {
                          onOrden({
                            columna: llave,
                            descendente: activa ? !orden.descendente : false,
                          });
                        }}
                      >
                        {contenido}
                        <span aria-hidden="true">
                          {activa ? (orden.descendente ? " ▼" : " ▲") : " ↕"}
                        </span>
                      </button>
                    ) : (
                      contenido
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {tabla.getRowModel().rows.length === 0 ? (
            <tr>
              <td colSpan={columnas.length} className="tabla__vacia">
                {vacio}
              </td>
            </tr>
          ) : (
            tabla.getRowModel().rows.map((fila) => (
              <tr key={fila.id}>
                {fila.getVisibleCells().map((celda) => (
                  <td
                    key={celda.id}
                    className={
                      celda.column.columnDef.meta?.numerica === true ? "tabla__numerica" : undefined
                    }
                  >
                    {flexRender(celda.column.columnDef.cell, celda.getContext())}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

export const TAMANOS_DE_PAGINA = [25, 50, 100] as const;

export function Paginacion({
  total,
  pagina,
  porPagina,
  onPagina,
  onPorPagina,
  etiqueta,
  tamanos = TAMANOS_DE_PAGINA,
}: {
  readonly total: number;
  readonly pagina: number;
  readonly porPagina: number;
  readonly onPagina: (pagina: number) => void;
  readonly onPorPagina?: ((porPagina: number) => void) | undefined;
  readonly etiqueta: string;
  readonly tamanos?: readonly number[];
}) {
  const paginas = Math.max(1, Math.ceil(total / porPagina));
  const id = useId();
  return (
    <nav className="paginacion" aria-label={`Paginación de ${etiqueta}`}>
      <button
        type="button"
        className="boton"
        disabled={pagina <= 1}
        onClick={() => {
          onPagina(pagina - 1);
        }}
      >
        ← Anterior
      </button>
      <span className="paginacion__estado">
        Página {formatearEntero(pagina)} de {formatearEntero(paginas)} · {formatearEntero(total)}{" "}
        {total === 1 ? "fila" : "filas"}
      </span>
      <button
        type="button"
        className="boton"
        disabled={pagina >= paginas}
        onClick={() => {
          onPagina(pagina + 1);
        }}
      >
        Siguiente →
      </button>
      {onPorPagina !== undefined ? (
        <span className="paginacion__tamano">
          <label htmlFor={id}>Filas por página</label>
          <select
            id={id}
            value={porPagina}
            onChange={(evento) => {
              onPorPagina(Number(evento.target.value));
            }}
          >
            {tamanos.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </span>
      ) : null}
    </nav>
  );
}
