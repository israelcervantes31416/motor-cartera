/**
 * Optimizacion: la ejecucion de optimization/v1 del contexto, leida de sus propias rutas. Cinco
 * vistas: el resumen con su certificado, la capacidad por canal y por pool, las restricciones (las
 * activas, destacadas con texto), las asignaciones paginadas en el servidor y el solver con su
 * huella. Un TIME_LIMIT o un INFACTIBLE se muestran tal cual, con su diagnostico.
 */
import { createColumnHelper } from "@tanstack/react-table";
import { useId, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import {
  useCapacidad,
  useDiagnostico,
  useOptimizacion,
  useRestricciones,
  useResultadosOptimizacion,
  type FiltrosDeResultados,
} from "../../api/consultas";
import type {
  EjecucionOptimizacion,
  PaginaCapacidad,
  PaginaRestricciones,
  PaginaResultadosOptimizacion,
} from "../../api/tipos";
import { ConContexto, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_OPTIMO } from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { Fuente } from "../../components/Fuente";
import { Barras, Figura } from "../../components/Graficas";
import { Ficha, Indicador, Indicadores } from "../../components/Kpi";
import { Pestanas } from "../../components/Pestanas";
import { Paginacion, TablaDatos, TAMANOS_DE_PAGINA } from "../../components/Tabla";
import {
  comoLista,
  comoNumero,
  comoRegistro,
  comoTexto,
  textoDe,
  type Registro,
} from "../../lib/datos";
import { rutaConContexto, type Contexto } from "../../lib/contexto";
import {
  abreviar,
  etiquetaDeCodigo,
  formatearCentavos,
  formatearDecimal,
  formatearDuracion,
  formatearEntero,
  formatearFecha,
  formatearPorcentaje,
  formatearPorcentajePreciso,
} from "../../lib/formato";

const VISTAS = [
  ["resumen", "Resumen"],
  ["capacidad", "Capacidad"],
  ["restricciones", "Restricciones"],
  ["asignaciones", "Asignaciones"],
  ["solver", "Solver y auditoría"],
] as const;

export function PaginaOptimizacion() {
  const { vista = "resumen" } = useParams();
  return (
    <ConContexto titulo="Optimización" requiere={["optimizacion"]}>
      {(listo) => <Optimizacion vista={vista} {...listo} />}
    </ConContexto>
  );
}

function Optimizacion({ vista, contexto }: ContextoListo & { readonly vista: string }) {
  const id = contexto.optimizacion ?? "";
  const ejecucion = useOptimizacion(id);
  return (
    <>
      <h1>Optimización</h1>
      <Aviso tono="aviso">{AVISO_OPTIMO}</Aviso>
      <Pestanas
        etiqueta="Vistas de optimización"
        pestanas={VISTAS.map(([clave, etiqueta]) => ({
          etiqueta,
          a: rutaConContexto(`/optimizacion/${clave}`, contexto),
        }))}
      />
      <Resultado consulta={ejecucion} que="la optimización">
        {(e) => {
          const publicada = e.estado === "EXITOSA";
          return (
            <>
              <Fuente nombre="optimization/v1" runId={e.optimizacion_run_id} estado={e.estado}>
                {`${e.resultado ?? ""} · operación ${formatearFecha(e.fecha_operacion)} · ${e.version_politica}`}
              </Fuente>
              {vista === "capacidad" ? (
                publicada ? (
                  <VistaCapacidad id={id} />
                ) : (
                  <SinPlan e={e} />
                )
              ) : vista === "restricciones" ? (
                publicada ? (
                  <VistaRestricciones id={id} />
                ) : (
                  <SinPlan e={e} />
                )
              ) : vista === "asignaciones" ? (
                publicada ? (
                  <VistaAsignaciones id={id} contexto={contexto} />
                ) : (
                  <SinPlan e={e} />
                )
              ) : vista === "solver" ? (
                <VistaSolver e={e} />
              ) : (
                <VistaResumen e={e} />
              )}
            </>
          );
        }}
      </Resultado>
    </>
  );
}

function SinPlan({ e }: { readonly e: EjecucionOptimizacion }) {
  return (
    <section className="tarjeta alerta alerta--aviso">
      <h2>Sin plan publicado</h2>
      <p>
        La ejecución está <InsigniaEstado estado={e.estado} />
        {e.resultado ? ` (${e.resultado})` : ""}: solo un óptimo demostrado publica un plan. Su
        diagnóstico está en Solver y auditoría.
      </p>
    </section>
  );
}

function VistaResumen({ e }: { readonly e: EjecucionOptimizacion }) {
  const reduccion = comoRegistro(comoRegistro(e.resumen)?.["reduccion"]);
  return (
    <>
      <section className="tarjeta" aria-labelledby="optimizacion-resumen">
        <h2 id="optimizacion-resumen">El plan</h2>
        <p>
          Solver:{" "}
          {e.solver_status ? (
            <InsigniaEstado estado={e.solver_status} />
          ) : (
            <InsigniaEstado estado="SIN_VALOR" />
          )}
        </p>
        <Indicadores etiqueta="El plan óptimo">
          <Indicador
            etiqueta="Valor de oportunidad del plan"
            detalle="Recuperación positiva esperada predicha, en el objetivo: no dinero a recuperar."
          >
            <ValorOpcional valor={e.objetivo_primario_centavos} formatear={formatearCentavos} />
          </Indicador>
          <Indicador etiqueta="Mejor cota">
            <ValorOpcional valor={e.best_bound} formatear={formatearCentavos} />
          </Indicador>
          <Indicador etiqueta="Gap relativo">
            <ValorOpcional valor={e.mip_gap} formatear={formatearPorcentajePreciso} />
          </Indicador>
          <Indicador etiqueta="Cuentas asignadas">{formatearEntero(e.cuentas_asignadas)}</Indicador>
          <Indicador etiqueta="Elegibles">{formatearEntero(e.cuentas_elegibles)}</Indicador>
          <Indicador etiqueta="No elegibles">{formatearEntero(e.cuentas_no_elegibles)}</Indicador>
          <Indicador etiqueta="Universo">{formatearEntero(e.cuentas_universo)}</Indicador>
          <Indicador etiqueta="Tiempo del solver">
            {formatearDuracion(Number(e.solver_seconds))}
          </Indicador>
        </Indicadores>
        {reduccion !== null ? (
          <Ficha
            etiqueta="Reducción exacta de candidatas"
            filas={[
              [
                "Elegibles",
                <ValorOpcional
                  key="e"
                  valor={comoNumero(reduccion["elegibles"])}
                  formatear={formatearEntero}
                />,
              ],
              [
                "Retenidas en el modelo",
                <ValorOpcional
                  key="r"
                  valor={comoNumero(reduccion["retenidas"])}
                  formatear={formatearEntero}
                />,
              ],
              [
                "Eliminadas por dominancia",
                <ValorOpcional
                  key="x"
                  valor={comoNumero(reduccion["eliminadas"])}
                  formatear={formatearEntero}
                />,
              ],
              [
                "Clases de dominancia",
                <ValorOpcional
                  key="c"
                  valor={comoNumero(reduccion["clases"])}
                  formatear={formatearEntero}
                />,
              ],
              [
                "Porcentaje eliminado (publicado)",
                <ValorOpcional
                  key="p"
                  valor={comoNumero(reduccion["porcentaje_eliminado"])}
                  formatear={(v) => `${formatearDecimal(v)} %`}
                />,
              ],
            ]}
          />
        ) : null}
      </section>
      <section className="tarjeta" aria-labelledby="optimizacion-limitaciones">
        <h2 id="optimizacion-limitaciones">Lo que el plan no dice</h2>
        <ul className="lista-compacta">
          {e.limitaciones.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

const columnaPool = createColumnHelper<PaginaCapacidad["elementos"][number]>();

function Medidor({ valor }: { readonly valor: number | null }) {
  if (valor === null) return <InsigniaEstado estado="SIN_VALOR" />;
  const ancho = Math.max(0, Math.min(1, valor)) * 100;
  return (
    <span className="medidor">
      <span className="medidor__pista" aria-hidden="true">
        <span className="medidor__relleno" style={{ width: `${ancho}%` }} />
      </span>
      {formatearPorcentaje(valor)}
    </span>
  );
}

function VistaCapacidad({ id }: { readonly id: string }) {
  const [busqueda, setBusqueda] = useSearchParams();
  const pagina = Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1);
  const capacidad = useCapacidad(id, { pagina, por_pagina: 50 });
  return (
    <Resultado consulta={capacidad} que="la capacidad">
      {(c) => {
        const porCanal = Object.entries(comoRegistro(c.por_canal) ?? {});
        return (
          <>
            <Figura
              titulo="Utilización por canal"
              descripcion="La capacidad utilizada entre la efectiva de cada canal, como la publicó el plan."
              datos={
                <table className="tabla tabla--compacta">
                  <caption>Por canal</caption>
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
                      <th scope="col" className="tabla__numerica">
                        Unidades
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {porCanal.map(([canal, v]) => {
                      const r = comoRegistro(v);
                      return (
                        <tr key={canal}>
                          <th scope="row">{canal}</th>
                          <td className="tabla__numerica">
                            <ValorOpcional
                              valor={comoNumero(r?.["capacidad"])}
                              formatear={formatearEntero}
                            />
                          </td>
                          <td className="tabla__numerica">
                            <ValorOpcional
                              valor={comoNumero(r?.["utilizada"])}
                              formatear={formatearEntero}
                            />
                          </td>
                          <td className="tabla__numerica">
                            <ValorOpcional
                              valor={comoNumero(r?.["utilizacion"])}
                              formatear={formatearPorcentaje}
                            />
                          </td>
                          <td className="tabla__numerica">
                            <ValorOpcional
                              valor={comoNumero(r?.["unidades"])}
                              formatear={formatearEntero}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              }
            >
              {/* Un canal sin utilizacion publicada (sin capacidad declarada) no es una barra en
                  cero: queda solo en la tabla, con su estado. */}
              <Barras
                titulo="Utilización por canal"
                barras={porCanal.flatMap(([canal, v]) => {
                  const valor = comoNumero(comoRegistro(v)?.["utilizacion"]);
                  return valor === null ? [] : [{ categoria: canal, valor }];
                })}
                formatear={formatearPorcentaje}
              />
            </Figura>
            <TablaDatos
              titulo="Capacidad de cada pool"
              filas={c.elementos}
              claveDeFila={(p) => p.pool}
              columnas={[
                columnaPool.display({
                  id: "pool",
                  header: "Pool",
                  cell: ({ row }) => <code>{row.original.pool}</code>,
                }),
                columnaPool.display({
                  id: "canal",
                  header: "Canal",
                  cell: ({ row }) => row.original.canal,
                }),
                columnaPool.display({
                  id: "alcance",
                  header: "Alcance",
                  cell: ({ row }) =>
                    `${etiquetaDeCodigo(row.original.tipo_alcance)}${row.original.referencia_alcance ? ` · ${row.original.referencia_alcance}` : ""}`,
                }),
                columnaPool.display({
                  id: "capacidad",
                  header: "Capacidad efectiva",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.capacidad),
                }),
                columnaPool.display({
                  id: "utilizada",
                  header: "Utilizada",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.utilizada),
                }),
                columnaPool.display({
                  id: "holgura",
                  header: "Holgura",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.holgura),
                }),
                columnaPool.display({
                  id: "utilizacion",
                  header: "Utilización",
                  cell: ({ row }) => <Medidor valor={comoNumero(row.original.utilizacion)} />,
                }),
                columnaPool.display({
                  id: "unidades",
                  header: "Unidades",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.unidades_consumidas),
                }),
              ]}
            />
            <Paginacion
              total={c.total}
              pagina={c.pagina}
              porPagina={c.por_pagina}
              etiqueta="los pools"
              onPagina={(p) => {
                const nueva = new URLSearchParams(busqueda);
                nueva.set("pagina", String(p));
                setBusqueda(nueva);
              }}
            />
            <p className="nota">Las unidades de recurso son esfuerzo abstracto, no costos.</p>
          </>
        );
      }}
    </Resultado>
  );
}

const columnaRestriccion = createColumnHelper<PaginaRestricciones["elementos"][number]>();

function VistaRestricciones({ id }: { readonly id: string }) {
  const [busqueda, setBusqueda] = useSearchParams();
  const pagina = Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1);
  const binding = busqueda.get("binding");
  const restricciones = useRestricciones(id, {
    pagina,
    por_pagina: 50,
    ...(binding === "true" || binding === "false" ? { binding: binding === "true" } : {}),
  });
  const idFiltro = useId();
  return (
    <>
      <div className="filtros">
        <div className="campo">
          <label htmlFor={idFiltro}>Restricciones</label>
          <select
            id={idFiltro}
            value={binding ?? ""}
            onChange={(evento) => {
              const nueva = new URLSearchParams(busqueda);
              if (evento.target.value === "") nueva.delete("binding");
              else nueva.set("binding", evento.target.value);
              nueva.delete("pagina");
              setBusqueda(nueva);
            }}
          >
            <option value="">Todas</option>
            <option value="true">Solo las activas (binding)</option>
            <option value="false">Solo las que tienen holgura</option>
          </select>
        </div>
      </div>
      <Resultado consulta={restricciones} que="las restricciones">
        {(r) => (
          <>
            <TablaDatos
              titulo="Cada restricción del plan: lado izquierdo, derecho y holgura"
              filas={r.elementos}
              claveDeFila={(x) => x.codigo}
              vacio="Ninguna restricción con ese filtro."
              columnas={[
                columnaRestriccion.display({
                  id: "codigo",
                  header: "Código",
                  cell: ({ row }) => <code>{row.original.codigo}</code>,
                }),
                columnaRestriccion.display({
                  id: "tipo",
                  header: "Tipo",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.tipo),
                }),
                columnaRestriccion.display({
                  id: "dimension",
                  header: "Dimensión",
                  cell: ({ row }) =>
                    row.original.dimension
                      ? `${etiquetaDeCodigo(row.original.dimension)} = ${row.original.valor_dimension ?? "—"}`
                      : "—",
                }),
                columnaRestriccion.display({
                  id: "lhs",
                  header: "LHS",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.lhs),
                }),
                columnaRestriccion.display({
                  id: "operador",
                  header: "Operador",
                  cell: ({ row }) => <code>{row.original.operador}</code>,
                }),
                columnaRestriccion.display({
                  id: "rhs",
                  header: "RHS",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.rhs),
                }),
                columnaRestriccion.display({
                  id: "holgura",
                  header: "Holgura",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearEntero(row.original.holgura),
                }),
                columnaRestriccion.display({
                  id: "binding",
                  header: "Binding",
                  cell: ({ row }) => (
                    <InsigniaEstado estado={row.original.binding ? "ACTIVA" : "HOLGADA"} />
                  ),
                }),
              ]}
            />
            <Paginacion
              total={r.total}
              pagina={r.pagina}
              porPagina={r.por_pagina}
              etiqueta="las restricciones"
              onPagina={(p) => {
                const nueva = new URLSearchParams(busqueda);
                nueva.set("pagina", String(p));
                setBusqueda(nueva);
              }}
            />
          </>
        )}
      </Resultado>
    </>
  );
}

