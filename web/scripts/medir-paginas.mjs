// Mide las pantallas principales con el build de produccion, en Chromium: cuanto tardan desde la
// navegacion hasta su contenido (su encabezado y ningun "Cargando"), cuantas peticiones de datos
// hacen (/api) y cuantos bytes bajan, de datos y en total. Cada pantalla se abre REPETICIONES veces
// en un contexto nuevo del navegador (sin cache), y se reporta su mediana. Contra una app ya servida
// con su API: el preview (`npm run preview`, en 4173) sobre la API local apuntada a la base del
// benchmark. No corre en el CI.
//
//   node scripts/medir-paginas.mjs [--url http://localhost:4173] [--salida medicion.json]
//
// MC_E2E_CANAL elige el navegador instalado ("chrome" o "msedge") donde no esta el de Playwright.
// Los presupuestos (docs/frontend.md): el resumen hasta 8 peticiones de datos, la Cuenta 360 hasta
// 10 y ninguna pantalla mas de 2 MiB de datos. Sale con 1 si uno se rompe.
import { writeFileSync } from "node:fs";

import { chromium } from "@playwright/test";

const argumentos = new Map(
  process.argv.slice(2).flatMap((a, i, todos) => (a.startsWith("--") ? [[a, todos[i + 1]]] : [])),
);
const URL_BASE = argumentos.get("--url") ?? "http://localhost:4173";
const SALIDA = argumentos.get("--salida");
const REPETICIONES = 5;
const MIB = 1024 * 1024;
const PRESUPUESTOS = { resumen: 8, cuenta: 10, bytesDeDatos: 2 * MIB };
const EJECUCIONES = [
  "decision_run_id",
  "analytics_run_id",
  "scoring_run_id",
  "optimizacion_run_id",
  "campo_run_id",
  "ruteo_run_id",
];

async function leer(ruta) {
  const respuesta = await fetch(`${URL_BASE}/api${ruta}`);
  if (!respuesta.ok) throw new Error(`${ruta}: HTTP ${respuesta.status}`);
  return respuesta.json();
}

/** El contexto completo del corte con mas cuentas, como lo propone el selector, y su cuenta de
 * campo asignada por la optimizacion: lo mismo que mide scripts/benchmark_app.py. */
async function contextoXl() {
  const inicio = await leer("/app/v1/contextos/opciones?por_pagina=1");
  const candidatos = [];
  for (const { cartera_id } of inicio.carteras) {
    const { cortes } = await leer(
      `/app/v1/contextos/opciones?cartera_id=${cartera_id}&por_pagina=100`,
    );
    for (const corte of cortes) {
      const { sugerencias } = await leer(`/app/v1/contextos/opciones?corte_id=${corte.corte_id}`);
      const completa = sugerencias.find(
        (s) => s.codigo === "DESDE_OPTIMIZACION" && EJECUCIONES.every((e) => s.contexto[e]),
      );
      if (completa) candidatos.push({ cuentas: corte.cuentas, contexto: completa.contexto });
    }
  }
  candidatos.sort((a, b) => b.cuentas - a.cuentas);
  const elegido = candidatos[0];
  if (!elegido) throw new Error("La base no tiene un contexto completo.");
  const c = elegido.contexto;
  const enUrl = new URLSearchParams({
    corte: c.corte_id,
    decision: c.decision_run_id,
    analytics: c.analytics_run_id,
    scoring: c.scoring_run_id,
    optimizacion: c.optimizacion_run_id,
    campo: c.campo_run_id,
    ruteo: c.ruteo_run_id,
  }).toString();
  const enApi = new URLSearchParams(Object.entries(c).filter(([, v]) => v)).toString();
  const asignadas = await leer(
    `/app/v1/cuentas?${enApi}&canal=CAMPO&estado_optimizacion=ASIGNADA&por_pagina=1`,
  );
  return { cuentas: elegido.cuentas, enUrl, cuenta: asignadas.elementos[0]?.cuenta_id };
}

function mediana(valores) {
  const ordenados = [...valores].sort((a, b) => a - b);
  const medio = Math.floor(ordenados.length / 2);
  return ordenados.length % 2 ? ordenados[medio] : (ordenados[medio - 1] + ordenados[medio]) / 2;
}

