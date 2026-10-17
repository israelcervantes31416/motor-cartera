/**
 * Analytics: la ejecucion de analytics/v1 del contexto, leida de sus propias rutas. La metrica y su
 * desglose se eligen de su catalogo (con su definicion, su formula, su unidad, su numerador, su
 * denominador, sus fuentes y sus limitaciones), y los agregados se filtran y se paginan en la base.
 *
 * Una celda SUPRIMIDO no se grafica ni se reconstruye: no hay "total menos el resto". Un periodo sin
 * valor es un hueco de la linea, no un cero.
 */
import { useId } from "react";
import { useSearchParams } from "react-router";

import {
  useAgregados,
  useCatalogo,
  useControles,
  useEjecucionAnalytics,
  useEmbudos,
} from "../../api/consultas";
import type { AgregadoAnalytics, MetricaAnalytics, PaginaAgregados } from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_ANALYTICS, AVISO_ATRIBUCION } from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { InsigniaEstado, ValorConEstado, ValorOpcional } from "../../components/Estado";
import { Fuente } from "../../components/Fuente";
import {
  Barras,
  COLORES,
  Composicion,
  Figura,
  MAXIMO_DE_CATEGORIAS,
  MAXIMO_DE_PARTES,
  SerieTemporal,
} from "../../components/Graficas";
import { Ficha } from "../../components/Kpi";
import { Paginacion } from "../../components/Tabla";
import { comoNumero, formatearSegunUnidad, NOMBRE_DE_UNIDAD } from "../../lib/datos";
import {
  etiquetaDeCodigo,
  formatearDecimal,
  formatearEntero,
  formatearFecha,
  formatearInstante,
  formatearPorcentaje,
} from "../../lib/formato";

const POR_PAGINA_CONJUNTO = 100;

export function PaginaAnalytics() {
  return (
    <ConContexto titulo="Analytics" requiere={["analytics"]}>
      {(listo) => <Analytics {...listo} />}
    </ConContexto>
  );
}

function Analytics({ contexto }: ContextoListo) {
  const id = contexto.analytics;
  const catalogo = useCatalogo();
  const ejecucion = useEjecucionAnalytics(id);
  return (
    <>
      <h1>Analytics</h1>
      <Aviso>{AVISO_ANALYTICS}</Aviso>
      <Resultado consulta={ejecucion} que="la ejecución de analytics">
        {(e) => (
          <>
            <Fuente nombre="analytics/v1" runId={e.analytics_run_id} estado={e.estado}>
              {`corte ${formatearFecha(e.fecha_corte)} · as_of ${formatearFecha(e.as_of)} · known_at ${formatearInstante(e.known_at, e.zona_horaria)} · ${e.zona_horaria} · granularidad ${etiquetaDeCodigo(e.granularidad)} · ${e.version_catalogo}`}
            </Fuente>
            {e.estado !== "EXITOSA" ? (
              <p className="alerta alerta--aviso">
                <InsigniaEstado estado={e.estado} /> La ejecución no publicó agregados
                {e.resultado ? ` (${e.resultado})` : ""}.
              </p>
            ) : (
              <Resultado consulta={catalogo} que="el catálogo de métricas">
                {(c) => <Exploracion id={e.analytics_run_id} metricas={c.metricas} />}
              </Resultado>
            )}
            <Controles id={e.analytics_run_id} />
          </>
        )}
      </Resultado>
    </>
  );
}

function etiquetaDeCelda(a: AgregadoAnalytics): string {
  const valores = Object.values(a.dimensiones);
  return valores.length === 0 ? "Total" : valores.join(" · ");
}

function valorGraficable(a: AgregadoAnalytics): number | null {
  return a.estado_valor === "DISPONIBLE" ? comoNumero(a.valor) : null;
}

