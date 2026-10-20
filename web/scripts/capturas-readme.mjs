// Las capturas del README (docs/assets/): el resumen, la Cuenta 360 y la optimizacion de un corte
// sintetico con su contexto completo, en Chromium, a 1440 x 900. Contra una app ya servida con su
// API y con datos sinteticos: la del compose de desarrollo despues de la prueba de humo completa,
// con los mismos pasos que el job compose del CI (docs/desarrollo.md). No corre en el CI.
//
//   node scripts/capturas-readme.mjs [--url http://localhost:8080] [--destino ../docs/assets]
//
// Elige, como scripts/medir-paginas.mjs, el corte con mas cuentas que tenga un contexto completo (una
// sugerencia DESDE_OPTIMIZACION con cada motor) y, en el, una cuenta de CAMPO que la optimizacion
// asigno. Cada captura espera el encabezado de su pantalla y que nada siga cargando. MC_E2E_CANAL
// elige el navegador instalado ("chrome" o "msedge") donde no esta el de Playwright.
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { chromium } from "@playwright/test";

const argumentos = new Map(
  process.argv.slice(2).flatMap((a, i, todos) => (a.startsWith("--") ? [[a, todos[i + 1]]] : [])),
);
const URL_BASE = argumentos.get("--url") ?? "http://localhost:8080";
const DESTINO = resolve(argumentos.get("--destino") ?? "../docs/assets");
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

/** El contexto completo del corte con mas cuentas, como lo propone el selector, y una cuenta de
 * campo que la optimizacion asigno. */
async function contextoCompleto() {
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
  if (!elegido) throw new Error("La base no tiene un contexto completo: falta la prueba de humo.");
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
  const cuenta = asignadas.elementos[0]?.cuenta_id;
  if (!cuenta) throw new Error("El contexto no tiene una cuenta de CAMPO asignada.");
  return { enUrl, cuenta };
}

const { enUrl: q, cuenta } = await contextoCompleto();
const CAPTURAS = [
  ["overview.png", `/resumen?${q}`, "Resumen"],
  ["cuenta-360.png", `/cuentas/${cuenta}?${q}`, /Cuenta 360/],
  ["optimization.png", `/optimizacion/resumen?${q}`, "Optimización"],
];
mkdirSync(DESTINO, { recursive: true });
const canal = process.env["MC_E2E_CANAL"];
const navegador = await chromium.launch(canal ? { channel: canal } : {});
try {
  const contexto = await navegador.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: "es-MX",
    colorScheme: "light",
    reducedMotion: "reduce",
  });
  for (const [archivo, ruta, encabezado] of CAPTURAS) {
    const pagina = await contexto.newPage();
    await pagina.goto(URL_BASE + ruta);
    await pagina.getByRole("heading", { level: 1, name: encabezado }).waitFor();
    await pagina.waitForFunction(() => document.querySelectorAll(".cargando").length === 0, null, {
      timeout: 60_000,
    });
    await pagina.waitForLoadState("networkidle");
    await pagina.screenshot({ path: resolve(DESTINO, archivo) });
    await pagina.close();
    console.log(`${archivo}: ${ruta.split("?")[0]}`);
  }
} finally {
  await navegador.close();
}