const ESTADOS = ["ASIGNADA", "NO_SELECCIONADA", "NO_ELEGIBLE"] as const;
const CANALES = ["DIGITAL", "TELEFONICA", "CAMPO"] as const;
const PRIORIDADES = ["MUY_ALTA", "ALTA", "MEDIA", "BAJA"] as const;
const CODIGO = /^[A-Z0-9_-]{1,32}$/;

const columnaResultado = createColumnHelper<PaginaResultadosOptimizacion["elementos"][number]>();

function VistaAsignaciones({ id, contexto }: { readonly id: string; readonly contexto: Contexto }) {
  const [busqueda, setBusqueda] = useSearchParams();
  const porPagina = Number(busqueda.get("por_pagina") ?? "50");
  const filtros: FiltrosDeResultados = {
    pagina: Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1),
    por_pagina: (TAMANOS_DE_PAGINA as readonly number[]).includes(porPagina) ? porPagina : 50,
  };
  const estado = ESTADOS.find((x) => x === busqueda.get("estado"));
  const canal = CANALES.find((x) => x === busqueda.get("canal"));
  const prioridad = PRIORIDADES.find((x) => x === busqueda.get("prioridad"));
  const pool = busqueda.get("pool") ?? "";
  const base = busqueda.get("base") ?? "";
  const producto = busqueda.get("producto") ?? "";
  const completos: FiltrosDeResultados = {
    ...filtros,
    ...(estado ? { estado } : {}),
    ...(canal ? { canal } : {}),
    ...(prioridad ? { prioridad } : {}),
    ...(CODIGO.test(pool) ? { pool } : {}),
    ...(CODIGO.test(base) ? { base } : {}),
    ...(/^[A-Z]{1,20}$/.test(producto) ? { producto } : {}),
  };
  const resultados = useResultadosOptimizacion(id, completos);

  function cambiar(llave: string, valor: string | undefined) {
    const nueva = new URLSearchParams(busqueda);
    if (valor === undefined || valor === "") nueva.delete(llave);
    else nueva.set(llave, valor);
    if (llave !== "pagina") nueva.delete("pagina");
    setBusqueda(nueva);
  }

  return (
    <>
      <form
        className="filtros"
        aria-label="Filtros de asignaciones"
        onSubmit={(evento) => {
          evento.preventDefault();
        }}
      >
        <Lista
          etiqueta="Estado"
          valor={estado ?? ""}
          opciones={ESTADOS}
          onCambio={(v) => {
            cambiar("estado", v);
          }}
        />
        <Lista
          etiqueta="Canal asignado"
          valor={canal ?? ""}
          opciones={CANALES}
          onCambio={(v) => {
            cambiar("canal", v);
          }}
        />
        <Lista
          etiqueta="Prioridad"
          valor={prioridad ?? ""}
          opciones={PRIORIDADES}
          onCambio={(v) => {
            cambiar("prioridad", v);
          }}
        />
        <Texto
          etiqueta="Pool"
          valor={pool}
          onCambio={(v) => {
            cambiar("pool", v?.toUpperCase());
          }}
        />
        <Texto
          etiqueta="Base"
          valor={base}
          onCambio={(v) => {
            cambiar("base", v?.toUpperCase());
          }}
        />
        <Texto
          etiqueta="Producto"
          valor={producto}
          onCambio={(v) => {
            cambiar("producto", v?.toUpperCase());
          }}
        />
      </form>
      <Resultado consulta={resultados} que="las asignaciones">
        {(r) => (
          <>
            <TablaDatos
              titulo="El resultado de cada cuenta en el plan"
              filas={r.elementos}
              claveDeFila={(x) => x.cuenta_id}
              vacio="Ninguna cuenta con esos filtros."
              columnas={[
                columnaResultado.display({
                  id: "cuenta",
                  header: "Cuenta",
                  cell: ({ row }) => (
                    <Link
                      to={rutaConContexto(
                        `/cuentas/${encodeURIComponent(row.original.cuenta_id)}/optimizacion`,
                        contexto,
                      )}
                    >
                      <code>{abreviar(row.original.cuenta_id, 13)}</code>
                    </Link>
                  ),
                }),
                columnaResultado.display({
                  id: "estado",
                  header: "Estado",
                  cell: ({ row }) => <InsigniaEstado estado={row.original.estado} />,
                }),
                columnaResultado.display({
                  id: "motivo",
                  header: "Motivo (un hecho)",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.motivo),
                }),
                columnaResultado.display({
                  id: "canal_decision",
                  header: "Canal de la decisión",
                  cell: ({ row }) => row.original.canal_decision,
                }),
                columnaResultado.display({
                  id: "canal",
                  header: "Canal asignado",
                  cell: ({ row }) => row.original.canal_asignado ?? "—",
                }),
                columnaResultado.display({
                  id: "pool",
                  header: "Pool",
                  cell: ({ row }) => row.original.pool ?? "—",
                }),
                columnaResultado.display({
                  id: "prioridad",
                  header: "Prioridad",
                  cell: ({ row }) => etiquetaDeCodigo(row.original.prioridad),
                }),
                columnaResultado.display({
                  id: "base",
                  header: "Base",
                  cell: ({ row }) => row.original.base ?? "—",
                }),
                columnaResultado.display({
                  id: "valor",
                  header: "Valor de oportunidad",
                  meta: { numerica: true },
                  cell: ({ row }) => formatearCentavos(row.original.valor_oportunidad_centavos),
                }),
                columnaResultado.display({
                  id: "unidades",
                  header: "Unidades",
                  meta: { numerica: true },
                  cell: ({ row }) => (
                    <ValorOpcional
                      valor={row.original.unidades_recurso}
                      formatear={formatearEntero}
                    />
                  ),
                }),
              ]}
            />
            <Paginacion
              total={r.total}
              pagina={r.pagina}
              porPagina={r.por_pagina}
              etiqueta="las asignaciones"
              onPagina={(p) => {
                cambiar("pagina", String(p));
              }}
              onPorPagina={(n) => {
                cambiar("por_pagina", String(n));
              }}
            />
          </>
        )}
      </Resultado>
    </>
  );
}

