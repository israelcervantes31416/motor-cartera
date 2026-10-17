/**
 * Campo: el plan de campo/v2 del contexto (sus conteos, su capacidad y su distribucion por base) y
 * la carga de cada gestor, leidos de sus propias rutas. Capacidad declarada, no factibilidad vial:
 * sin mapas, sin coordenadas y sin celdas.
 */
import { createColumnHelper } from "@tanstack/react-table";
import { useSearchParams } from "react-router";

import { useCargas, usePlanCampo } from "../../api/consultas";
import type { PaginaCargas } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { Fuente } from "../../components/Fuente";
import { Ficha, Indicador, Indicadores } from "../../components/Kpi";
import { Paginacion, TablaDatos } from "../../components/Tabla";
import { comoNumero, comoRegistro } from "../../lib/datos";
import {
  etiquetaDeCodigo,
  formatearEntero,
  formatearFecha,
  formatearPorcentaje,
} from "../../lib/formato";

export function PaginaCampo() {
  return (
    <ConContexto titulo="Campo" requiere={["campo"]}>
      {(listo) => <Campo {...listo} />}
    </ConContexto>
  );
}

const columnaCarga = createColumnHelper<PaginaCargas["elementos"][number]>();

function Campo({ contexto }: ContextoListo) {
  const id = contexto.campo;
  const plan = usePlanCampo(id);
  const [busqueda, setBusqueda] = useSearchParams();
  const pagina = Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1);
  const cargas = useCargas(id, { pagina, por_pagina: 50 });
  return (
    <>
      <h1>Campo</h1>
      <p className="entrada">
        Qué trabajo de campo cabe en la fecha, bajo la capacidad declarada de cada gestor: capacidad
        declarada, no factibilidad vial. Los gestores son códigos sintéticos.
      </p>
      <Resultado consulta={plan} que="el plan de campo">
        {(p) => {
          const distribucion = comoRegistro(p.distribucion);
          const porBase = Object.entries(comoRegistro(distribucion?.["por_base"]) ?? {});
          return (
            <>
              <Fuente nombre="campo/v2" runId={p.campo_run_id} estado={p.estado}>
                {`operación ${formatearFecha(p.fecha_operacion)} · ${p.version_politica} · ${p.configuracion.nombre} (${p.configuracion.zona_horaria})`}
              </Fuente>
              <Indicadores etiqueta="El plan de campo">
                <Indicador etiqueta="Candidatas">
                  {formatearEntero(p.conteos.candidatas_campo)}
                </Indicador>
                <Indicador etiqueta="Asignadas">{formatearEntero(p.conteos.asignadas)}</Indicador>
                <Indicador etiqueta="Sin capacidad">
                  {formatearEntero(p.conteos.sin_capacidad)}
                </Indicador>
                <Indicador etiqueta="Sin gestor disponible">
                  {formatearEntero(p.conteos.sin_gestor)}
                </Indicador>
                <Indicador etiqueta="Revisión territorial">
                  {formatearEntero(p.conteos.revision_territorial)}
                </Indicador>
                <Indicador etiqueta="Rebalanceadas">
                  {formatearEntero(p.conteos.rebalanceadas)}
                </Indicador>
                <Indicador
                  etiqueta="Utilización"
                  detalle={`${formatearEntero(p.capacidad.capacidad_utilizada)} de ${formatearEntero(p.capacidad.capacidad_total)} cupos`}
                >
                  <ValorOpcional valor={p.capacidad.utilizacion} formatear={formatearPorcentaje} />
                </Indicador>
              </Indicadores>
              <Ficha
                etiqueta="Entradas del plan"
                filas={[
                  [
                    "Decisión",
                    <Copiable key="d" valor={p.decision_run_id} etiqueta="la decisión" />,
                  ],
                  [
                    "Territorial",
                    <Copiable key="t" valor={p.territorial_run_id} etiqueta="la territorial" />,
                  ],
                  [
                    "Configuración",
                    <Copiable
                      key="c"
                      valor={p.configuracion.configuracion_campo_version_id}
                      etiqueta="la configuración"
                    />,
                  ],
                  [
                    "Hash de la configuración",
                    <Copiable
                      key="h"
                      valor={p.configuracion.hash_configuracion}
                      etiqueta="el hash"
                      largo={12}
                    />,
                  ],
                  [
                    "Firma",
                    <Copiable key="f" valor={p.firma_inputs} etiqueta="la firma" largo={12} />,
                  ],
                ]}
              />
              {porBase.length > 0 ? (
                <div className="tabla-contenedor" role="region" aria-label="Por base" tabIndex={0}>
                  <table className="tabla tabla--compacta">
                    <caption>Por base operativa, como lo publicó el plan</caption>
                    <thead>
                      <tr>
                        <th scope="col">Base</th>
                        <th scope="col" className="tabla__numerica">
                          Candidatas
                        </th>
                        <th scope="col" className="tabla__numerica">
                          Asignadas
                        </th>
                        <th scope="col" className="tabla__numerica">
                          Capacidad
                        </th>
                        <th scope="col" className="tabla__numerica">
                          Utilización
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {porBase.map(([base, v]) => {
                        const r = comoRegistro(v);
                        return (
                          <tr key={base}>
                            <th scope="row">{base}</th>
                            <td className="tabla__numerica">
                              <ValorOpcional
                                valor={comoNumero(r?.["candidatas"])}
                                formatear={formatearEntero}
                              />
                            </td>
                            <td className="tabla__numerica">
                              <ValorOpcional
                                valor={comoNumero(r?.["asignadas"])}
                                formatear={formatearEntero}
                              />
                            </td>
                            <td className="tabla__numerica">
                              <ValorOpcional
                                valor={comoNumero(r?.["capacidad"])}
                                formatear={formatearEntero}
                              />
                            </td>
                            <td className="tabla__numerica">
                              <ValorOpcional
                                valor={comoNumero(r?.["utilizacion"])}
                                formatear={formatearPorcentaje}
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          );
        }}
      </Resultado>
      <section className="tarjeta" aria-labelledby="cargas">
        <h2 id="cargas">Carga de cada gestor</h2>
        <Resultado consulta={cargas} que="las cargas">
          {(c) => (
            <>
              <TablaDatos
                titulo="Gestores de la configuración, en el orden de su código"
                filas={c.elementos}
                claveDeFila={(x) => x.gestor.gestor_id}
                columnas={[
                  columnaCarga.display({
                    id: "gestor",
                    header: "Gestor",
                    cell: ({ row }) => <code>{row.original.gestor.codigo}</code>,
                  }),
                  columnaCarga.display({
                    id: "base",
                    header: "Base",
                    cell: ({ row }) => row.original.base.codigo,
                  }),
                  columnaCarga.display({
                    id: "jornada",
                    header: "Jornada",
                    cell: ({ row }) => <InsigniaEstado estado={row.original.estado_jornada} />,
                  }),
                  columnaCarga.display({
                    id: "capacidad",
                    header: "Capacidad",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearEntero(row.original.capacidad),
                  }),
                  columnaCarga.display({
                    id: "asignadas",
                    header: "Asignadas",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearEntero(row.original.asignadas),
                  }),
                  columnaCarga.display({
                    id: "utilizacion",
                    header: "Utilización",
                    meta: { numerica: true },
                    cell: ({ row }) => (
                      <ValorOpcional
                        valor={row.original.utilizacion}
                        formatear={formatearPorcentaje}
                      />
                    ),
                  }),
                  columnaCarga.display({
                    id: "rebalanceadas",
                    header: "Rebalanceadas",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearEntero(row.original.rebalanceadas),
                  }),
                  columnaCarga.display({
                    id: "ventanas",
                    header: "Ventanas",
                    cell: ({ row }) =>
                      row.original.ventanas.map((v) => `${v.inicio}–${v.fin}`).join(", ") ||
                      etiquetaDeCodigo("SIN_VENTANAS"),
                  }),
                ]}
              />
              <Paginacion
                total={c.total}
                pagina={c.pagina}
                porPagina={c.por_pagina}
                etiqueta="los gestores"
                onPagina={(n) => {
                  const nueva = new URLSearchParams(busqueda);
                  nueva.set("pagina", String(n));
                  setBusqueda(nueva);
                }}
              />
            </>
          )}
        </Resultado>
      </section>
    </>
  );
}