function Exploracion({
  id,
  metricas,
}: {
  readonly id: string;
  readonly metricas: readonly MetricaAnalytics[];
}) {
  const [busqueda, setBusqueda] = useSearchParams();
  const pedida = busqueda.get("metrica");
  const metrica =
    metricas.find((m) => m.codigo === pedida) ??
    metricas.find((m) => m.codigo === "recuperacion.neta") ??
    metricas[0];
  const conjuntos = (metrica?.dimensiones_permitidas ?? [])
    .map((d) => d.conjunto)
    .filter((c) => c !== "TOTAL");
  const conjuntoPedido = busqueda.get("conjunto");
  const conjunto = conjuntos.find((c) => c === conjuntoPedido) ?? conjuntos[0];
  const paginaConjunto = Math.max(1, Number(busqueda.get("pagina_conjunto") ?? "1") || 1);
  const conPeriodo = metrica?.vistas.includes("PERIODO") ?? false;
  const serie = useAgregados(
    id,
    {
      ...(metrica ? { metrica: metrica.codigo } : {}),
      vista: "PERIODO",
      dimension_set: "TOTAL",
      por_pagina: 500,
    },
    metrica !== undefined && conPeriodo,
  );
  const categorias = useAgregados(
    id,
    {
      ...(metrica ? { metrica: metrica.codigo } : {}),
      vista: "ACUMULADA",
      ...(conjunto ? { dimension_set: conjunto } : {}),
      pagina: paginaConjunto,
      por_pagina: POR_PAGINA_CONJUNTO,
    },
    metrica !== undefined && conjunto !== undefined,
  );
  const idMetrica = useId();
  const idConjunto = useId();

  if (metrica === undefined) return <p>El catálogo no tiene métricas.</p>;
  const formatear = (v: number | string) => formatearSegunUnidad(metrica.unidad, v);
  const familias = [...new Set(metricas.map((m) => m.familia))];

  function cambiar(cambios: Record<string, string | undefined>) {
    const nueva = new URLSearchParams(busqueda);
    for (const [llave, valor] of Object.entries(cambios)) {
      if (valor === undefined) nueva.delete(llave);
      else nueva.set(llave, valor);
    }
    setBusqueda(nueva);
  }

  return (
    <>
      <form
        className="filtros"
        aria-label="Métrica y desglose"
        onSubmit={(evento) => {
          evento.preventDefault();
        }}
      >
        <div className="campo">
          <label htmlFor={idMetrica}>Métrica</label>
          <select
            id={idMetrica}
            value={metrica.codigo}
            onChange={(evento) => {
              cambiar({
                metrica: evento.target.value,
                conjunto: undefined,
                pagina_conjunto: undefined,
              });
            }}
          >
            {familias.map((f) => (
              <optgroup key={f} label={etiquetaDeCodigo(f)}>
                {metricas
                  .filter((m) => m.familia === f)
                  .map((m) => (
                    <option key={m.codigo} value={m.codigo}>
                      {m.nombre}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </div>
        {conjuntos.length > 0 ? (
          <div className="campo">
            <label htmlFor={idConjunto}>Desglose</label>
            <select
              id={idConjunto}
              value={conjunto}
              onChange={(evento) => {
                cambiar({ conjunto: evento.target.value, pagina_conjunto: undefined });
              }}
            >
              {conjuntos.map((c) => (
                <option key={c} value={c}>
                  {etiquetaDeCodigo(c)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </form>
      <Definicion metrica={metrica} />
      {conPeriodo ? (
        <Resultado consulta={serie} que="la serie de la métrica">
          {(datos) => <Serie datos={datos} metrica={metrica} formatear={formatear} />}
        </Resultado>
      ) : (
        <p className="nota">La métrica no se publica por periodo: no hay serie temporal.</p>
      )}
      {conjunto !== undefined ? (
        <Resultado consulta={categorias} que="el desglose">
          {(datos) => (
            <Categorias
              datos={datos}
              metrica={metrica}
              conjunto={conjunto}
              formatear={formatear}
              onPagina={(p) => {
                cambiar({ pagina_conjunto: p <= 1 ? undefined : String(p) });
              }}
            />
          )}
        </Resultado>
      ) : (
        <p className="nota">La métrica solo se publica en el TOTAL: no tiene desgloses.</p>
      )}
      <Embudos id={id} />
    </>
  );
}

function Definicion({ metrica }: { readonly metrica: MetricaAnalytics }) {
  return (
    <section className="tarjeta" aria-labelledby="definicion-metrica">
      <h2 id="definicion-metrica">{metrica.nombre}</h2>
      <p>{metrica.descripcion}</p>
      <Ficha
        etiqueta="Definición de la métrica"
        filas={[
          ["Código", <code key="c">{metrica.codigo}</code>],
          ["Unidad", NOMBRE_DE_UNIDAD[metrica.unidad] ?? metrica.unidad],
          ["Fórmula", metrica.formula],
          ["Numerador", metrica.numerador ?? "—"],
          ["Denominador", metrica.denominador ?? "—"],
          ["Población", metrica.poblacion],
          ["Regla temporal", metrica.regla_temporal],
          ["Fuentes requeridas", metrica.fuentes_requeridas.join(", ") || "—"],
          ["Cuándo no está disponible", metrica.nulo],
          ["Versión del catálogo", metrica.version],
        ]}
      />
      {metrica.limitaciones.length > 0 ? (
        <>
          <h3>Limitaciones</h3>
          <ul className="lista-compacta">
            {metrica.limitaciones.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      ) : null}
      {metrica.familia === "atribucion" ? <Aviso>{AVISO_ATRIBUCION}</Aviso> : null}
    </section>
  );
}

function TablaDeAgregados({
  filas,
  titulo,
  formatear,
  periodo,
}: {
  readonly filas: readonly AgregadoAnalytics[];
  readonly titulo: string;
  readonly formatear: (v: string) => string;
  readonly periodo: boolean;
}) {
  return (
    <div className="tabla-contenedor" role="region" aria-label={titulo} tabIndex={0}>
      <table className="tabla tabla--compacta">
        <caption>{titulo}</caption>
        <thead>
          <tr>
            <th scope="col">{periodo ? "Periodo" : "Celda"}</th>
            <th scope="col">Estado</th>
            <th scope="col" className="tabla__numerica">
              Valor
            </th>
            <th scope="col" className="tabla__numerica">
              Numerador
            </th>
            <th scope="col" className="tabla__numerica">
              Denominador
            </th>
            <th scope="col" className="tabla__numerica">
              Cuentas
            </th>
          </tr>
        </thead>
        <tbody>
          {filas.map((a) => (
            <tr key={`${a.dimension_hash}-${a.periodo_desde}`}>
              <th scope="row">
                {periodo
                  ? `${formatearFecha(a.periodo_desde)}${a.periodo_hasta !== a.periodo_desde ? ` – ${formatearFecha(a.periodo_hasta)}` : ""}${a.periodo_parcial ? " (parcial)" : ""}`
                  : etiquetaDeCelda(a)}
              </th>
              <td>
                <InsigniaEstado estado={a.estado_valor} />
              </td>
              <td className="tabla__numerica">
                <ValorConEstado estado={a.estado_valor} valor={a.valor} formatear={formatear} />
              </td>
              <td className="tabla__numerica">
                <ValorOpcional valor={a.numerador} formatear={formatearDecimal} />
              </td>
              <td className="tabla__numerica">
                <ValorOpcional valor={a.denominador} formatear={formatearDecimal} />
              </td>
              <td className="tabla__numerica">
                {a.estado_valor === "SUPRIMIDO" ? (
                  <InsigniaEstado estado="SUPRIMIDO" />
                ) : (
                  <ValorOpcional valor={a.poblacion_cuentas} formatear={formatearEntero} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Serie({
  datos,
  metrica,
  formatear,
}: {
  readonly datos: PaginaAgregados;
  readonly metrica: MetricaAnalytics;
  readonly formatear: (v: number | string) => string;
}) {
  const filas = datos.elementos;
  const huecos = filas.filter((a) => valorGraficable(a) === null).length;
  return (
    <Figura
      titulo={`${metrica.nombre}: serie por periodo`}
      descripcion={`${formatearEntero(filas.length)} periodos del horizonte, en el TOTAL.${huecos > 0 ? ` ${formatearEntero(huecos)} sin valor (no disponibles o suprimidos): huecos, no ceros.` : ""}`}
      datos={
        <TablaDeAgregados
          filas={filas}
          titulo="La serie, periodo por periodo"
          formatear={formatear}
          periodo
        />
      }
    >
      {filas.length === 0 ? (
        <p className="vacio">La ejecución no publicó periodos de esta métrica.</p>
      ) : (
        <SerieTemporal
          titulo={metrica.nombre}
          puntos={filas.map((a) => ({
            etiqueta: formatearFecha(a.periodo_desde),
            valor: valorGraficable(a),
          }))}
          formatear={formatear}
        />
      )}
    </Figura>
  );
}

function Categorias({
  datos,
  metrica,
  conjunto,
  formatear,
  onPagina,
}: {
  readonly datos: PaginaAgregados;
  readonly metrica: MetricaAnalytics;
  readonly conjunto: string;
  readonly formatear: (v: number | string) => string;
  readonly onPagina: (p: number) => void;
}) {
  const filas = datos.elementos;
  // Las celdas que se dibujan: solo las publicadas con valor. Una suprimida o no disponible no es
  // una barra ni una parte en cero, y no se reconstruye.
  const visibles = filas.flatMap((a) => {
    const valor = valorGraficable(a);
    return valor === null ? [] : [{ categoria: etiquetaDeCelda(a), valor }];
  });
  const suprimidas = filas.filter((a) => a.estado_valor === "SUPRIMIDO").length;
  const graficable = datos.total <= MAXIMO_DE_CATEGORIAS;
  const componible =
    graficable &&
    metrica.aditiva &&
    metrica.tipo === "VALOR" &&
    visibles.length >= 2 &&
    visibles.length <= MAXIMO_DE_PARTES &&
    visibles.every((v) => v.valor >= 0);
  const nota = [
    `${formatearEntero(datos.total)} celdas de ${etiquetaDeCodigo(conjunto)}.`,
    suprimidas > 0
      ? `${formatearEntero(suprimidas)} suprimidas: no se grafican ni se reconstruyen.`
      : "",
    graficable ? "" : `Con más de ${MAXIMO_DE_CATEGORIAS} celdas, solo la tabla, paginada.`,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <>
      <Figura
        titulo={`${metrica.nombre} por ${etiquetaDeCodigo(conjunto).toLowerCase()}`}
        descripcion={nota}
        datos={
          <>
            <TablaDeAgregados
              filas={filas}
              titulo={`Por ${etiquetaDeCodigo(conjunto).toLowerCase()}`}
              formatear={formatear}
              periodo={false}
            />
            <Paginacion
              total={datos.total}
              pagina={datos.pagina}
              porPagina={datos.por_pagina}
              onPagina={onPagina}
              etiqueta="las celdas"
            />
          </>
        }
      >
        {graficable && visibles.length > 0 ? (
          <Barras
            titulo={`${metrica.nombre} por ${etiquetaDeCodigo(conjunto)}`}
            barras={visibles}
            formatear={formatear}
          />
        ) : (
          <p className="vacio">Sin celdas graficables: ver los datos.</p>
        )}
      </Figura>
      {componible ? (
        <Figura
          titulo={`Composición de ${metrica.nombre.toLowerCase()}`}
          descripcion={`Cada celda visible de ${etiquetaDeCodigo(conjunto).toLowerCase()}, apilada con su valor publicado.${suprimidas > 0 ? " Las suprimidas no se muestran: la composición está incompleta." : ""}`}
          datos={
            <TablaDeAgregados
              filas={filas}
              titulo="Las partes de la composición"
              formatear={formatear}
              periodo={false}
            />
          }
        >
          <Composicion titulo={metrica.nombre} partes={visibles} formatear={formatear} />
        </Figura>
      ) : null}
    </>
  );
}

const ETAPAS: Readonly<Record<string, string>> = {
  COHORTE: "Cohorte",
  GESTIONADAS: "Gestionadas",
  CONTACTADAS: "Contactadas",
  CON_PROMESA: "Con promesa",
  CANDIDATAS: "Candidatas de campo",
  ASIGNADAS: "Asignadas",
  PARADAS_PROGRAMADAS: "Paradas programadas",
};

function Embudos({ id }: { readonly id: string }) {
  const embudos = useEmbudos(id);
  return (
    <Resultado consulta={embudos} que="los embudos">
      {(datos) => (
        <div className="rejilla rejilla--dos">
          {datos.embudos.map((e) => {
            // Solo las etapas publicadas con valor: una no disponible no es una barra en cero.
            const barras = e.etapas.flatMap((x) =>
              x.estado_valor === "DISPONIBLE" && x.cuentas !== null
                ? [{ categoria: ETAPAS[x.etapa] ?? etiquetaDeCodigo(x.etapa), valor: x.cuentas }]
                : [],
            );
            return (
              <Figura
                key={e.embudo}
                titulo={`Embudo ${e.embudo === "CONTACTO" ? "de contacto" : "de campo"}`}
                descripcion="Conjuntos de cuentas anidados del horizonte. Un pago no es una etapa, y una parada programada no es una visita."
                datos={
                  <table className="tabla tabla--compacta">
                    <caption>Etapas</caption>
                    <thead>
                      <tr>
                        <th scope="col">Etapa</th>
                        <th scope="col">Estado</th>
                        <th scope="col" className="tabla__numerica">
                          Cuentas
                        </th>
                        <th scope="col" className="tabla__numerica">
                          De la anterior
                        </th>
                        <th scope="col" className="tabla__numerica">
                          De la cohorte
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {e.etapas.map((x) => (
                        <tr key={x.etapa}>
                          <th scope="row">{ETAPAS[x.etapa] ?? etiquetaDeCodigo(x.etapa)}</th>
                          <td>
                            <InsigniaEstado estado={x.estado_valor} />
                          </td>
                          <td className="tabla__numerica">
                            <ValorConEstado
                              estado={x.estado_valor}
                              valor={x.cuentas}
                              formatear={formatearEntero}
                            />
                          </td>
                          <td className="tabla__numerica">
                            <ValorOpcional
                              valor={x.pct_etapa_anterior}
                              formatear={formatearPorcentaje}
                            />
                          </td>
                          <td className="tabla__numerica">
                            <ValorOpcional valor={x.pct_cohorte} formatear={formatearPorcentaje} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
              >
                {barras.length > 0 ? (
                  <Barras
                    titulo={`Embudo ${e.embudo}`}
                    barras={barras}
                    formatear={formatearEntero}
                    colores={COLORES.ordinal.slice(1)}
                  />
                ) : (
                  <p className="vacio">Ninguna etapa disponible.</p>
                )}
              </Figura>
            );
          })}
        </div>
      )}
    </Resultado>
  );
}

function Controles({ id }: { readonly id: string }) {
  const controles = useControles(id);
  return (
    <details className="tarjeta detalles">
      <summary>Controles de reconciliación</summary>
      <Resultado consulta={controles} que="los controles">
        {(datos) => (
          <>
            <p>
              {formatearEntero(datos.controles_totales)} controles,{" "}
              {formatearEntero(datos.controles_fallidos)} fallidos. Exactos, sin tolerancia.
            </p>
            <div className="tabla-contenedor" role="region" aria-label="Controles" tabIndex={0}>
              <table className="tabla tabla--compacta">
                <caption>Cada control con lo esperado, lo observado y su diferencia</caption>
                <thead>
                  <tr>
                    <th scope="col">Control</th>
                    <th scope="col">Estado</th>
                    <th scope="col" className="tabla__numerica">
                      Esperado
                    </th>
                    <th scope="col" className="tabla__numerica">
                      Observado
                    </th>
                    <th scope="col" className="tabla__numerica">
                      Diferencia
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {datos.controles.map((c) => (
                    <tr key={c.codigo}>
                      <th scope="row">
                        <code>{c.codigo}</code>
                      </th>
                      <td>
                        <InsigniaEstado estado={c.estado} />
                      </td>
                      <td className="tabla__numerica">
                        <ValorOpcional valor={c.esperado} formatear={formatearDecimal} />
                      </td>
                      <td className="tabla__numerica">
                        <ValorOpcional valor={c.observado} formatear={formatearDecimal} />
                      </td>
                      <td className="tabla__numerica">
                        <ValorOpcional valor={c.diferencia} formatear={formatearDecimal} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Resultado>
    </details>
  );
}