async function medir(navegador, ruta, encabezado) {
  const muestras = [];
  for (let i = 0; i < REPETICIONES; i += 1) {
    const contexto = await navegador.newContext({ viewport: { width: 1440, height: 900 } });
    const pagina = await contexto.newPage();
    const peticiones = [];
    pagina.on("requestfinished", (peticion) => peticiones.push(peticion));
    const inicio = performance.now();
    await pagina.goto(URL_BASE + ruta);
    await pagina.getByRole("heading", { level: 1, name: encabezado }).waitFor();
    // El contenido llego cuando ninguna consulta de la pantalla sigue cargando.
    await pagina.waitForFunction(() => document.querySelectorAll(".cargando").length === 0, null, {
      timeout: 60_000,
    });
    const milisegundos = performance.now() - inicio;
    await pagina.waitForLoadState("networkidle");
    let bytesDeDatos = 0;
    let bytes = 0;
    let deDatos = 0;
    for (const peticion of peticiones) {
      const { responseBodySize, responseHeadersSize } = await peticion.sizes();
      bytes += responseBodySize + responseHeadersSize;
      if (new URL(peticion.url()).pathname.startsWith("/api/")) {
        deDatos += 1;
        bytesDeDatos += responseBodySize;
      }
    }
    muestras.push({ milisegundos, deDatos, bytesDeDatos, bytes, peticiones: peticiones.length });
    await contexto.close();
  }
  return {
    mediana_ms: Math.round(mediana(muestras.map((m) => m.milisegundos))),
    maximo_ms: Math.round(Math.max(...muestras.map((m) => m.milisegundos))),
    peticiones_de_datos: Math.max(...muestras.map((m) => m.deDatos)),
    bytes_de_datos: Math.max(...muestras.map((m) => m.bytesDeDatos)),
    bytes_totales: Math.max(...muestras.map((m) => m.bytes)),
    peticiones: Math.max(...muestras.map((m) => m.peticiones)),
  };
}

const xl = await contextoXl();
const q = xl.enUrl;
const PANTALLAS = [
  ["resumen", `/resumen?${q}`, "Resumen"],
  ["cuentas", `/cuentas?${q}`, "Cuentas"],
  ["cuenta", `/cuentas/${xl.cuenta}?${q}`, /Cuenta 360/],
  ["analytics", `/analytics?${q}`, "Analytics"],
  ["predictivo_modelo", `/predictivo/modelo?${q}`, "Predictivo"],
  ["predictivo_drift", `/predictivo/drift?${q}`, "Predictivo"],
  ["optimizacion_resumen", `/optimizacion/resumen?${q}`, "Optimización"],
  ["optimizacion_asignaciones", `/optimizacion/asignaciones?${q}`, "Optimización"],
  ["campo", `/campo?${q}`, "Campo"],
  ["ruteo", `/ruteo?${q}`, "Ruteo"],
];
const canal = process.env["MC_E2E_CANAL"];
const navegador = await chromium.launch(canal ? { channel: canal } : {});
const resultados = {};
try {
  for (const [nombre, ruta, encabezado] of PANTALLAS) {
    resultados[nombre] = await medir(navegador, ruta, encabezado);
    console.log(nombre, JSON.stringify(resultados[nombre]));
  }
} finally {
  await navegador.close();
}

const rotos = [];
if (resultados.resumen.peticiones_de_datos > PRESUPUESTOS.resumen)
  rotos.push("resumen: peticiones");
if (resultados.cuenta.peticiones_de_datos > PRESUPUESTOS.cuenta) rotos.push("cuenta: peticiones");
for (const [nombre, r] of Object.entries(resultados)) {
  if (r.bytes_de_datos > PRESUPUESTOS.bytesDeDatos) rotos.push(`${nombre}: bytes de datos`);
}
const reporte = {
  url: URL_BASE,
  cuentas_del_corte: xl.cuentas,
  repeticiones: REPETICIONES,
  resultados,
  rotos,
};
if (SALIDA) writeFileSync(SALIDA, `${JSON.stringify(reporte, null, 2)}\n`);
console.log(rotos.length ? `MAL: ${rotos.join("; ")}` : "Todos los presupuestos se cumplen.");
process.exit(rotos.length ? 1 : 0);
