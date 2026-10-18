/**
 * Las pestanas de la Cuenta 360. Las que leen listas (historia, pagos, gestiones) o explicaciones
 * (decision, prediccion, plan) las piden al abrirse, paginadas en el servidor; las demas muestran
 * lo que ya trajo la cuenta. Ninguna muestra una coordenada, un CLIENTE_UNICO ni un actor_ref.
 */
import { createColumnHelper } from "@tanstack/react-table";
import { useId, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";

import {
  useDecisionDeCuenta,
  useExplicacionOptimizacion,
  useExplicacionPredictiva,
  useGestionesDeCuenta,
  useHistoriaDeCuenta,
  useLineaDeTiempo,
  usePagosDeCuenta,
} from "../../api/consultas";
import type {
  ContextoValidado,
  Cuenta360,
  ElementoDeLinea,
  GestionesDeCuenta,
  PagosDeCuenta,
} from "../../api/tipos";
import {
  Aviso,
  AVISO_ATRIBUCION,
  AVISO_OPTIMO,
  AVISO_PARADA,
  AVISO_PREDICCION,
} from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { SinFuente } from "../../components/Fuente";
import { Barras, Figura } from "../../components/Graficas";
import { Ficha } from "../../components/Kpi";
import { Paginacion, TablaDatos } from "../../components/Tabla";
import { comoRegistro, textoDe } from "../../lib/datos";
import { rutaConContexto, type Contexto } from "../../lib/contexto";
import {
  abreviar,
  etiquetaDeCodigo,
  formatearCentavos,
  formatearDecimal,
  formatearDistancia,
  formatearDuracion,
  formatearEntero,
  formatearFecha,
  formatearInstante,
  formatearMonto,
  formatearPorcentaje,
} from "../../lib/formato";

const POR_PAGINA = 25;

/** El known_at contra el que se marca un dato registrado tarde: el de analytics, si se declaro;
 * si no, el de la decision o el del scoring. */
export function relojDelContexto(
  validado: ContextoValidado,
): { known_at: string; zona: string; de: string } | null {
  for (const tipo of ["ANALYTICS", "DECISION_V2", "SCORING"]) {
    const e = validado.ejecuciones.find((x) => x.tipo === tipo);
    if (e?.known_at) return { known_at: e.known_at, zona: e.zona_horaria ?? "UTC", de: tipo };
  }
  return null;
}

function usePaginaDeUrl(llave: string): [number, (p: number) => void] {
  const [busqueda, setBusqueda] = useSearchParams();
  const pagina = Math.max(1, Number(busqueda.get(llave) ?? "1") || 1);
  return [
    pagina,
    (p: number) => {
      const nueva = new URLSearchParams(busqueda);
      if (p <= 1) nueva.delete(llave);
      else nueva.set(llave, String(p));
      setBusqueda(nueva);
    },
  ];
}

function Seccion({ titulo, children }: { readonly titulo: string; readonly children: ReactNode }) {
  const id = useId();
  return (
    <section className="tarjeta tarjeta--ancha" aria-labelledby={id}>
      <h2 id={id}>{titulo}</h2>
      {children}
    </section>
  );
}

function Tardio({
  registradoEn,
  reloj,
}: {
  readonly registradoEn: string;
  readonly reloj: { known_at: string } | null;
}) {
  if (reloj === null || new Date(registradoEn) <= new Date(reloj.known_at)) return null;
  return (
    <span
      className="insignia insignia--aviso"
      title="Se registró después del known_at del contexto"
    >
      <span aria-hidden="true">⏱ </span>Registrado después del known_at del contexto
    </span>
  );
}

// --- historia ---------------------------------------------------------------------------------------

export function PestanaHistoria({
  cuenta,
  validado,
}: {
  readonly cuenta: Cuenta360;
  readonly validado: ContextoValidado;
}) {
  const [pagina, setPagina] = usePaginaDeUrl("pagina");
  const [paginaCortes, setPaginaCortes] = usePaginaDeUrl("pagina_cortes");
  const linea = useLineaDeTiempo(cuenta.cuenta_id, { pagina, por_pagina: POR_PAGINA });
  const historia = useHistoriaDeCuenta(cuenta.cuenta_id, {
    pagina: paginaCortes,
    por_pagina: POR_PAGINA,
  });
  const reloj = relojDelContexto(validado);
  const zona = reloj?.zona ?? "UTC";
  return (
    <>
      <Seccion titulo="Línea de tiempo">
        <p className="nota">
          Por su momento de negocio (ocurrido_en). Un evento registrado tarde aparece donde ocurrió,
          con su registrado_en;{" "}
          {reloj !== null
            ? `se marca el que se registró después del known_at del contexto (${formatearInstante(reloj.known_at, zona)}).`
            : "el contexto no declara un known_at contra el que compararlo."}
        </p>
        <Resultado consulta={linea} que="la línea de tiempo">
          {(datos) =>
            datos.elementos.length === 0 ? (
              <p className="vacio">La cuenta no tiene eventos, observaciones ni movimientos.</p>
            ) : (
              <>
                <ol className="linea">
                  {datos.elementos.map((e, i) => (
                    <li key={i} className="linea__elemento">
                      <ElementoDeLaLinea elemento={e} zona={zona} reloj={reloj} />
                    </li>
                  ))}
                </ol>
                <Paginacion
                  total={datos.total}
                  pagina={datos.pagina}
                  porPagina={datos.por_pagina}
                  onPagina={setPagina}
                  etiqueta="la línea de tiempo"
                />
              </>
            )
          }
        </Resultado>
      </Seccion>
      <Seccion titulo="Snapshots por corte">
        <Resultado consulta={historia} que="los snapshots">
          {(datos) => (
            <>
              <TablaDatos
                titulo="Un snapshot por corte en que se observó la cuenta"
                filas={datos.elementos}
                claveDeFila={(s) => s.corte_id}
                columnas={[
                  columnaHistoria.display({
                    id: "fecha",
                    header: "Corte",
                    cell: ({ row }) => formatearFecha(row.original.fecha_corte),
                  }),
                  columnaHistoria.display({
                    id: "saldo",
                    header: "Saldo total",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearMonto(row.original.saldo_total),
                  }),
                  columnaHistoria.display({
                    id: "atraso",
                    header: "Días de atraso",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearEntero(row.original.dias_atraso),
                  }),
                  columnaHistoria.display({
                    id: "delta_saldo",
                    header: "Δ saldo",
                    meta: { numerica: true },
                    cell: ({ row }) => (
                      <ValorOpcional
                        valor={row.original.delta_saldo_total}
                        formatear={formatearMonto}
                      />
                    ),
                  }),
                  columnaHistoria.display({
                    id: "continuidad",
                    header: "Continuidad",
                    cell: ({ row }) =>
                      row.original.continuo_desde_anterior
                        ? "Continuo"
                        : row.original.cortes_ausentes_desde_anterior === null
                          ? "Primera observación"
                          : `${formatearEntero(row.original.cortes_ausentes_desde_anterior)} cortes ausente`,
                  }),
                  columnaHistoria.display({
                    id: "evidencia",
                    header: "Evidencia",
                    cell: ({ row }) => (
                      <Copiable
                        valor={row.original.dataset_id}
                        etiqueta="el dataset del snapshot"
                      />
                    ),
                  }),
                ]}
              />
              <Paginacion
                total={datos.total}
                pagina={datos.pagina}
                porPagina={datos.por_pagina}
                onPagina={setPaginaCortes}
                etiqueta="los snapshots"
              />
            </>
          )}
        </Resultado>
      </Seccion>
    </>
  );
}

const columnaHistoria =
  createColumnHelper<
    NonNullable<ReturnType<typeof useHistoriaDeCuenta>["data"]>["elementos"][number]
  >();

function ElementoDeLaLinea({
  elemento,
  zona,
  reloj,
}: {
  readonly elemento: ElementoDeLinea;
  readonly zona: string;
  readonly reloj: { known_at: string } | null;
}) {
  const op = elemento.operacional;
  const fuente = elemento.fuente_corte;
  const eco = elemento.economico;
  return (
    <>
      <p className="linea__cabeza">
        <InsigniaEstado estado={elemento.dominio} />{" "}
        <strong>{etiquetaDeCodigo(elemento.tipo)}</strong>{" "}
        <time dateTime={elemento.instante}>{formatearInstante(elemento.instante, zona)}</time>
      </p>
      {op ? (
        <p className="linea__detalle">
          Ocurrido {formatearInstante(op.evento.ocurrido_en, zona)} · registrado{" "}
          {formatearInstante(op.evento.registrado_en, zona)}{" "}
          <Tardio registradoEn={op.evento.registrado_en} reloj={reloj} />
          {op.canal ? ` · ${op.canal}` : ""}
          {op.nivel_contacto ? ` · ${etiquetaDeCodigo(op.nivel_contacto)}` : ""}
          {op.resultado ? ` · ${etiquetaDeCodigo(op.resultado)}` : ""}
          {op.monto_prometido ? ` · prometido ${formatearMonto(op.monto_prometido)}` : ""}
        </p>
      ) : null}
      {fuente ? (
        <p className="linea__detalle">
          Lo que dijo el corte del {formatearFecha(fuente.fecha_corte)}: promesa{" "}
          {fuente.estatus_promesa_pago ?? "—"}, plan {fuente.estatus_plan ?? "—"}. Una observación
          de la fuente, no un evento.
        </p>
      ) : null}
      {eco ? (
        <p className="linea__detalle">
          {etiquetaDeCodigo(eco.tipo_movimiento)} de {formatearMonto(eco.monto_reportado)} ·{" "}
          {etiquetaDeCodigo(eco.estado_conciliacion)} · interpretación {eco.version_motor}
        </p>
      ) : null}
    </>
  );
}

// --- pagos ------------------------------------------------------------------------------------------

type PagoAtribuido = PagosDeCuenta["pagina"]["elementos"][number];
const columnaPago = createColumnHelper<PagoAtribuido>();

export function PestanaPagos({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const [pagina, setPagina] = usePaginaDeUrl("pagina");
  const pagos = usePagosDeCuenta(cuenta.cuenta_id, { pagina, por_pagina: POR_PAGINA });
  return (
    <Seccion titulo="Pagos: movimientos económicos canónicos">
      <Aviso>{AVISO_ATRIBUCION}</Aviso>
      <p className="nota">
        Los movimientos que interpreta el motor de pagos: un pago observado (una fila de la fuente)
        no es una recuperación canónica, y esta no es la contabilidad del acreedor.
      </p>
      <Resultado consulta={pagos} que="los pagos">
        {(datos) => (
          <>
            <Ficha
              etiqueta="Resumen de la interpretación vigente"
              filas={[
                ["Observaciones", formatearEntero(datos.resumen.observaciones)],
                ["Movimientos canónicos", formatearEntero(datos.resumen.movimientos_canonicos)],
                ["Duplicados exactos", formatearEntero(datos.resumen.duplicados_exactos)],
                ["Reversos", formatearEntero(datos.resumen.reversos)],
                ["Posibles reversos", formatearEntero(datos.resumen.posibles_reversos)],
                ["Pagos anulados", formatearEntero(datos.resumen.pagos_anulados)],
                [
                  "Recuperación bruta interpretada",
                  formatearMonto(datos.resumen.recuperacion_bruta_interpretada),
                ],
                [
                  "Recuperación neta interpretada",
                  formatearMonto(datos.resumen.recuperacion_neta_interpretada),
                ],
              ]}
            />
            <TablaDatos
              titulo="Pagos con su atribución vigente"
              filas={datos.pagina.elementos}
              claveDeFila={(p) => p.movimiento.movimiento_id}
              vacio="La cuenta no tiene pagos interpretados."
              columnas={[
                columnaPago.display({
                  id: "fecha",
                  header: "Recepción",
                  cell: ({ row }) => formatearInstante(row.original.movimiento.fecha_recepcion),
                }),
                columnaPago.display({
                  id: "tipo",
                  header: "Tipo",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.movimiento.tipo_movimiento),
                }),
                columnaPago.display({
                  id: "monto",
                  header: "Monto",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearMonto(row.original.movimiento.monto_reportado),
                }),
                columnaPago.display({
                  id: "conciliacion",
                  header: "Conciliación",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.movimiento.estado_conciliacion),
                }),
                columnaPago.display({
                  id: "atribucion",
                  header: "Atribución",
                  cell: ({ row }) =>
                    row.original.atribucion === null ? (
                      <InsigniaEstado estado="NO_DISPONIBLE" />
                    ) : (
                      <InsigniaEstado estado={row.original.atribucion.clasificacion} />
                    ),
                }),
                columnaPago.display({
                  id: "movimiento",
                  header: "Movimiento",
                  cell: ({ row }) => (
                    <Copiable
                      valor={row.original.movimiento.movimiento_id}
                      etiqueta="el movimiento"
                    />
                  ),
                }),
              ]}
            />
            <Paginacion
              total={datos.pagina.total}
              pagina={datos.pagina.pagina}
              porPagina={datos.pagina.por_pagina}
              onPagina={setPagina}
              etiqueta="los pagos"
            />
          </>
        )}
      </Resultado>
    </Seccion>
  );
}

