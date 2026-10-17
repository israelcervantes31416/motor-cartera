/**
 * Una ruta: su gestor, su base, su jornada y sus paradas en su orden de visita, con la llegada
 * estimada, el servicio y el tramo desde la anterior. Sin mapa y sin coordenadas; cada cuenta es su
 * identificador tecnico (GET /app/v1/ruteo/rutas/{ruta_id}/paradas, sin su CLIENTE_UNICO).
 */
import { createColumnHelper } from "@tanstack/react-table";
import { Link, useParams, useSearchParams } from "react-router";

import { useParadas, useRuta } from "../../api/consultas";
import type { PaginaParadas } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_PARADA } from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { Ficha } from "../../components/Kpi";
import { Paginacion, TablaDatos } from "../../components/Tabla";
import { esUuid, rutaConContexto } from "../../lib/contexto";
import {
  abreviar,
  etiquetaDeCodigo,
  formatearDistancia,
  formatearDuracion,
  formatearEntero,
  formatearFecha,
  formatearInstante,
} from "../../lib/formato";

export function PaginaRuta() {
  const { rutaId = "" } = useParams();
  return (
    <ConContexto titulo="Ruta" requiere={["ruteo"]}>
      {(listo) => <Ruta id={rutaId} {...listo} />}
    </ConContexto>
  );
}

const columnaParada = createColumnHelper<PaginaParadas["elementos"][number]>();

function Ruta({ id, contexto }: ContextoListo & { readonly id: string }) {
  const valido = esUuid(id);
  const ruta = useRuta(id, valido);
  const [busqueda, setBusqueda] = useSearchParams();
  const pagina = Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1);
  const paradas = useParadas(id, { pagina, por_pagina: 50 }, valido);
  const zona = ruta.data?.zona_horaria ?? "UTC";
  if (!valido) {
    return (
      <section className="tarjeta">
        <h1>Ruta</h1>
        <p>
          <code>{id}</code> no es un identificador de ruta.
        </p>
      </section>
    );
  }
  return (
    <>
      <h1>Ruta</h1>
      <Link className="boton" to={rutaConContexto("/ruteo", contexto)}>
        ← Todas las rutas
      </Link>
      <Aviso>{AVISO_PARADA}</Aviso>
      <Resultado consulta={ruta} que="la ruta">
        {(r) => (
          <section className="tarjeta" aria-labelledby="ruta-ficha">
            <h2 id="ruta-ficha">
              {r.gestor.codigo} · {r.base.codigo} <InsigniaEstado estado={r.estado_ruta} />
            </h2>
            {r.ruteo_run_id !== contexto.ruteo ? (
              <p className="alerta alerta--aviso">
                Esta ruta es de otro ruteo que el del contexto ({abreviar(r.ruteo_run_id)}).
              </p>
            ) : null}
            <Ficha
              etiqueta="La ruta"
              filas={[
                ["Fecha de operación", formatearFecha(r.fecha_operacion)],
                [
                  "Jornada",
                  r.jornada_inicio && r.jornada_fin
                    ? `${formatearInstante(r.jornada_inicio, r.zona_horaria)} a ${formatearInstante(r.jornada_fin, r.zona_horaria)}`
                    : "Sin jornada",
                ],
                [
                  "Salida de la base",
                  r.salida_base ? formatearInstante(r.salida_base, r.zona_horaria) : "—",
                ],
                [
                  "Regreso",
                  r.regreso?.llegada ? formatearInstante(r.regreso.llegada, r.zona_horaria) : "—",
                ],
                ["Paradas", formatearEntero(r.conteos.paradas_campo)],
                ["Programadas", formatearEntero(r.conteos.programadas)],
                ["Sin tiempo", formatearEntero(r.conteos.sin_tiempo)],
                ["No enrutables", formatearEntero(r.conteos.no_enrutables)],
                ["Distancia total", formatearDistancia(r.distancia_total_m)],
                [
                  "Traslado · servicio · espera",
                  `${formatearDuracion(r.duracion_traslado_s)} · ${formatearDuracion(r.duracion_servicio_s)} · ${formatearDuracion(r.duracion_espera_s)}`,
                ],
                ["Zona horaria", r.zona_horaria],
                ["Motivo de la base", r.motivo_base ? etiquetaDeCodigo(r.motivo_base) : "—"],
              ]}
            />
          </section>
        )}
      </Resultado>
      <section className="tarjeta" aria-labelledby="paradas">
        <h2 id="paradas">Paradas en orden de visita</h2>
        <Resultado consulta={paradas} que="las paradas">
          {(p) => (
            <>
              <TablaDatos
                titulo="Las programadas por su orden; después, las demás con su motivo"
                filas={p.elementos}
                claveDeFila={(x) => x.cuenta_id}
                columnas={[
                  columnaParada.display({
                    id: "orden",
                    header: "Orden",
                    meta: { numerica: true },
                    cell: ({ row }) => (
                      <ValorOpcional
                        valor={row.original.orden_visita}
                        formatear={formatearEntero}
                      />
                    ),
                  }),
                  columnaParada.display({
                    id: "cuenta",
                    header: "Cuenta",
                    cell: ({ row }) => (
                      <Link
                        to={rutaConContexto(
                          `/cuentas/${encodeURIComponent(row.original.cuenta_id)}/ruteo`,
                          contexto,
                        )}
                      >
                        <code>{abreviar(row.original.cuenta_id, 13)}</code>
                      </Link>
                    ),
                  }),
                  columnaParada.display({
                    id: "estado",
                    header: "Estado",
                    cell: ({ row }) => <InsigniaEstado estado={row.original.estado} />,
                  }),
                  columnaParada.display({
                    id: "eta",
                    header: "Llegada estimada",
                    cell: ({ row }) =>
                      row.original.llegada_estimada
                        ? formatearInstante(row.original.llegada_estimada, zona)
                        : "—",
                  }),
                  columnaParada.display({
                    id: "servicio",
                    header: "Servicio",
                    cell: ({ row }) =>
                      row.original.inicio_servicio && row.original.fin_servicio
                        ? `${formatearInstante(row.original.inicio_servicio, zona)} – ${formatearInstante(row.original.fin_servicio, zona)}`
                        : "—",
                  }),
                  columnaParada.display({
                    id: "tramo",
                    header: "Desde la anterior",
                    meta: { numerica: true },
                    cell: ({ row }) => (
                      <>
                        <ValorOpcional
                          valor={row.original.distancia_desde_anterior_m}
                          formatear={formatearDistancia}
                        />{" "}
                        <ValorOpcional
                          valor={row.original.duracion_desde_anterior_s}
                          formatear={formatearDuracion}
                        />
                      </>
                    ),
                  }),
                  columnaParada.display({
                    id: "motivo",
                    header: "Motivo",
                    cell: ({ row }) =>
                      row.original.motivo ? etiquetaDeCodigo(row.original.motivo) : "—",
                  }),
                ]}
              />
              <Paginacion
                total={p.total}
                pagina={p.pagina}
                porPagina={p.por_pagina}
                etiqueta="las paradas"
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
