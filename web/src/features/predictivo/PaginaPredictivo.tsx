/**
 * Predictivo: el scoring del contexto y su version del modelo, leidos de predictive/model-v1. Cinco
 * vistas, cada una su URL: la ficha del modelo, su desempeno contra el baseline, su calibracion por
 * bin, el scoring (la distribucion de sus predicciones, contada en la base, y sus predicciones,
 * paginadas en el servidor) y su drift. Una prediccion no es un efecto causal.
 */
import { createColumnHelper } from "@tanstack/react-table";
import { useId, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import {
  useDistribucion,
  useDrift,
  useModelo,
  usePredicciones,
  useScoring,
  type FiltrosDePredicciones,
} from "../../api/consultas";
import type {
  ComponentePredictivo,
  FilaDeDrift,
  ModeloPredictivo,
  PaginaPredicciones,
} from "../../api/tipos";
import { ConContexto, ejecucionDe, type ContextoListo } from "../../app/contexto";
import { Aviso, AVISO_DRIFT, AVISO_PREDICCION } from "../../components/Avisos";
import { Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado, ValorOpcional } from "../../components/Estado";
import { Fuente } from "../../components/Fuente";
import {
  Barras,
  Calibracion,
  decimo,
  Figura,
  Histograma,
  TablaHistograma,
} from "../../components/Graficas";
import { Ficha } from "../../components/Kpi";
import { Pestanas } from "../../components/Pestanas";
import { Paginacion, TablaDatos, TAMANOS_DE_PAGINA } from "../../components/Tabla";
import {
  comoLista,
  comoNumero,
  comoRegistro,
  conteosDe,
  textoDe,
  type Registro,
} from "../../lib/datos";
import { rutaConContexto, type Contexto } from "../../lib/contexto";
import {
  abreviar,
  etiquetaDeCodigo,
  formatearDecimal,
  formatearEntero,
  formatearFecha,
  formatearInstante,
  formatearMonto,
  formatearPorcentaje,
} from "../../lib/formato";

const VISTAS = [
  ["modelo", "Modelo"],
  ["desempeno", "Desempeño"],
  ["calibracion", "Calibración"],
  ["scoring", "Scoring"],
  ["drift", "Drift"],
] as const;

export function PaginaPredictivo() {
  const { vista = "modelo" } = useParams();
  return (
    <ConContexto titulo="Predictivo" requiere={["scoring"]}>
      {(listo) => <Predictivo vista={vista} {...listo} />}
    </ConContexto>
  );
}

function Predictivo({ vista, contexto, validado }: ContextoListo & { readonly vista: string }) {
  const scoringId = contexto.scoring;
  const modeloId = ejecucionDe(validado, "scoring")?.lee["MODELO"];
  const modelo = useModelo(modeloId);
  return (
    <>
      <h1>Predictivo</h1>
      <Aviso tono="aviso">{AVISO_PREDICCION}</Aviso>
      <Pestanas
        etiqueta="Vistas de predictivo"
        pestanas={VISTAS.map(([clave, etiqueta]) => ({
          etiqueta,
          a: rutaConContexto(`/predictivo/${clave}`, contexto),
        }))}
      />
      {vista === "scoring" ? (
        <VistaScoring id={scoringId ?? ""} contexto={contexto} />
      ) : vista === "drift" ? (
        <VistaDrift id={scoringId ?? ""} />
      ) : (
        <Resultado consulta={modelo} que="la versión del modelo">
          {(m) =>
            vista === "desempeno" ? (
              <VistaDesempeno modelo={m} />
            ) : vista === "calibracion" ? (
              <VistaCalibracion modelo={m} />
            ) : (
              <VistaModelo modelo={m} />
            )
          }
        </Resultado>
      )}
    </>
  );
}

function clasificador(c: ComponentePredictivo): boolean {
  return c.algoritmo === "LOGISTICA_L2";
}

function VistaModelo({ modelo }: { readonly modelo: ModeloPredictivo }) {
  const ficha = comoRegistro(modelo.ficha);
  const usoNoPrevisto = comoLista(ficha?.["uso_no_previsto"]).map(String);
  const limitaciones = comoLista(ficha?.["limitaciones"]).map(String);
  const privacidad = comoLista(ficha?.["privacidad_y_etica"]).map(String);
  return (
    <>
      <section className="tarjeta" aria-labelledby="ficha-modelo">
        <h2 id="ficha-modelo">Ficha del modelo</h2>
        <Fuente
          nombre={modelo.version_modelo}
          runId={modelo.modelo_version_id}
          estado={modelo.estado}
        />
        <Ficha
          etiqueta="La versión del modelo"
          filas={[
            ["Versión", modelo.version_modelo],
            [
              "Entrenamiento",
              <Copiable key="t" valor={modelo.training_run_id} etiqueta="el entrenamiento" />,
            ],
            [
              "TRAIN",
              `${formatearFecha(modelo.periodo_train_desde)} – ${formatearFecha(modelo.periodo_train_hasta)}`,
            ],
            [
              "VALIDATION",
              `${formatearFecha(modelo.periodo_validation_desde)} – ${formatearFecha(modelo.periodo_validation_hasta)}`,
            ],
            [
              "TEST",
              `${formatearFecha(modelo.periodo_test_desde)} – ${formatearFecha(modelo.periodo_test_hasta)}`,
            ],
            [
              "Features",
              <span key="f">
                {modelo.version_features}{" "}
                <Copiable
                  valor={modelo.hash_features}
                  etiqueta="el hash de las features"
                  largo={12}
                />
              </span>,
            ],
            [
              "Targets",
              <span key="g">
                {modelo.version_targets}{" "}
                <Copiable
                  valor={modelo.hash_targets}
                  etiqueta="el hash de los targets"
                  largo={12}
                />
              </span>,
            ],
            [
              "Política",
              <span key="p">
                {modelo.version_politica}{" "}
                <Copiable
                  valor={modelo.hash_politica}
                  etiqueta="el hash de la política"
                  largo={12}
                />
              </span>,
            ],
            [
              "Hash del modelo",
              <Copiable
                key="m"
                valor={modelo.hash_modelo}
                etiqueta="el hash del modelo"
                largo={12}
              />,
            ],
            [
              "Hash del dataset",
              <Copiable
                key="d"
                valor={modelo.dataset_hash}
                etiqueta="el hash del dataset"
                largo={12}
              />,
            ],
            ["Publicada", modelo.publicada_en ? formatearInstante(modelo.publicada_en) : "—"],
          ]}
        />
        {typeof ficha?.["uso_previsto"] === "string" ? (
          <>
            <h3>Uso previsto</h3>
            <p>{ficha["uso_previsto"]}</p>
          </>
        ) : null}
        {usoNoPrevisto.length > 0 ? (
          <>
            <h3>Uso no previsto</h3>
            <ul className="lista-compacta">
              {usoNoPrevisto.map((u) => (
                <li key={u}>{u}</li>
              ))}
            </ul>
          </>
        ) : null}
      </section>
      <section className="tarjeta" aria-labelledby="componentes-modelo">
        <h2 id="componentes-modelo">Componentes</h2>
        <div className="tabla-contenedor" role="region" aria-label="Componentes" tabIndex={0}>
          <table className="tabla">
            <caption>Un componente por target</caption>
            <thead>
              <tr>
                <th scope="col">Target</th>
                <th scope="col">Algoritmo</th>
                <th scope="col">Calibración</th>
                <th scope="col">Estado</th>
                <th scope="col" className="tabla__numerica">
                  Prevalencia en TEST
                </th>
                <th scope="col">Artefacto</th>
              </tr>
            </thead>
            <tbody>
              {modelo.componentes.map((c) => (
                <tr key={c.target}>
                  <th scope="row">{c.target}</th>
                  <td>{c.algoritmo}</td>
                  <td>{c.calibracion}</td>
                  <td>
                    <InsigniaEstado estado={c.estado} />
                  </td>
                  <td className="tabla__numerica">
                    <ValorOpcional
                      valor={comoNumero(c.metricas_test["prevalencia"])}
                      formatear={formatearPorcentaje}
                    />
                  </td>
                  <td>
                    <Copiable
                      valor={c.hash_artefacto}
                      etiqueta={`el artefacto de ${c.target}`}
                      largo={12}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {limitaciones.length > 0 || privacidad.length > 0 ? (
        <section className="tarjeta" aria-labelledby="limitaciones-modelo">
          <h2 id="limitaciones-modelo">Limitaciones, privacidad y ética</h2>
          <ul className="lista-compacta">
            {[...limitaciones, ...privacidad].map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}

const METRICAS_CLASIFICADOR: readonly (readonly [string, string, (v: number) => string])[] = [
  ["roc_auc", "ROC AUC", formatearDecimal],
  ["pr_auc", "PR AUC", formatearDecimal],
  ["brier", "Brier", formatearDecimal],
  ["log_loss", "Log loss", formatearDecimal],
  ["ece", "ECE", formatearDecimal],
];
const METRICAS_MONTO: readonly (readonly [string, string, (v: number) => string])[] = [
  ["mae", "MAE", formatearMonto],
  ["rmse", "RMSE", formatearMonto],
  ["real_medio", "Real medio", formatearMonto],
  ["predicho_medio", "Predicho medio", formatearMonto],
  ["suma_real", "Suma real", formatearMonto],
  ["suma_predicha", "Suma predicha", formatearMonto],
  ["razon_sumas", "Predicho / real", formatearDecimal],
];

function intervalo(c: ComponentePredictivo, metrica: string): Registro | null {
  return comoRegistro(comoRegistro(c.bootstrap_test["intervalos"])?.[metrica]);
}

function VistaDesempeno({ modelo }: { readonly modelo: ModeloPredictivo }) {
  return (
    <>
      <p className="nota">
        Las métricas de TEST de cada componente contra el baseline constante de TRAIN, con sus
        intervalos bootstrap por cuenta y sus compuertas.
      </p>
      {modelo.componentes.map((c) => {
        const lista = clasificador(c) ? METRICAS_CLASIFICADOR : METRICAS_MONTO;
        const extras = clasificador(c)
          ? Object.keys(c.metricas_test).filter(
              (k) => k.startsWith("lift_") || k.startsWith("captura_"),
            )
          : [];
        return (
          <section key={c.target} className="tarjeta" aria-labelledby={`desempeno-${c.target}`}>
            <h2 id={`desempeno-${c.target}`}>
              {c.target} <InsigniaEstado estado={c.estado} />
            </h2>
            <div
              className="tabla-contenedor"
              role="region"
              aria-label={`Métricas de ${c.target}`}
              tabIndex={0}
            >
              <table className="tabla tabla--compacta">
                <caption>TEST contra baseline</caption>
                <thead>
                  <tr>
                    <th scope="col">Métrica</th>
                    <th scope="col" className="tabla__numerica">
                      Modelo
                    </th>
                    <th scope="col" className="tabla__numerica">
                      Baseline
                    </th>
                    <th scope="col">Intervalo bootstrap</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ...lista,
                    ...extras.map((k) => [k, etiquetaDeCodigo(k), formatearDecimal] as const),
                  ].map(([llave, etiqueta, formatear]) => {
                    const i = intervalo(c, llave);
                    const inferior = comoNumero(i?.["inferior"]);
                    const superior = comoNumero(i?.["superior"]);
                    return (
                      <tr key={llave}>
                        <th scope="row">{etiqueta}</th>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(c.metricas_test[llave])}
                            formatear={formatear}
                          />
                        </td>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(c.baseline_test[llave])}
                            formatear={formatear}
                          />
                        </td>
                        <td>
                          {inferior !== null && superior !== null
                            ? `${formatear(inferior)} a ${formatear(superior)}`
                            : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <h3>Compuertas</h3>
            <ul className="lista-compacta">
              {c.compuertas.map((g, i) => {
                const r = comoRegistro(g);
                return (
                  <li key={i}>
                    <InsigniaEstado estado={r?.["ok"] === true ? "OK" : "FALLO"} />{" "}
                    {etiquetaDeCodigo(textoDe(r?.["compuerta"]))}: {textoDe(r?.["valor"], "—")}{" "}
                    {textoDe(r?.["operador"])} {textoDe(r?.["umbral"])}
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </>
  );
}

function VistaCalibracion({ modelo }: { readonly modelo: ModeloPredictivo }) {
  const clasificadores = modelo.componentes.filter(clasificador);
  return (
    <>
      <p className="nota">
        La predicción media de cada bin de TEST contra la frecuencia observada: los puntos que
        publicó el entrenamiento, sin suavizar.
      </p>
      <div className="rejilla rejilla--dos">
        {clasificadores.map((c) => {
          const bins = comoLista(c.metricas_test["tabla_calibracion"])
            .map(comoRegistro)
            .filter((b): b is Registro => b !== null);
          const puntos = bins.flatMap((b) => {
            const p = comoNumero(b["prediccion_media"]);
            const o = comoNumero(b["frecuencia_observada"]);
            return p === null || o === null ? [] : [{ prediccion: p, observada: o }];
          });
          return (
            <Figura
              key={c.target}
              titulo={`Calibración de ${c.target}`}
              descripcion={`ECE ${formatearDecimal(comoNumero(c.metricas_test["ece"]) ?? Number.NaN)} en TEST, ${formatearEntero(bins.length)} bins de población casi igual.`}
              datos={
                <table className="tabla tabla--compacta">
                  <caption>Bins de calibración</caption>
                  <thead>
                    <tr>
                      <th scope="col">Bin</th>
                      <th scope="col" className="tabla__numerica">
                        Cuentas
                      </th>
                      <th scope="col" className="tabla__numerica">
                        Predicción media
                      </th>
                      <th scope="col" className="tabla__numerica">
                        Frecuencia observada
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {bins.map((b, i) => (
                      <tr key={i}>
                        <th scope="row">{textoDe(b["bin"], String(i + 1))}</th>
                        <td className="tabla__numerica">
                          <ValorOpcional valor={comoNumero(b["n"])} formatear={formatearEntero} />
                        </td>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(b["prediccion_media"])}
                            formatear={formatearPorcentaje}
                          />
                        </td>
                        <td className="tabla__numerica">
                          <ValorOpcional
                            valor={comoNumero(b["frecuencia_observada"])}
                            formatear={formatearPorcentaje}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              }
            >
              {puntos.length > 0 ? (
                <Calibracion titulo={`Calibración de ${c.target}`} puntos={puntos} />
              ) : (
                <p className="vacio">El componente no publicó su tabla de calibración.</p>
              )}
            </Figura>
          );
        })}
      </div>
    </>
  );
}

const ORDENES_PREDICCION = [
  "CUENTA",
  "P_RECUPERACION_POSITIVA_30D",
  "P_CONTACTO_14D",
  "P_PROMESA_30D",
  "RIESGO_SIN_RECUPERACION_30D",
  "RECUPERACION_POSITIVA_ESPERADA_30D",
] as const;

function VistaScoring({ id, contexto }: { readonly id: string; readonly contexto: Contexto }) {
  const scoring = useScoring(id);
  const distribucion = useDistribucion(id);
  const [busqueda, setBusqueda] = useSearchParams();
  const porPagina = Number(busqueda.get("por_pagina") ?? "25");
  const orden = ORDENES_PREDICCION.find((o) => o === busqueda.get("orden")) ?? "CUENTA";
  const estado = busqueda.get("estado");
  const filtros: FiltrosDePredicciones = {
    pagina: Math.max(1, Number(busqueda.get("pagina") ?? "1") || 1),
    por_pagina: (TAMANOS_DE_PAGINA as readonly number[]).includes(porPagina) ? porPagina : 25,
    orden,
    sentido: busqueda.get("sentido") === "DESC" ? "DESC" : "ASC",
    ...(estado === "COMPLETA" || estado === "CON_IMPUTACION" ? { estado } : {}),
  };
  const predicciones = usePredicciones(id, filtros);

  function cambiar(cambios: Record<string, string | number | undefined>) {
    const nueva = new URLSearchParams(busqueda);
    for (const [llave, valor] of Object.entries(cambios)) {
      if (valor === undefined || valor === "") nueva.delete(llave);
      else nueva.set(llave, String(valor));
    }
    if (!("pagina" in cambios)) nueva.delete("pagina");
    setBusqueda(nueva);
  }

  return (
    <>
      <Resultado consulta={scoring} que="el scoring">
        {(s) => (
          <section className="tarjeta" aria-labelledby="scoring-ficha">
            <h2 id="scoring-ficha">Scoring</h2>
            <Fuente nombre="scoring" runId={s.scoring_run_id} estado={s.estado} />
            <Ficha
              etiqueta="El scoring"
              filas={[
                ["Modo", s.modo],
                ["as_of", formatearFecha(s.as_of)],
                ["known_at", formatearInstante(s.known_at, s.zona_horaria)],
                ["Cuentas objetivo", formatearEntero(s.cuentas_objetivo)],
                ["Predicciones publicadas", formatearEntero(s.predicciones_publicadas)],
                ["Features", `${s.version_features} · ${abreviar(s.hash_features, 12)}`],
                [
                  "Hash de las predicciones",
                  s.hash_predicciones ? (
                    <Copiable
                      key="h"
                      valor={s.hash_predicciones}
                      etiqueta="el hash de las predicciones"
                      largo={12}
                    />
                  ) : (
                    "—"
                  ),
                ],
                [
                  "Firma",
                  <Copiable
                    key="f"
                    valor={s.firma_inputs}
                    etiqueta="la firma del scoring"
                    largo={12}
                  />,
                ],
              ]}
            />
          </section>
        )}
      </Resultado>
      <Resultado consulta={distribucion} que="la distribución de las predicciones">
        {(d) => (
          <div className="rejilla rejilla--tres">
            {d.histogramas.map((h) => (
              <Figura
                key={h.salida}
                titulo={h.salida}
                descripcion={`Predicciones por décimo de [0, 1], contadas en la base, de ${formatearEntero(d.publicadas)}.`}
                datos={<TablaHistograma cuentas={h.cuentas} />}
              >
                <Histograma
                  titulo={h.salida}
                  columnas={h.cuentas.map((n, i) => ({ categoria: decimo(i), valor: n }))}
                  formatear={formatearEntero}
                />
              </Figura>
            ))}
          </div>
        )}
      </Resultado>
      <section className="tarjeta" aria-labelledby="predicciones">
        <h2 id="predicciones">Predicciones por cuenta</h2>
        <form
          className="filtros"
          aria-label="Filtros de predicciones"
          onSubmit={(evento) => {
            evento.preventDefault();
          }}
        >
          <SelectorSimple
            etiqueta="Estado"
            valor={estado ?? ""}
            opciones={["COMPLETA", "CON_IMPUTACION"]}
            onCambio={(v) => {
              cambiar({ estado: v });
            }}
          />
        </form>
        <Resultado consulta={predicciones} que="las predicciones">
          {(p) => (
            <TablaDePredicciones
              pagina={p}
              contexto={contexto}
              orden={{ columna: orden, descendente: filtros.sentido === "DESC" }}
              onOrden={(o) => {
                cambiar({ orden: o.columna, sentido: o.descendente ? "DESC" : "ASC" });
              }}
              onPagina={(n) => {
                cambiar({ pagina: n });
              }}
              onPorPagina={(n) => {
                cambiar({ por_pagina: n });
              }}
            />
          )}
        </Resultado>
      </section>
    </>
  );
}

const columnaPrediccion = createColumnHelper<PaginaPredicciones["elementos"][number]>();

function TablaDePredicciones({
  pagina,
  contexto,
  orden,
  onOrden,
  onPagina,
  onPorPagina,
}: {
  readonly pagina: PaginaPredicciones;
  readonly contexto: Contexto;
  readonly orden: { columna: string; descendente: boolean };
  readonly onOrden: (o: { columna: string; descendente: boolean }) => void;
  readonly onPagina: (p: number) => void;
  readonly onPorPagina: (p: number) => void;
}) {
  return (
    <>
      <TablaDatos
        titulo="Predicciones publicadas, ordenadas en el servidor"
        filas={pagina.elementos}
        claveDeFila={(p) => p.cuenta_id}
        orden={orden}
        onOrden={onOrden}
        columnas={[
          columnaPrediccion.display({
            id: "cuenta",
            header: "Cuenta",
            meta: { orden: "CUENTA" },
            cell: ({ row }) => (
              <Link
                to={rutaConContexto(
                  `/cuentas/${encodeURIComponent(row.original.cuenta_id)}/predictivo`,
                  contexto,
                )}
              >
                <code>{abreviar(row.original.cuenta_id, 13)}</code>
              </Link>
            ),
          }),
          columnaPrediccion.display({
            id: "producto",
            header: "Producto",
            cell: ({ row }) => row.original.producto,
          }),
          columnaPrediccion.display({
            id: "p_rec",
            header: "p recuperación 30 d",
            meta: { orden: "P_RECUPERACION_POSITIVA_30D", numerica: true },
            cell: ({ row }) => formatearPorcentaje(row.original.p_recuperacion_positiva_30d),
          }),
          columnaPrediccion.display({
            id: "p_con",
            header: "p contacto 14 d",
            meta: { orden: "P_CONTACTO_14D", numerica: true },
            cell: ({ row }) => formatearPorcentaje(row.original.p_contacto_14d),
          }),
          columnaPrediccion.display({
            id: "p_pro",
            header: "p promesa 30 d",
            meta: { orden: "P_PROMESA_30D", numerica: true },
            cell: ({ row }) => formatearPorcentaje(row.original.p_promesa_30d),
          }),
          columnaPrediccion.display({
            id: "riesgo",
            header: "Riesgo sin recuperación",
            meta: { orden: "RIESGO_SIN_RECUPERACION_30D", numerica: true },
            cell: ({ row }) => formatearPorcentaje(row.original.riesgo_sin_recuperacion_30d),
          }),
          columnaPrediccion.display({
            id: "esperada",
            header: "Recuperación positiva esperada",
            meta: { orden: "RECUPERACION_POSITIVA_ESPERADA_30D", numerica: true },
            cell: ({ row }) => formatearMonto(row.original.recuperacion_positiva_esperada_30d),
          }),
          columnaPrediccion.display({
            id: "estado",
            header: "Estado",
            cell: ({ row }) => <InsigniaEstado estado={row.original.estado} />,
          }),
        ]}
      />
      <Paginacion
        total={pagina.total}
        pagina={pagina.pagina}
        porPagina={pagina.por_pagina}
        onPagina={onPagina}
        onPorPagina={onPorPagina}
        etiqueta="las predicciones"
      />
    </>
  );
}

type OrdenDrift = "psi" | "nombre" | "faltantes_scoring";

function VistaDrift({ id }: { readonly id: string }) {
  const drift = useDrift(id);
  const [orden, setOrden] = useState<{ columna: OrdenDrift; descendente: boolean }>({
    columna: "psi",
    descendente: true,
  });
  return (
    <>
      <Aviso tono="aviso">{AVISO_DRIFT}</Aviso>
      <Resultado consulta={drift} que="el drift">
        {(d) => {
          const resumen = comoRegistro(d.resumen);
          const severidades = conteosDe(resumen?.["feature"]);
          // La lista entera de una vez (unas decenas de filas, no paginada): ordenarla es solo
          // presentarla.
          const filas = [...d.filas].sort(
            (a, b) => comparar(a, b, orden.columna) * (orden.descendente ? -1 : 1),
          );
          return (
            <>
              <Figura
                titulo="Features por severidad de drift"
                descripcion="Cuántas features quedaron en cada severidad del PSI contra TRAIN, como lo publicó el scoring."
                datos={
                  <table className="tabla tabla--compacta">
                    <caption>Severidad</caption>
                    <thead>
                      <tr>
                        <th scope="col">Severidad</th>
                        <th scope="col" className="tabla__numerica">
                          Features
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {severidades.map((s) => (
                        <tr key={s.clave}>
                          <th scope="row">
                            <InsigniaEstado estado={s.clave} />
                          </th>
                          <td className="tabla__numerica">{formatearEntero(s.valor)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
              >
                <Barras
                  titulo="Features por severidad"
                  barras={["BAJO", "MODERADO", "ALTO"].map((s) => ({
                    categoria: etiquetaDeCodigo(s),
                    valor: severidades.find((x) => x.clave === s)?.valor ?? 0,
                  }))}
                  formatear={formatearEntero}
                />
              </Figura>
              <TablaDatos
                titulo="PSI por feature y por salida, contra TRAIN"
                filas={filas}
                claveDeFila={(f) => `${f.tipo}-${f.nombre}`}
                orden={orden}
                onOrden={(o) => {
                  setOrden({ columna: o.columna as OrdenDrift, descendente: o.descendente });
                }}
                columnas={[
                  columnaDrift.display({
                    id: "nombre",
                    header: "Feature o salida",
                    meta: { orden: "nombre" },
                    cell: ({ row }) => <code>{row.original.nombre}</code>,
                  }),
                  columnaDrift.display({
                    id: "tipo",
                    header: "Tipo",
                    cell: ({ row }) => row.original.tipo,
                  }),
                  columnaDrift.display({
                    id: "psi",
                    header: "PSI",
                    meta: { orden: "psi", numerica: true },
                    cell: ({ row }) => formatearDecimal(row.original.psi),
                  }),
                  columnaDrift.display({
                    id: "severidad",
                    header: "Severidad",
                    cell: ({ row }) => <InsigniaEstado estado={row.original.severidad} />,
                  }),
                  columnaDrift.display({
                    id: "faltantes_train",
                    header: "Faltantes en TRAIN",
                    meta: { numerica: true },
                    cell: ({ row }) => (
                      <ValorOpcional
                        valor={row.original.faltantes_train}
                        formatear={formatearPorcentaje}
                      />
                    ),
                  }),
                  columnaDrift.display({
                    id: "faltantes_scoring",
                    header: "Faltantes en el scoring",
                    meta: { orden: "faltantes_scoring", numerica: true },
                    cell: ({ row }) => (
                      <ValorOpcional
                        valor={row.original.faltantes_scoring}
                        formatear={formatearPorcentaje}
                      />
                    ),
                  }),
                  columnaDrift.display({
                    id: "severidad_faltantes",
                    header: "Severidad de faltantes",
                    cell: ({ row }) =>
                      row.original.severidad_faltantes ? (
                        <InsigniaEstado estado={row.original.severidad_faltantes} />
                      ) : (
                        "—"
                      ),
                  }),
                ]}
              />
            </>
          );
        }}
      </Resultado>
    </>
  );
}

const columnaDrift = createColumnHelper<FilaDeDrift>();

function comparar(a: FilaDeDrift, b: FilaDeDrift, columna: OrdenDrift): number {
  if (columna === "nombre") return a.nombre.localeCompare(b.nombre);
  const x = comoNumero(a[columna]) ?? -1;
  const y = comoNumero(b[columna]) ?? -1;
  return x - y;
}

function SelectorSimple({
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