function Lista({
  etiqueta,
  valor,
  opciones,
  onCambio,
}: {
  readonly etiqueta: string;
  readonly valor: string;
  readonly opciones: readonly string[];
  readonly onCambio: (v: string | undefined) => void;
}) {
  const id = useId();
  return (
    <div className="campo">
      <label htmlFor={id}>{etiqueta}</label>
      <select
        id={id}
        value={valor}
        onChange={(evento) => {
          onCambio(evento.target.value || undefined);
        }}
      >
        <option value="">Todos</option>
        {opciones.map((o) => (
          <option key={o} value={o}>
            {etiquetaDeCodigo(o)}
          </option>
        ))}
      </select>
    </div>
  );
}

function Texto({
  etiqueta,
  valor,
  onCambio,
}: {
  readonly etiqueta: string;
  readonly valor: string;
  readonly onCambio: (v: string | undefined) => void;
}) {
  const id = useId();
  return (
    <div className="campo">
      <label htmlFor={id}>{etiqueta}</label>
      <input
        id={id}
        type="text"
        autoComplete="off"
        defaultValue={valor}
        placeholder="Todos"
        onBlur={(evento) => {
          onCambio(evento.target.value.trim() || undefined);
        }}
        onKeyDown={(evento) => {
          if (evento.key === "Enter") onCambio(evento.currentTarget.value.trim() || undefined);
        }}
      />
    </div>
  );
}

