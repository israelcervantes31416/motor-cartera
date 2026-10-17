/**
 * La cabecera contextual: que cartera, que corte y que ejecucion de cada tipo se esta viendo, con su
 * estado, sus relojes, su version y su identificador completo. El resumen (cartera, corte,
 * compatibilidad y las ejecuciones que piden atencion) siempre esta a la vista; el detalle de cada
 * ejecucion se despliega, y el navegador de quien la ve recuerda si la dejo abierta.
 */
import type { UseQueryResult } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import type { ContextoValidado } from "../api/tipos";
import { Copiable } from "../components/Copiar";
import { InsigniaEstado } from "../components/Estado";
import {
  EJECUCIONES,
  NOMBRE_DE_EJECUCION,
  rutaConContexto,
  TIPO_DE_EJECUCION,
  type ClaveDeEjecucion,
  type Contexto,
} from "../lib/contexto";
import { formatearEntero, formatearFecha, formatearInstante } from "../lib/formato";

const CLAVE_ABIERTA = "motor-cartera:contexto-abierto";

// Una comodidad por persona: sin almacenamiento (ventana privada, sitio bloqueado) solo no se
// recuerda, y la cabecera se muestra plegada.
function leerAbierta(): boolean {
  try {
    return window.localStorage.getItem(CLAVE_ABIERTA) === "si";
  } catch {
    return false;
  }
}

function guardarAbierta(abierta: boolean): void {
  try {
    window.localStorage.setItem(CLAVE_ABIERTA, abierta ? "si" : "no");
  } catch {
    // Sin almacenamiento: no se recuerda.
  }
}

interface Atencion {
  readonly clave: ClaveDeEjecucion;
  readonly estado: string;
}

/** Las ejecuciones declaradas que no estan EXITOSA (o que no existen): las que el resumen nombra. */
function piden(contexto: Contexto, datos: ContextoValidado): Atencion[] {
  return EJECUCIONES.flatMap((clave) => {
    if (contexto[clave] === undefined) return [];
    const ejecucion = datos.ejecuciones.find((e) => e.tipo === TIPO_DE_EJECUCION[clave]);
    if (ejecucion === undefined) return [{ clave, estado: "EJECUCION_NO_ENCONTRADA" }];
    return ejecucion.estado === "EXITOSA" ? [] : [{ clave, estado: ejecucion.estado }];
  });
}

export function BarraDeContexto({
  contexto,
  validado,
}: {
  readonly contexto: Contexto;
  readonly validado: UseQueryResult<ContextoValidado>;
}) {
  const datos = validado.data;
  const [abierta, setAbierta] = useState(leerAbierta);
  const declaradas = EJECUCIONES.filter((clave) => contexto[clave] !== undefined).length;
  return (
    <details
      className="contexto"
      open={abierta}
      onToggle={(evento) => {
        const ahora = evento.currentTarget.open;
        setAbierta(ahora);
        guardarAbierta(ahora);
      }}
    >
      <summary className="contexto__resumen">
        <span className="contexto__titulo">Contexto</span>
        {datos !== undefined ? (
          <>
            <span>{datos.corte.cartera_id}</span>
            <span>Corte {formatearFecha(datos.corte.fecha_corte)}</span>
            <InsigniaEstado estado={datos.compatible ? "COMPATIBLE" : "INCOMPATIBLE"} />
            <span className="contexto__declaradas">
              {declaradas} de {EJECUCIONES.length} ejecuciones declaradas
            </span>
            {piden(contexto, datos).map((a) => (
              <span key={a.clave}>
                {NOMBRE_DE_EJECUCION[a.clave]} <InsigniaEstado estado={a.estado} />
              </span>
            ))}
          </>
        ) : (
          <span>{validado.isError ? "No se pudo validar" : "Validando…"}</span>
        )}
        <span className="contexto__abrir">
          {abierta ? "Ocultar las ejecuciones" : "Ver las ejecuciones"}
        </span>
      </summary>
      <div className="contexto__cuerpo">
        <dl className="contexto__lista">
          <div className="contexto__item">
            <dt>Corte</dt>
            <dd>
              {datos !== undefined ? (
                <>
                  {datos.corte.cartera_id} · {formatearFecha(datos.corte.fecha_corte)} ·{" "}
                  {formatearEntero(datos.corte.cuentas)} cuentas
                </>
              ) : null}{" "}
              <Copiable valor={contexto.corte} etiqueta="el identificador del corte" />
            </dd>
          </div>
          {EJECUCIONES.map((clave) => {
            const declarada = contexto[clave];
            const ejecucion = datos?.ejecuciones.find((e) => e.tipo === TIPO_DE_EJECUCION[clave]);
            return (
              <div className="contexto__item" key={clave}>
                <dt>{NOMBRE_DE_EJECUCION[clave]}</dt>
                <dd>
                  {declarada === undefined ? (
                    <InsigniaEstado estado="NO_DECLARADA" />
                  ) : ejecucion === undefined ? (
                    <>
                      {datos !== undefined ? (
                        <InsigniaEstado estado="EJECUCION_NO_ENCONTRADA" />
                      ) : null}{" "}
                      <Copiable valor={declarada} etiqueta={`el identificador de ${clave}`} />
                    </>
                  ) : (
                    <>
                      <InsigniaEstado estado={ejecucion.estado} />{" "}
                      {ejecucion.as_of !== null ? (
                        <span>as_of {formatearFecha(ejecucion.as_of)}</span>
                      ) : null}{" "}
                      {ejecucion.known_at !== null ? (
                        <span>
                          known_at{" "}
                          {formatearInstante(
                            ejecucion.known_at,
                            ejecucion.zona_horaria ?? undefined,
                          )}
                        </span>
                      ) : null}{" "}
                      {ejecucion.fecha_operacion !== null ? (
                        <span>operación {formatearFecha(ejecucion.fecha_operacion)}</span>
                      ) : null}{" "}
                      {ejecucion.version_motor !== null ? (
                        <code>{ejecucion.version_motor}</code>
                      ) : null}{" "}
                      <Copiable
                        valor={ejecucion.run_id}
                        etiqueta={`el identificador de ${clave}`}
                      />
                    </>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
        <Link className="boton" to={rutaConContexto("/", contexto)}>
          Cambiar el contexto
        </Link>
      </div>
    </details>
  );
}
