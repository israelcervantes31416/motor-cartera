/**
 * El selector de contexto: la pantalla de entrada. Lista las carteras y sus cortes, y de un corte,
 * sus ejecuciones de cada tipo y los contextos compatibles que la API propone. La persona elige y
 * abre: nada se abre solo y nada se elige como "lo mas reciente". Lo que se va eligiendo vive en la
 * URL, asi que el selector a medio llenar tambien se puede copiar.
 */
import { useId } from "react";
import { Link, useSearchParams } from "react-router";

import { useContextoValidado, useOpciones } from "../../api/consultas";
import type { EjecucionDelContexto, Opciones, SugerenciaDeContexto } from "../../api/tipos";
import { ProblemasDeContexto } from "../../app/contexto";
import { Aviso } from "../../components/Avisos";
import { Cargando, ErrorDeCarga, Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado } from "../../components/Estado";
import { Paginacion } from "../../components/Tabla";
import {
  EJECUCIONES,
  leerContexto,
  NOMBRE_DE_EJECUCION,
  rutaConContexto,
  TIPO_DE_EJECUCION,
  type ClaveDeEjecucion,
  type Contexto,
} from "../../lib/contexto";
import { abreviar, formatearEntero, formatearFecha, formatearInstante } from "../../lib/formato";

const POR_PAGINA = 10;
const CARTERA = /^[A-Z0-9_]{1,32}$/;

const MOTIVOS: Readonly<Record<SugerenciaDeContexto["codigo"], string>> = {
  DESDE_OPTIMIZACION: "Una optimización y lo que declaró, con analytics si encadena.",
  DESDE_ANALYTICS: "Una ejecución de analytics y su manifiesto, con optimización si encadena.",
};

function descripcionDeEjecucion(e: EjecucionDelContexto): string {
  const partes = [e.estado];
  if (e.as_of !== null) partes.push(`as_of ${formatearFecha(e.as_of)}`);
  if (e.fecha_operacion !== null) partes.push(`operación ${formatearFecha(e.fecha_operacion)}`);
  partes.push(`iniciada ${formatearInstante(e.iniciada_en)}`);
  partes.push(abreviar(e.run_id));
  return partes.join(" · ");
}

function contextoDeSugerencia(s: SugerenciaDeContexto): Contexto {
  const c = s.contexto;
  const contexto: { corte: string } & Partial<Record<ClaveDeEjecucion, string>> = {
    corte: c.corte_id,
  };
  if (c.decision_run_id) contexto.decision = c.decision_run_id;
  if (c.analytics_run_id) contexto.analytics = c.analytics_run_id;
  if (c.scoring_run_id) contexto.scoring = c.scoring_run_id;
  if (c.optimizacion_run_id) contexto.optimizacion = c.optimizacion_run_id;
  if (c.campo_run_id) contexto.campo = c.campo_run_id;
  if (c.ruteo_run_id) contexto.ruteo = c.ruteo_run_id;
  return contexto;
}

export function SelectorDeContexto() {
  const [busqueda, setBusqueda] = useSearchParams();
  const { contexto } = leerContexto(busqueda);
  const carteraPedida = busqueda.get("cartera");
  const cartera = carteraPedida !== null && CARTERA.test(carteraPedida) ? carteraPedida : undefined;
  const pagina = Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1);
  const opciones = useOpciones({
    ...(cartera !== undefined ? { cartera_id: cartera } : {}),
    ...(contexto !== null ? { corte_id: contexto.corte } : {}),
    pagina,
    por_pagina: POR_PAGINA,
  });

  function cambiar(cambios: Record<string, string | undefined>) {
    const nueva = new URLSearchParams(busqueda);
    for (const [llave, valor] of Object.entries(cambios)) {
      if (valor === undefined || valor === "") nueva.delete(llave);
      else nueva.set(llave, valor);
    }
    setBusqueda(nueva, { replace: true });
  }

  return (
    <>
      <h1>Abrir un contexto operativo</h1>
      <p className="entrada">
        Un contexto fija el corte y cada ejecución que se lee: decisión, analytics, scoring,
        optimización, campo y ruteo, cada una por su identificador. La app no mezcla “lo más
        reciente” de cada motor: tú eliges y abres.
      </p>
      <Resultado consulta={opciones} que="las carteras y sus cortes">
        {(datos) => (
          <>
            <Carteras
              datos={datos}
              onCartera={(c) => {
                cambiar({ cartera: c, corte: undefined, pagina: undefined, ...vaciar() });
              }}
            />
            <Cortes
              datos={datos}
              pagina={pagina}
              seleccionado={contexto?.corte}
              onPagina={(p) => {
                cambiar({ pagina: String(p) });
              }}
            />
            {contexto !== null && datos.corte !== null ? (
              <EjecucionesDelCorte
                datos={datos}
                contexto={contexto}
                onEjecucion={(clave, valor) => {
                  cambiar({ [clave]: valor });
                }}
              />
            ) : null}
          </>
        )}
      </Resultado>
    </>
  );
}

