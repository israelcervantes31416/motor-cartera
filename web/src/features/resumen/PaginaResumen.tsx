/**
 * El resumen ejecutivo-operacional de un contexto: cada bloque, de la ejecucion que el contexto
 * declara y tal como la publico (GET /app/v1/resumen). Un bloque sin ejecucion declarada dice que
 * no esta declarado; ninguno muestra un cero que nadie publico.
 */
import { useId, type ReactNode } from "react";

import { useCatalogo, useResumen } from "../../api/consultas";
import type { EtapaDeEmbudo, ResumenApp } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import {
  Aviso,
  AVISO_ANALYTICS,
  AVISO_ATRIBUCION,
  AVISO_OPTIMO,
  AVISO_PARADA,
  AVISO_PREDICCION,
} from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { InsigniaEstado, ValorConEstado, ValorOpcional } from "../../components/Estado";
import { Fuente, SinFuente } from "../../components/Fuente";
import {
  Barras,
  COLORES,
  decimo,
  Figura,
  Histograma,
  TablaHistograma,
} from "../../components/Graficas";
import { Indicador, Indicadores } from "../../components/Kpi";
import { comoNumero, comoRegistro, conteosDe, formatearSegunUnidad } from "../../lib/datos";
import {
  etiquetaDeCodigo,
  formatearCentavos,
  formatearDistancia,
  formatearDuracion,
  formatearEntero,
  formatearFecha,
  formatearInstante,
  formatearMonto,
  formatearPorcentaje,
  formatearPorcentajePreciso,
} from "../../lib/formato";

export function PaginaResumen() {
  return <ConContexto titulo="Resumen">{(listo) => <Resumen {...listo} />}</ConContexto>;
}

function Resumen({ contexto, validado }: ContextoListo) {
  const resumen = useResumen(contexto, true);
  const corte = validado.corte;
  return (
    <>
      <h1>Resumen</h1>
      <p className="entrada">
        {corte.cartera_id} · corte del {formatearFecha(corte.fecha_corte)} ·{" "}
        {formatearEntero(corte.cuentas)} cuentas. Cada bloque es de la ejecución que el contexto
        declara.
      </p>
      <Resultado consulta={resumen} que="el resumen">
        {(r) => (
          <div className="rejilla rejilla--dos">
            <BloqueAnalytics r={r} />
            <BloqueDecision r={r} />
            <BloquePredictivo r={r} />
            <BloqueOptimizacion r={r} />
            <BloqueCampo r={r} />
            <BloqueRuteo r={r} />
          </div>
        )}
      </Resultado>
    </>
  );
}

function Bloque({
  titulo,
  children,
  ancho = false,
}: {
  readonly titulo: string;
  readonly children: ReactNode;
  readonly ancho?: boolean;
}) {
  const id = useId();
  return (
    <section className={ancho ? "tarjeta tarjeta--ancha" : "tarjeta"} aria-labelledby={id}>
      <h2 id={id}>{titulo}</h2>
      {children}
    </section>
  );
}

const INDICADORES: Readonly<Record<string, string>> = {
  cuentas: "Cuentas de la cohorte",
  saldo_total: "Saldo inicial de la cohorte",
  recuperacion_neta: "Recuperación neta",
  recuperacion_sobre_saldo_inicial: "Recuperación / saldo inicial",
  cuentas_gestionadas: "Cuentas gestionadas",
  contactabilidad: "Contactadas / gestionadas",
  promesas_creadas: "Promesas creadas",
  tasa_cumplimiento_promesas: "Cumplimiento de promesas",
  asociacion_unica: "Pagos con asociación única",
  campo_candidatas: "Candidatas de campo",
  campo_asignadas: "Asignadas en campo",
  ruteo_paradas_objetivo: "Paradas a rutear",
  ruteo_programadas: "Paradas programadas",
};

const ETAPAS: Readonly<Record<string, string>> = {
  COHORTE: "Cohorte",
  GESTIONADAS: "Gestionadas",
  CONTACTADAS: "Contactadas",
  CON_PROMESA: "Con promesa",
  CANDIDATAS: "Candidatas de campo",
  ASIGNADAS: "Asignadas",
  PARADAS_PROGRAMADAS: "Paradas programadas",
};