// --- gestiones --------------------------------------------------------------------------------------

type Gestion = GestionesDeCuenta["pagina"]["elementos"][number];
const columnaGestion = createColumnHelper<Gestion>();

export function PestanaGestiones({
  cuenta,
  validado,
}: {
  readonly cuenta: Cuenta360;
  readonly validado: ContextoValidado;
}) {
  const [pagina, setPagina] = usePaginaDeUrl("pagina");
  const gestiones = useGestionesDeCuenta(cuenta.cuenta_id, { pagina, por_pagina: POR_PAGINA });
  const reloj = relojDelContexto(validado);
  const zona = reloj?.zona ?? "UTC";
  return (
    <Seccion titulo="Gestiones">
      <Resultado consulta={gestiones} que="las gestiones">
        {(datos) => (
          <>
            <Ficha
              etiqueta="Resumen del lifecycle"
              filas={[
                ["Gestiones", formatearEntero(datos.resumen.gestiones)],
                ["Anuladas", formatearEntero(datos.resumen.gestiones_anuladas)],
                [
                  "Promesas",
                  `${formatearEntero(datos.resumen.promesas)} (${formatearEntero(datos.resumen.promesas_vigentes)} vigentes)`,
                ],
                [
                  "Convenios",
                  `${formatearEntero(datos.resumen.convenios)} (${formatearEntero(datos.resumen.convenios_vigentes)} vigentes)`,
                ],
                ["Visitas", formatearEntero(datos.resumen.visitas)],
              ]}
            />
            <TablaDatos
              titulo="Gestiones por su momento de negocio"
              filas={datos.pagina.elementos}
              claveDeFila={(g) => g.gestion_id}
              vacio="La cuenta no tiene gestiones registradas."
              columnas={[
                columnaGestion.display({
                  id: "ocurrido",
                  header: "Ocurrido",
                  cell: ({ row }) => formatearInstante(row.original.ocurrido_en, zona),
                }),
                columnaGestion.display({
                  id: "registrado",
                  header: "Registrado",
                  cell: ({ row }) => (
                    <>
                      {formatearInstante(row.original.registrado_en, zona)}{" "}
                      <Tardio registradoEn={row.original.registrado_en} reloj={reloj} />
                    </>
                  ),
                }),
                columnaGestion.display({
                  id: "canal",
                  header: "Canal",
                  cell: ({ row }) => row.original.canal,
                }),
                columnaGestion.display({
                  id: "contacto",
                  header: "Contacto",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.nivel_contacto),
                }),
                columnaGestion.display({
                  id: "resultado",
                  header: "Resultado",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.resultado),
                }),
                columnaGestion.display({
                  id: "estado",
                  header: "Estado",
                  cell: ({ row }) => <InsigniaEstado estado={row.original.estado} />,
                }),
              ]}
            />
            <Paginacion
              total={datos.pagina.total}
              pagina={datos.pagina.pagina}
              porPagina={datos.pagina.por_pagina}
              onPagina={setPagina}
              etiqueta="las gestiones"
            />
            <Aviso>{AVISO_ATRIBUCION}</Aviso>
          </>
        )}
      </Resultado>
    </Seccion>
  );
}