function VistaSolver({ e }: { readonly e: EjecucionOptimizacion }) {
  const huella = comoRegistro(e.huella_solver);
  const parametros = comoRegistro(huella?.["parametros"]);
  const certificado = comoRegistro(e.certificado);
  const niveles = comoLista(certificado?.["niveles"])
    .map(comoRegistro)
    .filter((n): n is Registro => n !== null);
  const diagnostico = useDiagnostico(e.optimizacion_run_id, e.estado !== "EN_PROCESO");
  return (
    <>
      <section className="tarjeta" aria-labelledby="solver-ficha">
        <h2 id="solver-ficha">Solver</h2>
        <Ficha
          etiqueta="El solver y el modelo"
          filas={[
            [
              "Estado del solver",
              e.solver_status ? <InsigniaEstado key="s" estado={e.solver_status} /> : "—",
            ],
            ["Algoritmo", comoTexto(huella?.["algoritmo"]) ?? "—"],
            [
              "HiGHS · SciPy · NumPy · Python",
              [huella?.["highs"], huella?.["scipy"], huella?.["numpy"], huella?.["python"]]
                .map((v) => comoTexto(v) ?? "—")
                .join(" · "),
            ],
            [
              "Sistema",
              `${comoTexto(huella?.["sistema"]) ?? "—"} · ${comoTexto(huella?.["arquitectura"]) ?? "—"}`,
            ],
            [
              "Huella del solver",
              <Copiable
                key="f"
                valor={e.solver_fingerprint}
                etiqueta="la huella del solver"
                largo={12}
              />,
            ],
            ["Variables", formatearEntero(e.variables)],
            ["Restricciones", formatearEntero(e.restricciones_solver)],
            ["No nulos", formatearEntero(e.elementos_no_nulos)],
            ["Clases de dominancia", formatearEntero(e.clases)],
            ["Tiempo del solver", formatearDuracion(Number(e.solver_seconds))],
            [
              "Gap relativo",
              <ValorOpcional key="g" valor={e.mip_gap} formatear={formatearPorcentajePreciso} />,
            ],
            [
              "Mejor cota del objetivo primario",
              <ValorOpcional key="b" valor={e.best_bound} formatear={formatearCentavos} />,
            ],
            ...(parametros
              ? Object.entries(parametros).map(([k, v]): [string, ReactNode] => [
                  `Parámetro ${k}`,
                  String(v),
                ])
              : []),
          ]}
        />
      </section>
      <section className="tarjeta" aria-labelledby="solver-certificado">
        <h2 id="solver-certificado">Certificado de optimalidad</h2>
        {niveles.length === 0 ? (
          <p>Sin certificado: la ejecución no llegó a resolver.</p>
        ) : (
          <div
            className="tabla-contenedor"
            role="region"
            aria-label="Niveles del certificado"
            tabIndex={0}
          >
            <table className="tabla tabla--compacta">
              <caption>Cada nivel lexicográfico, con el óptimo del anterior fijo</caption>
              <thead>
                <tr>
                  <th scope="col">Nivel</th>
                  <th scope="col">Sentido</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Método</th>
                  <th scope="col" className="tabla__numerica">
                    Objetivo
                  </th>
                  <th scope="col" className="tabla__numerica">
                    Mejor cota
                  </th>
                  <th scope="col" className="tabla__numerica">
                    Gap
                  </th>
                  <th scope="col" className="tabla__numerica">
                    Segundos
                  </th>
                </tr>
              </thead>
              <tbody>
                {niveles.map((n, i) => (
                  <tr key={i}>
                    <th scope="row">{textoDe(n["nivel"], String(i + 1))}</th>
                    <td>{etiquetaDeCodigo(textoDe(n["sentido"]))}</td>
                    <td>
                      <InsigniaEstado estado={textoDe(n["estado"])} />
                    </td>
                    <td>{etiquetaDeCodigo(textoDe(n["metodo"]))}</td>
                    <td className="tabla__numerica">
                      <ValorOpcional
                        valor={comoNumero(n["objetivo"])}
                        formatear={formatearEntero}
                      />
                    </td>
                    <td className="tabla__numerica">
                      <ValorOpcional
                        valor={comoNumero(n["mejor_cota"])}
                        formatear={formatearDecimal}
                      />
                    </td>
                    <td className="tabla__numerica">
                      <ValorOpcional
                        valor={comoNumero(n["gap_relativo"])}
                        formatear={formatearDecimal}
                      />
                    </td>
                    <td className="tabla__numerica">
                      <ValorOpcional
                        valor={comoNumero(n["segundos"])}
                        formatear={formatearDecimal}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="tarjeta" aria-labelledby="solver-auditoria">
        <h2 id="solver-auditoria">Hashes y diagnóstico</h2>
        <Ficha
          etiqueta="Hashes del plan"
          filas={[
            [
              "Firma de las entradas",
              <Copiable key="f" valor={e.firma_inputs} etiqueta="la firma" largo={12} />,
            ],
            [
              "Hash del plan",
              e.hash_plan ? (
                <Copiable key="p" valor={e.hash_plan} etiqueta="el hash del plan" largo={12} />
              ) : (
                "Sin plan"
              ),
            ],
            [
              "Hash de la política",
              <Copiable
                key="h"
                valor={e.hash_politica}
                etiqueta="el hash de la política"
                largo={12}
              />,
            ],
            [
              "Hash de la configuración",
              <Copiable
                key="c"
                valor={e.hash_configuracion}
                etiqueta="el hash de la configuración"
                largo={12}
              />,
            ],
          ]}
        />
        <Resultado consulta={diagnostico} que="el diagnóstico">
          {(d) => (
            <>
              <p>
                {d.plan_publicado ? "Plan publicado." : "Sin plan publicado."} Resultado:{" "}
                <InsigniaEstado estado={d.resultado ?? d.estado} />
              </p>
              {d.detalle ? <p>{d.detalle}</p> : null}
              {d.diagnostico ? (
                <details className="detalles">
                  <summary>Diagnóstico publicado</summary>
                  <pre className="json">{JSON.stringify(d.diagnostico, null, 2)}</pre>
                </details>
              ) : null}
            </>
          )}
        </Resultado>
      </section>
    </>
  );
}