function BloqueAnalytics({ r }: { readonly r: ResumenApp }) {
  const a = r.analytics;
  const catalogo = useCatalogo();
  const unidades = new Map((catalogo.data?.metricas ?? []).map((m) => [m.codigo, m.unidad]));
  return (
    <Bloque titulo="Cartera, recuperación y gestión" ancho>
      {a.estado_fuente !== "DISPONIBLE" ? (
        <SinFuente estado={a.estado_fuente} resultado={a.resultado} nombre="analytics/v1" />
      ) : (
        <>
          <Fuente nombre="analytics/v1" runId={a.run_id} estado={a.estado ?? "EXITOSA"}>
            {a.as_of !== null && a.as_of !== undefined ? `as_of ${formatearFecha(a.as_of)}` : ""}
            {a.known_at !== null && a.known_at !== undefined
              ? ` · known_at ${formatearInstante(a.known_at, a.zona_horaria ?? "UTC")}`
              : ""}
            {a.version_catalogo ? ` · ${a.version_catalogo}` : ""}
          </Fuente>
          <Indicadores etiqueta="Indicadores de analytics/v1">
            {(a.indicadores ?? []).map((i) => (
              <Indicador
                key={i.nombre}
                etiqueta={INDICADORES[i.nombre] ?? etiquetaDeCodigo(i.nombre)}
              >
                <ValorConEstado
                  estado={i.estado_valor}
                  valor={i.valor}
                  formatear={(v) => formatearSegunUnidad(unidades.get(i.metrica) ?? "", v)}
                />
              </Indicador>
            ))}
          </Indicadores>
          <div className="rejilla rejilla--dos">
            {(a.embudos ?? []).map((embudo) => {
              const etapas = embudo.etapas;
              // Solo las etapas publicadas con valor: una no disponible no es una barra en cero.
              const barras = etapas.flatMap((e) =>
                e.estado_valor === "DISPONIBLE" && e.cuentas !== null
                  ? [{ categoria: ETAPAS[e.etapa] ?? etiquetaDeCodigo(e.etapa), valor: e.cuentas }]
                  : [],
              );
              return (
                <Figura
                  key={embudo.embudo}
                  titulo={`Embudo ${embudo.embudo === "CONTACTO" ? "de contacto" : "de campo"}`}
                  descripcion="Cuentas distintas por etapa, anidadas, del horizonte de analytics/v1. Un pago no es una etapa."
                  datos={<TablaEmbudo etapas={etapas} />}
                >
                  {barras.length > 0 ? (
                    <Barras
                      titulo={`Embudo ${embudo.embudo}`}
                      barras={barras}
                      formatear={formatearEntero}
                      colores={COLORES.ordinal.slice(1)}
                    />
                  ) : (
                    <p className="sin-fuente">Ninguna etapa disponible: no se grafica.</p>
                  )}
                </Figura>
              );
            })}
          </div>
          <Aviso>{AVISO_ANALYTICS}</Aviso>
          <Aviso>{AVISO_ATRIBUCION}</Aviso>
        </>
      )}
    </Bloque>
  );
}