// --- decision ---------------------------------------------------------------------------------------

export function PestanaDecision({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const seccion = cuenta.decision;
  const decision = useDecisionDeCuenta(
    cuenta.cuenta_id,
    seccion.estado === "DISPONIBLE" && seccion.run_id !== null ? seccion.run_id : undefined,
  );
  if (seccion.estado !== "DISPONIBLE") {
    return (
      <Seccion titulo="Decisión (decision/v2)">
        <SinFuente estado={seccion.estado} nombre="una decision/v2 con esta cuenta" />
      </Seccion>
    );
  }
  return (
    <Seccion titulo="Decisión (decision/v2)">
      <p className="nota">
        Un motor de reglas deterministas y explicables: la prioridad no es una probabilidad ni una
        predicción.
      </p>
      <Resultado consulta={decision} que="la decisión explicada">
        {(d) => (
          <>
            <Ficha
              etiqueta="La decisión"
              filas={[
                ["Prioridad base", <InsigniaEstado key="b" estado={d.prioridad_base} />],
                ["Prioridad final", <InsigniaEstado key="f" estado={d.prioridad_final} />],
                ["Ajuste total", formatearEntero(d.ajuste_total)],
                ["Canal recomendado", d.canal_recomendado],
                ["as_of", formatearFecha(d.as_of)],
                ["known_at", formatearInstante(d.known_at)],
                ["Política", `${d.versiones.politica} · ${abreviar(d.hash_politica, 12)}`],
                [
                  "Hash de la decisión",
                  <Copiable
                    key="h"
                    valor={d.decision_hash}
                    etiqueta="el hash de la decisión"
                    largo={12}
                  />,
                ],
                [
                  "Verificación",
                  d.verificacion.decision_hash &&
                  d.verificacion.feature_hash &&
                  d.verificacion.motivos
                    ? "La política reproduce la decisión publicada"
                    : "La verificación no reproduce la decisión publicada",
                ],
              ]}
            />
            <table className="tabla">
              <caption>Cada regla de la política, evaluada sobre las features publicadas</caption>
              <thead>
                <tr>
                  <th scope="col">Regla</th>
                  <th scope="col">Aplicó</th>
                  <th scope="col" className="tabla__numerica">
                    Efecto
                  </th>
                  <th scope="col">Condiciones</th>
                </tr>
              </thead>
              <tbody>
                {d.reglas.map((r) => (
                  <tr key={r.codigo}>
                    <th scope="row">{etiquetaDeCodigo(r.codigo)}</th>
                    <td>{r.activa ? "Sí" : "No"}</td>
                    <td className="tabla__numerica">{formatearEntero(r.efecto)}</td>
                    <td>
                      <ul className="lista-compacta">
                        {r.condiciones.map((c, i) => (
                          <li key={i}>
                            <code>{c.feature}</code> {c.operador} {c.umbral}: {c.valor ?? "NULL"} (
                            {c.cumple ? "cumple" : "no cumple"})
                          </li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <details className="detalles">
              <summary>Features publicadas ({Object.keys(d.features).length})</summary>
              <Ficha
                etiqueta="Features de la decisión"
                filas={Object.entries(d.features).map(([nombre, valor]) => [
                  nombre,
                  valor === null ? (
                    <InsigniaEstado key={nombre} estado="SIN_VALOR" />
                  ) : (
                    String(valor)
                  ),
                ])}
              />
            </details>
          </>
        )}
      </Resultado>
    </Seccion>
  );
}

// --- geografia, campo y ruteo -----------------------------------------------------------------------

export function PestanaGeografia({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const g = cuenta.geografia;
  return (
    <Seccion titulo="Geografía y territorial">
      <p className="nota">
        La geografía y la base de la territorial/v2 que leyó el plan de campo del contexto. Sin
        coordenadas: el estado, el municipio y la base.
      </p>
      {g.estado !== "DISPONIBLE" ? (
        <SinFuente estado={g.estado} nombre="una territorial/v2 con esta cuenta" />
      ) : (
        <Ficha
          etiqueta="Geografía"
          filas={[
            [
              "Estado de la geografía",
              <InsigniaEstado key="e" estado={g.estado_geografia ?? "SIN_VALOR"} />,
            ],
            [
              "Municipio (referencia oficial)",
              g.municipio ? `${g.municipio} (${g.cvegeo ?? "—"})` : "—",
            ],
            ["Municipio de la fuente", g.municipio_fuente ?? "—"],
            [
              "Comparación con la fuente",
              g.comparacion_municipio ? etiquetaDeCodigo(g.comparacion_municipio) : "—",
            ],
            [
              "Asignación territorial",
              g.estado_asignacion ? etiquetaDeCodigo(g.estado_asignacion) : "—",
            ],
            [
              "Base",
              g.codigo_base
                ? `${g.codigo_base}${g.nombre_base ? ` · ${g.nombre_base}` : ""}`
                : "Sin base",
            ],
            [
              "Distancia a la base",
              <ValorOpcional key="d" valor={g.distancia_base_m} formatear={formatearDistancia} />,
            ],
            [
              "territorial/v2",
              g.territorial_run_id ? (
                <Copiable key="t" valor={g.territorial_run_id} etiqueta="la territorial/v2" />
              ) : (
                "—"
              ),
            ],
            [
              "geografia/v1",
              g.geografia_run_id ? (
                <Copiable key="g" valor={g.geografia_run_id} etiqueta="la geografía" />
              ) : (
                "—"
              ),
            ],
          ]}
        />
      )}
    </Seccion>
  );
}

export function PestanaCampo({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const c = cuenta.campo;
  const a = c.asignacion;
  const carga = c.carga_gestor;
  return (
    <Seccion titulo="Campo (campo/v2)">
      <p className="nota">
        Capacidad declarada, no factibilidad vial. El gestor es un código sintético.
      </p>
      {c.estado !== "DISPONIBLE" || !a ? (
        <SinFuente estado={c.estado} nombre="un plan de campo con esta cuenta" />
      ) : (
        <>
          <Ficha
            etiqueta="Asignación de campo"
            filas={[
              ["Estado", <InsigniaEstado key="e" estado={a.estado} />],
              ["Gestor", a.gestor?.codigo ?? "Sin gestor"],
              ["Base", a.base?.codigo ?? "Sin base"],
              ["Prioridad", etiquetaDeCodigo(a.prioridad)],
              [
                "Lugar en la cola de su base",
                <ValorOpcional key="o" valor={a.orden_prioridad} formatear={formatearEntero} />,
              ],
              [
                "Rebalanceada",
                a.es_rebalanceada ? "Sí: la recibió otro gestor que el preferido" : "No",
              ],
              ["Fecha de operación", formatearFecha(a.fecha_operacion)],
              [
                "Motivos",
                a.motivos
                  .map((m) => textoDe(comoRegistro(m)?.["codigo"]))
                  .filter(Boolean)
                  .join(", ") || "—",
              ],
            ]}
          />
          {carga ? (
            <Ficha
              etiqueta="Carga del gestor en el plan"
              filas={[
                ["Jornada", etiquetaDeCodigo(carga.estado_jornada)],
                ["Capacidad efectiva", formatearEntero(carga.capacidad)],
                ["Asignadas", formatearEntero(carga.asignadas)],
                [
                  "Utilización",
                  <ValorOpcional
                    key="u"
                    valor={carga.utilizacion}
                    formatear={formatearPorcentaje}
                  />,
                ],
              ]}
            />
          ) : null}
        </>
      )}
    </Seccion>
  );
}

export function PestanaRuteo({
  cuenta,
  contexto,
}: {
  readonly cuenta: Cuenta360;
  readonly contexto: Contexto;
}) {
  const r = cuenta.ruteo;
  const p = r.parada;
  return (
    <Seccion titulo="Ruteo (ruteo/v2)">
      <Aviso>{AVISO_PARADA}</Aviso>
      {r.estado !== "DISPONIBLE" || !p ? (
        <SinFuente estado={r.estado} nombre="un ruteo con esta cuenta" />
      ) : (
        <>
          <Ficha
            etiqueta="Parada de la cuenta"
            filas={[
              ["Estado", <InsigniaEstado key="e" estado={p.estado} />],
              [
                "Orden de visita",
                <ValorOpcional key="o" valor={p.orden_visita} formatear={formatearEntero} />,
              ],
              [
                "Llegada estimada",
                p.llegada_estimada ? formatearInstante(p.llegada_estimada, p.zona_horaria) : "—",
              ],
              [
                "Servicio",
                p.inicio_servicio && p.fin_servicio
                  ? `${formatearInstante(p.inicio_servicio, p.zona_horaria)} a ${formatearInstante(p.fin_servicio, p.zona_horaria)}`
                  : "—",
              ],
              [
                "Desde la parada anterior",
                <ValorOpcional
                  key="d"
                  valor={p.distancia_desde_anterior_m}
                  formatear={formatearDistancia}
                />,
              ],
              [
                "Duración desde la anterior",
                <ValorOpcional
                  key="t"
                  valor={p.duracion_desde_anterior_s}
                  formatear={formatearDuracion}
                />,
              ],
              ["Motivo", p.motivo ? etiquetaDeCodigo(p.motivo) : "—"],
              ["Gestor y base", `${p.gestor} · ${p.base}`],
              ["Estado de la ruta", etiquetaDeCodigo(p.estado_ruta)],
              ["Zona horaria", p.zona_horaria],
            ]}
          />
          <Link
            className="boton"
            to={rutaConContexto(`/ruteo/rutas/${encodeURIComponent(p.ruta_id)}`, contexto)}
          >
            Ver la ruta completa
          </Link>
        </>
      )}
    </Seccion>
  );
}

// --- predictivo y optimizacion ---------------------------------------------------------------------

export function PestanaPredictivo({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const s = cuenta.predictivo;
  const p = s.prediccion;
  const explicacion = useExplicacionPredictiva(
    s.estado === "DISPONIBLE" && p ? p.scoring_run_id : undefined,
    cuenta.cuenta_id,
  );
  return (
    <Seccion titulo="Predictivo (predictive/model-v1)">
      <Aviso tono="aviso">{AVISO_PREDICCION}</Aviso>
      {s.estado !== "DISPONIBLE" || !p ? (
        <SinFuente estado={s.estado} nombre="un scoring con esta cuenta" />
      ) : (
        <>
          <Ficha
            etiqueta="La predicción"
            filas={[
              ["p recuperación positiva 30 d", formatearPorcentaje(p.p_recuperacion_positiva_30d)],
              ["p contacto 14 d", formatearPorcentaje(p.p_contacto_14d)],
              ["p promesa 30 d", formatearPorcentaje(p.p_promesa_30d)],
              ["Riesgo sin recuperación 30 d", formatearPorcentaje(p.riesgo_sin_recuperacion_30d)],
              [
                "Recuperación positiva esperada 30 d",
                formatearMonto(p.recuperacion_positiva_esperada_30d),
              ],
              ["Estado", <InsigniaEstado key="e" estado={p.estado} />],
              [
                "Versión del modelo",
                <Copiable key="m" valor={p.modelo_version_id} etiqueta="la versión del modelo" />,
              ],
              ["as_of", formatearFecha(p.as_of)],
              ["known_at", formatearInstante(p.known_at)],
              [
                "feature_hash",
                <Copiable key="f" valor={p.feature_hash} etiqueta="el feature_hash" largo={12} />,
              ],
            ]}
          />
          <h3>Contribuciones al score</h3>
          <p className="nota">
            Cada contribución es una asociación del modelo con su resultado, en la escala de su
            predictor lineal: no una causa.
          </p>
          <Resultado consulta={explicacion} que="la explicación">
            {(e) => (
              <div className="rejilla rejilla--dos">
                {e.componentes.map((c) => (
                  <Figura
                    key={c.target}
                    titulo={`${c.salida}: contribución al score`}
                    descripcion={`Las mayores contribuciones al predictor lineal (${c.escala}); suman ${formatearDecimal(c.suma_de_contribuciones)} con el intercepto ${formatearDecimal(c.intercepto)}.`}
                    datos={<TablaContribuciones componente={c} />}
                  >
                    <Barras
                      titulo={`${c.salida}: contribución al score`}
                      barras={[...c.hacia_arriba, ...c.hacia_abajo].map((x) => ({
                        categoria: x.feature,
                        valor: x.contribucion,
                      }))}
                      formatear={(v) => formatearDecimal(v)}
                    />
                  </Figura>
                ))}
              </div>
            )}
          </Resultado>
        </>
      )}
    </Seccion>
  );
}

function TablaContribuciones({
  componente,
}: {
  readonly componente: NonNullable<
    ReturnType<typeof useExplicacionPredictiva>["data"]
  >["componentes"][number];
}) {
  return (
    <table className="tabla tabla--compacta">
      <caption>Contribución al score de cada feature</caption>
      <thead>
        <tr>
          <th scope="col">Feature</th>
          <th scope="col">Sentido</th>
          <th scope="col" className="tabla__numerica">
            Contribución al score
          </th>
        </tr>
      </thead>
      <tbody>
        {componente.hacia_arriba.map((x) => (
          <tr key={`a-${x.feature}`}>
            <th scope="row">{x.feature}</th>
            <td>Hacia arriba</td>
            <td className="tabla__numerica">{formatearDecimal(x.contribucion)}</td>
          </tr>
        ))}
        {componente.hacia_abajo.map((x) => (
          <tr key={`b-${x.feature}`}>
            <th scope="row">{x.feature}</th>
            <td>Hacia abajo</td>
            <td className="tabla__numerica">{formatearDecimal(x.contribucion)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PestanaOptimizacion({ cuenta }: { readonly cuenta: Cuenta360 }) {
  const s = cuenta.optimizacion;
  const r = s.resultado;
  const explicacion = useExplicacionOptimizacion(
    s.estado === "DISPONIBLE" && r && s.run_id !== null ? s.run_id : undefined,
    cuenta.cuenta_id,
  );
  return (
    <Seccion titulo="Optimización (optimization/v1)">
      <Aviso tono="aviso">{AVISO_OPTIMO}</Aviso>
      {s.estado !== "DISPONIBLE" || !r ? (
        <SinFuente estado={s.estado} nombre="una optimización con esta cuenta" />
      ) : (
        <>
          <Ficha
            etiqueta="Lo que concluyó el plan"
            filas={[
              ["Estado", <InsigniaEstado key="e" estado={r.estado} />],
              ["Motivo (un hecho)", etiquetaDeCodigo(r.motivo)],
              ["Canal de la decisión", r.canal_decision],
              ["Canal asignado", r.canal_asignado ?? "—"],
              ["Pool", r.pool ?? "—"],
              ["Valor de oportunidad", formatearCentavos(r.valor_oportunidad_centavos)],
              [
                "Unidades de recurso",
                <ValorOpcional key="u" valor={r.unidades_recurso} formatear={formatearEntero} />,
              ],
              ["p contacto 14 d (solo desempate)", formatearPorcentaje(r.p_contacto_14d)],
              ["Fecha de operación", s.fecha_operacion ? formatearFecha(s.fecha_operacion) : "—"],
            ]}
          />
          <Resultado consulta={explicacion} que="la explicación del plan">
            {(e) => (
              <>
                <p>{e.explicacion}</p>
                {e.desempate ? <p className="nota">{e.desempate}</p> : null}
                <Ficha
                  etiqueta="Hechos de su elegibilidad"
                  filas={[
                    ["Canales admisibles", e.canales_admisibles.join(", ") || "Ninguno"],
                    ["Pools que la alcanzaban", e.pools_elegibles.join(", ") || "Ninguno"],
                    ...Object.entries(e.hechos).map(([k, v]): [string, ReactNode] => [
                      etiquetaDeCodigo(k),
                      textoDe(v, "—"),
                    ]),
                  ]}
                />
              </>
            )}
          </Resultado>
          <ul className="lista-compacta">
            {s.limitaciones.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
    </Seccion>
  );
}

// --- evidencia --------------------------------------------------------------------------------------

export function PestanaEvidencia({
  cuenta,
  validado,
}: {
  readonly cuenta: Cuenta360;
  readonly validado: ContextoValidado;
}) {
  const s = cuenta.snapshot;
  return (
    <Seccion titulo="Evidencia">
      <p className="nota">De dónde sale cada valor de esta cuenta en el contexto.</p>
      <Ficha
        etiqueta="Evidencia del snapshot y de las ejecuciones"
        filas={[
          ["Corte", <Copiable key="c" valor={cuenta.corte_id} etiqueta="el corte" />],
          [
            "Firma del corte",
            <Copiable
              key="f"
              valor={validado.corte.firma_contenido}
              etiqueta="la firma del corte"
              largo={12}
            />,
          ],
          s
            ? [
                "Dataset del snapshot",
                <Copiable key="d" valor={s.dataset_id} etiqueta="el dataset" />,
              ]
            : null,
          s
            ? [
                "Fila y hoja de la fuente",
                `${formatearEntero(s.source_row)} · ${s.source_sheet ?? "—"}`,
              ]
            : null,
          cuenta.decision.decision_hash
            ? [
                "Hash de la decisión",
                <Copiable
                  key="h"
                  valor={cuenta.decision.decision_hash}
                  etiqueta="el hash de la decisión"
                  largo={12}
                />,
              ]
            : null,
          cuenta.predictivo.prediccion
            ? [
                "feature_hash de la predicción",
                <Copiable
                  key="p"
                  valor={cuenta.predictivo.prediccion.feature_hash}
                  etiqueta="el feature_hash"
                  largo={12}
                />,
              ]
            : null,
          ...validado.ejecuciones.map((e): [string, ReactNode] => [
            `${etiquetaDeCodigo(e.tipo)}${e.firma_inputs ? " (firma)" : ""}`,
            <span key={e.run_id}>
              <Copiable valor={e.run_id} etiqueta={`la ejecución ${e.tipo}`} />{" "}
              {e.firma_inputs ? (
                <Copiable valor={e.firma_inputs} etiqueta={`la firma de ${e.tipo}`} largo={12} />
              ) : null}
            </span>,
          ]),
        ]}
      />
    </Seccion>
  );
}
