/**
 * Ruteo: la ejecucion de ruteo/v2 del contexto (sus conteos, sus totales, su red y su politica) y la
 * ruta de cada gestor, paginadas en el servidor. Una heuristica, no una ruta optima: sin mapas, sin
 * polilineas y sin coordenadas; el orden, los tiempos y las distancias.
 */
import { createColumnHelper } from "@tanstack/react-table";
import { Link, useSearchParams } from "react-router";

import { useRutas, useRuteo } from "../../api/consultas";
import type { PaginaRutas } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_PARADA } from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado } from "../../components/Estado";
import { Fuente } from "../../components/Fuente";
import { Ficha, Indicador, Indicadores } from "../../components/Kpi";
import { Paginacion, TablaDatos } from "../../components/Tabla";
import { rutaConContexto } from "../../lib/contexto";
import {
  formatearDistancia,
  formatearDuracion,
  formatearEntero,
  formatearFecha,
} from "../../lib/formato";

export function PaginaRuteo() {
  return (
    <ConContexto titulo="Ruteo" requiere={["ruteo"]}>
      {(listo) => <Ruteo {...listo} />}
    </ConContexto>
  );
}

const columnaRuta = createColumnHelper<PaginaRutas["elementos"][number]>();

function Ruteo({ contexto }: ContextoListo) {
  const id = contexto.ruteo;
  const ruteo = useRuteo(id);
  const [busqueda, setBusqueda] = useSearchParams();
  const pagina = Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1);
  const rutas = useRutas(id, { pagina, por_pagina: 50 });
  return (
    <>
      <h1>Ruteo</h1>
      <Aviso>{AVISO_PARADA}</Aviso>
      <p className="entrada">
        La secuencia de visitas de cada gestor por la red vial, con ETA planificadas sin tráfico:
        una heurística, no una ruta óptima. Sin mapas ni coordenadas.
      </p>
      <Resultado consulta={ruteo} que="el ruteo">
        {(r) => (
          <>
            <Fuente nombre="ruteo/v2" runId={r.ruteo_run_id} estado={r.estado}>
              {`operación ${formatearFecha(r.fecha_operacion)} · ${r.version_politica} · red ${r.red.region} (${r.red.motor_ruteo} ${r.red.version_motor_ruteo}) · ${r.zona_horaria}`}
            </Fuente>
            <Indicadores etiqueta="El ruteo">
              <Indicador etiqueta="Gestores con ruta">
                {formatearEntero(r.conteos.gestores_objetivo)}
              </Indicador>
              <Indicador etiqueta="Rutas completas">
                {formatearEntero(r.conteos.rutas_completas)}
              </Indicador>
              <Indicador etiqueta="Rutas parciales">
                {formatearEntero(r.conteos.rutas_parciales)}
              </Indicador>
              <Indicador etiqueta="Paradas programadas">
                {formatearEntero(r.conteos.programadas)}
              </Indicador>
              <Indicador etiqueta="Sin tiempo">{formatearEntero(r.conteos.sin_tiempo)}</Indicador>
              <Indicador etiqueta="No enrutables">
                {formatearEntero(r.conteos.no_enrutables)}
              </Indicador>
              <Indicador etiqueta="Con base no enrutable">
                {formatearEntero(r.conteos.no_enrutables_base)}
              </Indicador>
              <Indicador etiqueta="Distancia total">
                {formatearDistancia(r.totales.distancia_total_m)}
              </Indicador>
              <Indicador etiqueta="Duración vial">
                {formatearDuracion(r.totales.duracion_vial_total_s)}
              </Indicador>
              <Indicador etiqueta="Servicio">
                {formatearDuracion(r.totales.duracion_servicio_total_s)}
              </Indicador>
              <Indicador etiqueta="Espera">
                {formatearDuracion(r.totales.duracion_espera_total_s)}
              </Indicador>
            </Indicadores>
            <Ficha
              etiqueta="Entradas y política"
              filas={[
                [
                  "Plan de campo",
                  <Copiable key="c" valor={r.campo_run_id} etiqueta="el plan de campo" />,
                ],
                [
                  "Red vial",
                  <Copiable key="r" valor={r.red.red_vial_version_id} etiqueta="la red vial" />,
                ],
                [
                  "Huella de la red",
                  <Copiable key="h" valor={r.red.huella} etiqueta="la huella" largo={12} />,
                ],
                [
                  "Hash de la política",
                  <Copiable key="p" valor={r.hash_politica} etiqueta="la política" largo={12} />,
                ],
                [
                  "Firma",
                  <Copiable key="f" valor={r.firma_inputs} etiqueta="la firma" largo={12} />,
                ],
                r.politica ? ["Algoritmo", r.politica.algoritmo] : null,
              ]}
            />
          </>
        )}
      </Resultado>
      <section className="tarjeta" aria-labelledby="rutas">
        <h2 id="rutas">Rutas</h2>
        <Resultado consulta={rutas} que="las rutas">
          {(p) => (
            <>
              <TablaDatos
                titulo="La ruta de cada gestor, en el orden de su código"
                filas={p.elementos}
                claveDeFila={(x) => x.ruta_id}
                columnas={[
                  columnaRuta.display({
                    id: "gestor",
                    header: "Gestor",
                    cell: ({ row }) => (
                      <Link
                        to={rutaConContexto(
                          `/ruteo/rutas/${encodeURIComponent(row.original.ruta_id)}`,
                          contexto,
                        )}
                      >
                        {row.original.gestor.codigo}
                      </Link>
                    ),
                  }),
                  columnaRuta.display({
                    id: "base",
                    header: "Base",
                    cell: ({ row }) => row.original.base.codigo,
                  }),
                  columnaRuta.display({
                    id: "estado",
                    header: "Estado",
                    cell: ({ row }) => <InsigniaEstado estado={row.original.estado_ruta} />,
                  }),
                  columnaRuta.display({
                    id: "paradas",
                    header: "Paradas",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearEntero(row.original.conteos.paradas_campo),
                  }),
                  columnaRuta.display({
                    id: "programadas",
                    header: "Programadas",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearEntero(row.original.conteos.programadas),
                  }),
                  columnaRuta.display({
                    id: "distancia",
                    header: "Distancia",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearDistancia(row.original.distancia_total_m),
                  }),
                  columnaRuta.display({
                    id: "duracion",
                    header: "Duración",
                    meta: { numerica: true },
                    cell: ({ row }) => formatearDuracion(row.original.duracion_total_s),
                  }),
                ]}
              />
              <Paginacion
                total={p.total}
                pagina={p.pagina}
                porPagina={p.por_pagina}
                etiqueta="las rutas"
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