function TablaEmbudo({ etapas }: { readonly etapas: readonly EtapaDeEmbudo[] }) {
  return (
    <table className="tabla">
      <caption>Etapas del embudo</caption>
      <thead>
        <tr>
          <th scope="col">Etapa</th>
          <th scope="col">Estado</th>
          <th scope="col" className="tabla__numerica">
            Cuentas
          </th>
          <th scope="col" className="tabla__numerica">
            De la etapa anterior
          </th>
          <th scope="col" className="tabla__numerica">
            De la cohorte
          </th>
        </tr>
      </thead>
      <tbody>
        {etapas.map((e) => (
          <tr key={e.etapa}>
            <th scope="row">{ETAPAS[e.etapa] ?? etiquetaDeCodigo(e.etapa)}</th>
            <td>
              <InsigniaEstado estado={e.estado_valor} />
            </td>
            <td className="tabla__numerica">
              <ValorConEstado
                estado={e.estado_valor}
                valor={e.cuentas}
                formatear={formatearEntero}
              />
            </td>
            <td className="tabla__numerica">
              <ValorOpcional valor={e.pct_etapa_anterior} formatear={formatearPorcentaje} />
            </td>
            <td className="tabla__numerica">
              <ValorOpcional valor={e.pct_cohorte} formatear={formatearPorcentaje} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const ORDEN_PRIORIDAD = ["MUY_ALTA", "ALTA", "MEDIA", "BAJA"];

function BloqueDecision({ r }: { readonly r: ResumenApp }) {
  const d = r.decision;
  const distribucion = comoRegistro(d.distribucion);
  const prioridades = conteosDe(distribucion?.["prioridad_final"]).sort(
    (a, b) => ORDEN_PRIORIDAD.indexOf(a.clave) - ORDEN_PRIORIDAD.indexOf(b.clave),
  );
  const canales = conteosDe(distribucion?.["canal_recomendado"]);
  return (
    <Bloque titulo="Decisión (decision/v2)">
      {d.estado_fuente !== "DISPONIBLE" ? (
        <SinFuente estado={d.estado_fuente} resultado={d.resultado} nombre="decision/v2" />
      ) : (
        <>
          <Fuente nombre="decision/v2" runId={d.run_id} estado={d.estado ?? "EXITOSA"}>
            {d.as_of ? `as_of ${formatearFecha(d.as_of)}` : ""}
            {d.version_politica ? ` · ${d.version_politica}` : ""}
          </Fuente>
          <Indicadores etiqueta="Decisión">
            <Indicador etiqueta="Cuentas decididas">
              <ValorOpcional valor={d.cuentas_decididas} formatear={formatearEntero} />
            </Indicador>
          </Indicadores>
          <TablaDeConteos titulo="Por prioridad final" filas={prioridades} />
          <TablaDeConteos titulo="Por canal recomendado" filas={canales} />
          <p className="nota">
            Un motor de reglas explicable: una prioridad no es una probabilidad.
          </p>
        </>
      )}
    </Bloque>
  );
}

function TablaDeConteos({
  titulo,
  filas,
}: {
  readonly titulo: string;
  readonly filas: readonly { clave: string; valor: number }[];
}) {
  return (
    <table className="tabla tabla--compacta">
      <caption>{titulo}</caption>
      <thead>
        <tr>
          <th scope="col">Valor</th>
          <th scope="col" className="tabla__numerica">
            Cuentas
          </th>
        </tr>
      </thead>
      <tbody>
        {filas.length === 0 ? (
          <tr>
            <td colSpan={2}>Sin distribución publicada.</td>
          </tr>
        ) : (
          filas.map((f) => (
            <tr key={f.clave}>
              <th scope="row">{etiquetaDeCodigo(f.clave)}</th>
              <td className="tabla__numerica">{formatearEntero(f.valor)}</td>
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}

function BloquePredictivo({ r }: { readonly r: ResumenApp }) {
  const p = r.predictivo;
  const d = p.distribucion;
  const recuperacion = d?.histogramas.find((h) => h.salida === "p_recuperacion_positiva_30d");
  const drift = comoRegistro(p.drift_resumen);
  return (
    <Bloque titulo="Predictivo (predictive/model-v1)">
      {p.estado_fuente !== "DISPONIBLE" || d === null || d === undefined ? (
        <SinFuente estado={p.estado_fuente} resultado={p.resultado} nombre="el scoring" />
      ) : (
        <>
          <Fuente nombre="scoring" runId={p.run_id} estado={p.estado ?? "EXITOSA"}>
            {p.version_modelo ?? ""}
            {p.as_of ? ` · as_of ${formatearFecha(p.as_of)}` : ""}
            {p.known_at ? ` · known_at ${formatearInstante(p.known_at)}` : ""}
          </Fuente>
          <Aviso tono="aviso">{AVISO_PREDICCION}</Aviso>
          <Indicadores etiqueta="Predicciones publicadas">
            <Indicador etiqueta="Predicciones publicadas">
              {formatearEntero(d.publicadas)}
            </Indicador>
            <Indicador etiqueta="Con imputación">{formatearEntero(d.con_imputacion)}</Indicador>
            <Indicador etiqueta="p recuperación positiva 30 d (media)">
              <ValorOpcional
                valor={d.medias["p_recuperacion_positiva_30d"]}
                formatear={formatearPorcentaje}
              />
            </Indicador>
            <Indicador etiqueta="p contacto 14 d (media)">
              <ValorOpcional valor={d.medias["p_contacto_14d"]} formatear={formatearPorcentaje} />
            </Indicador>
            <Indicador etiqueta="p promesa 30 d (media)">
              <ValorOpcional valor={d.medias["p_promesa_30d"]} formatear={formatearPorcentaje} />
            </Indicador>
            <Indicador
              etiqueta="Recuperación positiva esperada 30 d (suma)"
              detalle="Positiva, no neta: no es dinero que se vaya a recuperar."
            >
              <ValorOpcional
                valor={d.suma_recuperacion_positiva_esperada_30d}
                formatear={formatearMonto}
              />
            </Indicador>
          </Indicadores>
          {recuperacion !== undefined ? (
            <Figura
              titulo="Distribución de p recuperación positiva 30 d"
              descripcion={`Cuántas de las ${formatearEntero(d.publicadas)} predicciones caen en cada décimo de [0, 1], contado en la base.`}
              datos={<TablaHistograma cuentas={recuperacion.cuentas} />}
            >
              <Histograma
                titulo="p recuperación positiva 30 d"
                columnas={recuperacion.cuentas.map((n, i) => ({ categoria: decimo(i), valor: n }))}
                formatear={formatearEntero}
              />
            </Figura>
          ) : null}
          {drift !== null ? (
            <TablaDeConteos
              titulo="Drift de las features por severidad"
              filas={conteosDe(drift["feature"])}
            />
          ) : null}
        </>
      )}
    </Bloque>
  );
}

function BloqueOptimizacion({ r }: { readonly r: ResumenApp }) {
  const o = r.optimizacion;
  const porCanal = comoRegistro(o.por_canal);
  const canales = porCanal === null ? [] : Object.entries(porCanal);
  return (
    <Bloque titulo="Optimización (optimization/v1)">
      {o.estado_fuente === "NO_DECLARADA" ? (
        <SinFuente estado="NO_DECLARADA" nombre="la optimización" />
      ) : (
        <>
          <Fuente nombre="optimization/v1" runId={o.run_id} estado={o.estado ?? "EXITOSA"}>
            {o.fecha_operacion ? `operación ${formatearFecha(o.fecha_operacion)}` : ""}
            {o.resultado ? ` · ${o.resultado}` : ""}
          </Fuente>
          <Aviso tono="aviso">{AVISO_OPTIMO}</Aviso>
          {o.solver_status ? (
            <p>
              Solver: <InsigniaEstado estado={o.solver_status} />
            </p>
          ) : null}
          {o.estado_fuente !== "DISPONIBLE" ? (
            <SinFuente estado={o.estado_fuente} resultado={o.resultado} nombre="La optimización" />
          ) : (
            <>
              <Indicadores etiqueta="Plan óptimo">
                <Indicador etiqueta="Cuentas asignadas">
                  <ValorOpcional valor={o.cuentas_asignadas} formatear={formatearEntero} />
                </Indicador>
                <Indicador etiqueta="Elegibles">
                  <ValorOpcional valor={o.cuentas_elegibles} formatear={formatearEntero} />
                </Indicador>
                <Indicador etiqueta="Universo">
                  <ValorOpcional valor={o.cuentas_universo} formatear={formatearEntero} />
                </Indicador>
                <Indicador
                  etiqueta="Valor de oportunidad del plan"
                  detalle="Recuperación positiva esperada predicha: no dinero a recuperar."
                >
                  <ValorOpcional
                    valor={o.objetivo_primario_centavos}
                    formatear={formatearCentavos}
                  />
                </Indicador>
                <Indicador etiqueta="Gap relativo">
                  <ValorOpcional valor={o.mip_gap} formatear={formatearPorcentajePreciso} />
                </Indicador>
                <Indicador etiqueta="Tiempo del solver">
                  <ValorOpcional
                    valor={o.solver_seconds}
                    formatear={(s) => formatearDuracion(Number(s))}
                  />
                </Indicador>
              </Indicadores>
              <table className="tabla tabla--compacta">
                <caption>Capacidad por canal</caption>
                <thead>
                  <tr>
                    <th scope="col">Canal</th>
                    <th scope="col" className="tabla__numerica">
                      Capacidad
                    </th>
                    <th scope="col" className="tabla__numerica">
                      Utilizada
                    </th>
                    <th scope="col" className="tabla__numerica">
                      Utilización
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {canales.map(([canal, valor]) => {
                    const c = comoRegistro(valor);
                    return (
                      <tr key={canal}>
                        <th scope="row">{canal}</th>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(c?.["capacidad"])}
                            formatear={formatearEntero}
                          />
                        </td>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(c?.["utilizada"])}
                            formatear={formatearEntero}
                          />
                        </td>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(c?.["utilizacion"])}
                            formatear={formatearPorcentaje}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </>
      )}
    </Bloque>
  );
}

function BloqueCampo({ r }: { readonly r: ResumenApp }) {
  const c = r.campo;
  return (
    <Bloque titulo="Campo (campo/v2)">
      {c.estado_fuente !== "DISPONIBLE" || !c.conteos || !c.capacidad ? (
        <SinFuente estado={c.estado_fuente} resultado={c.resultado} nombre="el plan de campo" />
      ) : (
        <>
          <Fuente nombre="campo/v2" runId={c.run_id} estado={c.estado ?? "EXITOSA"}>
            {c.fecha_operacion ? `operación ${formatearFecha(c.fecha_operacion)}` : ""}
          </Fuente>
          <Indicadores etiqueta="Plan de campo">
            <Indicador etiqueta="Candidatas">
              {formatearEntero(c.conteos.candidatas_campo)}
            </Indicador>
            <Indicador etiqueta="Asignadas">{formatearEntero(c.conteos.asignadas)}</Indicador>
            <Indicador etiqueta="Sin capacidad">
              {formatearEntero(c.conteos.sin_capacidad)}
            </Indicador>
            <Indicador etiqueta="Sin gestor">{formatearEntero(c.conteos.sin_gestor)}</Indicador>
            <Indicador etiqueta="Revisión territorial">
              {formatearEntero(c.conteos.revision_territorial)}
            </Indicador>
            <Indicador etiqueta="Rebalanceadas">
              {formatearEntero(c.conteos.rebalanceadas)}
            </Indicador>
            <Indicador
              etiqueta="Utilización de la capacidad"
              detalle={`${formatearEntero(c.capacidad.capacidad_utilizada)} de ${formatearEntero(c.capacidad.capacidad_total)} cupos declarados`}
            >
              <ValorOpcional valor={c.capacidad.utilizacion} formatear={formatearPorcentaje} />
            </Indicador>
          </Indicadores>
          <p className="nota">Capacidad declarada, no factibilidad vial.</p>
        </>
      )}
    </Bloque>
  );
}

function BloqueRuteo({ r }: { readonly r: ResumenApp }) {
  const ru = r.ruteo;
  return (
    <Bloque titulo="Ruteo (ruteo/v2)">
      {ru.estado_fuente !== "DISPONIBLE" || !ru.conteos || !ru.totales ? (
        <SinFuente estado={ru.estado_fuente} resultado={ru.resultado} nombre="el ruteo" />
      ) : (
        <>
          <Fuente nombre="ruteo/v2" runId={ru.run_id} estado={ru.estado ?? "EXITOSA"}>
            {ru.fecha_operacion ? `operación ${formatearFecha(ru.fecha_operacion)}` : ""}
          </Fuente>
          <Indicadores etiqueta="Ruteo">
            <Indicador etiqueta="Rutas completas">
              {formatearEntero(ru.conteos.rutas_completas)}
            </Indicador>
            <Indicador etiqueta="Rutas parciales">
              {formatearEntero(ru.conteos.rutas_parciales)}
            </Indicador>
            <Indicador etiqueta="Paradas programadas">
              {formatearEntero(ru.conteos.programadas)}
            </Indicador>
            <Indicador etiqueta="Sin tiempo">{formatearEntero(ru.conteos.sin_tiempo)}</Indicador>
            <Indicador etiqueta="No enrutables">
              {formatearEntero(ru.conteos.no_enrutables)}
            </Indicador>
            <Indicador etiqueta="Con base no enrutable">
              {formatearEntero(ru.conteos.no_enrutables_base)}
            </Indicador>
            <Indicador etiqueta="Distancia total">
              {formatearDistancia(ru.totales.distancia_total_m)}
            </Indicador>
            <Indicador etiqueta="Duración vial">
              {formatearDuracion(ru.totales.duracion_vial_total_s)}
            </Indicador>
          </Indicadores>
          <Aviso>{AVISO_PARADA}</Aviso>
        </>
      )}
    </Bloque>
  );
}