function vaciar(): Record<ClaveDeEjecucion, undefined> {
  return {
    decision: undefined,
    analytics: undefined,
    scoring: undefined,
    optimizacion: undefined,
    campo: undefined,
    ruteo: undefined,
  };
}

function Carteras({
  datos,
  onCartera,
}: {
  readonly datos: Opciones;
  readonly onCartera: (cartera: string) => void;
}) {
  const id = useId();
  return (
    <section className="tarjeta" aria-labelledby={id}>
      <h2 id={id}>Cartera</h2>
      <p>
        Despacho <code>{datos.despacho_id}</code>. Se muestran los cortes de{" "}
        <strong>{datos.cartera_id}</strong>.
      </p>
      {datos.carteras.length > 1 ? (
        <fieldset className="opciones">
          <legend>Elegir la cartera</legend>
          {datos.carteras.map((c) => (
            <label key={c.cartera_id} className="opcion">
              <input
                type="radio"
                name="cartera"
                value={c.cartera_id}
                checked={c.cartera_id === datos.cartera_id}
                onChange={() => {
                  onCartera(c.cartera_id);
                }}
              />
              {c.cartera_id} · {formatearEntero(c.cortes)} cortes · último{" "}
              {formatearFecha(c.ultimo_corte)}
            </label>
          ))}
        </fieldset>
      ) : null}
    </section>
  );
}

function Cortes({
  datos,
  pagina,
  seleccionado,
  onPagina,
}: {
  readonly datos: Opciones;
  readonly pagina: number;
  readonly seleccionado: string | undefined;
  readonly onPagina: (pagina: number) => void;
}) {
  const id = useId();
  return (
    <section className="tarjeta" aria-labelledby={id}>
      <h2 id={id}>Cortes de {datos.cartera_id}</h2>
      {datos.cortes.length === 0 ? (
        <p>La cartera no tiene cortes canónicos.</p>
      ) : (
        <div className="tabla-contenedor" role="region" aria-label="Cortes" tabIndex={0}>
          <table className="tabla">
            <caption>Del más reciente al más antiguo</caption>
            <thead>
              <tr>
                <th scope="col">Fecha de corte</th>
                <th scope="col" className="tabla__numerica">
                  Cuentas
                </th>
                <th scope="col">Identificador</th>
                <th scope="col">Acción</th>
              </tr>
            </thead>
            <tbody>
              {datos.cortes.map((c) => (
                <tr
                  key={c.corte_id}
                  aria-current={c.corte_id === seleccionado ? "true" : undefined}
                >
                  <td>{formatearFecha(c.fecha_corte)}</td>
                  <td className="tabla__numerica">{formatearEntero(c.cuentas)}</td>
                  <td>
                    <Copiable valor={c.corte_id} etiqueta="el identificador del corte" />
                  </td>
                  <td>
                    {c.corte_id === seleccionado ? (
                      <strong>Elegido</strong>
                    ) : (
                      <Link
                        to={`/?cartera=${encodeURIComponent(datos.cartera_id)}&corte=${encodeURIComponent(c.corte_id)}`}
                      >
                        Usar este corte
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Paginacion
        total={datos.total}
        pagina={pagina}
        porPagina={POR_PAGINA}
        onPagina={onPagina}
        etiqueta="los cortes"
      />
    </section>
  );
}

function EjecucionesDelCorte({
  datos,
  contexto,
  onEjecucion,
}: {
  readonly datos: Opciones;
  readonly contexto: Contexto;
  readonly onEjecucion: (clave: ClaveDeEjecucion, valor: string | undefined) => void;
}) {
  const validado = useContextoValidado(contexto);
  const idSugerencias = useId();
  const idManual = useId();
  return (
    <>
      <section className="tarjeta" aria-labelledby={idSugerencias}>
        <h2 id={idSugerencias}>Contextos compatibles del corte</h2>
        {datos.sugerencias.length === 0 ? (
          <p>
            La API no encontró una cadena compatible para proponer en este corte. Puedes declarar
            cada ejecución a mano.
          </p>
        ) : (
          <ul className="sugerencias">
            {datos.sugerencias.map((s, i) => {
              const sugerido = contextoDeSugerencia(s);
              return (
                <li key={i} className="sugerencia">
                  <p className="sugerencia__motivo">{MOTIVOS[s.codigo]}</p>
                  <ul className="sugerencia__ejecuciones">
                    {EJECUCIONES.map((clave) => (
                      <li key={clave}>
                        <span>{NOMBRE_DE_EJECUCION[clave]}</span>{" "}
                        {sugerido[clave] !== undefined ? (
                          <code>{abreviar(sugerido[clave])}</code>
                        ) : (
                          <InsigniaEstado estado="NO_DECLARADA" />
                        )}
                      </li>
                    ))}
                  </ul>
                  <Link
                    className="boton boton--primario"
                    to={rutaConContexto("/resumen", sugerido)}
                  >
                    Abrir este contexto
                  </Link>{" "}
                  <Link className="boton" to={rutaConContexto("/", sugerido)}>
                    Ajustarlo
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      <section className="tarjeta" aria-labelledby={idManual}>
        <h2 id={idManual}>Declarar cada ejecución</h2>
        <p>Lo que no declares no se lee. Cada lista trae las más recientes del corte.</p>
        <div className="formulario formulario--rejilla">
          {EJECUCIONES.map((clave) => {
            const tipo = datos.ejecuciones.find((e) => e.tipo === TIPO_DE_EJECUCION[clave]);
            const elementos = tipo?.elementos ?? [];
            const valor = contexto[clave] ?? "";
            const conocida = elementos.some((e) => e.run_id === valor);
            return (
              <div className="campo" key={clave}>
                <label htmlFor={`ejecucion-${clave}`}>
                  {NOMBRE_DE_EJECUCION[clave]} ({formatearEntero(tipo?.total ?? 0)} en el corte)
                </label>
                <select
                  id={`ejecucion-${clave}`}
                  value={valor}
                  onChange={(evento) => {
                    onEjecucion(clave, evento.target.value || undefined);
                  }}
                >
                  <option value="">No declarar</option>
                  {valor !== "" && !conocida ? (
                    <option value={valor}>{abreviar(valor)} (no está en la lista)</option>
                  ) : null}
                  {elementos.map((e) => (
                    <option key={e.run_id} value={e.run_id}>
                      {descripcionDeEjecucion(e)}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
        <div className="validacion" aria-live="polite">
          {validado.isPending ? (
            <Cargando que="la validación del contexto" />
          ) : validado.isError ? (
            <ErrorDeCarga error={validado.error} />
          ) : validado.data.compatible ? (
            <Aviso>
              <InsigniaEstado estado="COMPATIBLE" /> Cada ejecución existe, es del corte y encadena
              con las demás.
            </Aviso>
          ) : (
            <ProblemasDeContexto validado={validado.data} />
          )}
        </div>
        <Link className="boton boton--primario" to={rutaConContexto("/resumen", contexto)}>
          Abrir el contexto
        </Link>
      </section>
    </>
  );
}
