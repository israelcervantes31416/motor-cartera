/**
 * La API de las pruebas: MSW con las respuestas capturadas de la API real (capturas.ts), para cada
 * ruta que la app lee. Cada prueba puede reemplazar un manejador con `servidor.use(...)`.
 *
 * Los manejadores no calculan nada de negocio: devuelven la captura, la recortan al contexto que se
 * pidio (lo que no se declara es NO_DECLARADA, como en la API) o responden el error que la API
 * responderia. `peticiones` guarda cada URL pedida, para revisar filtros y paginacion.
 */
import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";

import type { Respuesta } from "../api/cliente";
import {
  EJECUCIONES,
  PARAMETROS,
  TIPO_DE_EJECUCION,
  type ClaveDeEjecucion,
  type Contexto,
} from "../lib/contexto";
import * as c from "./capturas";

export const peticiones: URL[] = [];

/** El contexto de las capturas: el corte y las seis ejecuciones de la sugerencia completa. */
export const CONTEXTO: Required<Contexto> = {
  corte: c.validado.corte.corte_id,
  decision: c.validado.declaradas["DECISION_V2"] ?? "",
  analytics: c.validado.declaradas["ANALYTICS"] ?? "",
  scoring: c.validado.declaradas["SCORING"] ?? "",
  optimizacion: c.validado.declaradas["OPTIMIZACION"] ?? "",
  campo: c.validado.declaradas["CAMPO_V2"] ?? "",
  ruteo: c.validado.declaradas["RUTEO_V2"] ?? "",
};

export const CUENTA = c.cuenta.cuenta_id;
export const RUTA = c.ruta.ruta_id;
export const NO_EXISTE = "00000000-0000-4000-8000-00000000dead";

export function errorDeApi(estado: number, codigo: string, mensaje = "La API dijo que no.") {
  return HttpResponse.json(
    { codigo, mensaje, detalles: [], run_id: null, pagos_run_id: null },
    { status: estado },
  );
}

function responder(datos: JsonBodyType) {
  return HttpResponse.json(datos);
}

function anotar(request: Request): URL {
  const url = new URL(request.url);
  peticiones.push(url);
  return url;
}

/** Que ejecuciones declara una peticion de la API (corte_id y cada *_run_id). */
function declaradas(url: URL): Partial<Record<ClaveDeEjecucion, string>> {
  const resultado: Partial<Record<ClaveDeEjecucion, string>> = {};
  for (const clave of EJECUCIONES) {
    const valor = url.searchParams.get(PARAMETROS[clave]);
    if (valor !== null) resultado[clave] = valor;
  }
  return resultado;
}

/** La validacion de la API para el contexto pedido, armada con la captura: solo lo declarado, y un
 * identificador que no es el de la captura no existe. */
export function validadoPara(url: URL): Respuesta<"/app/v1/contextos/validar"> {
  const pedidas = declaradas(url);
  const conocidas = Object.entries(pedidas).filter(([clave, valor]) => {
    return CONTEXTO[clave as ClaveDeEjecucion] === valor;
  });
  const tipos = new Set(conocidas.map(([clave]) => TIPO_DE_EJECUCION[clave as ClaveDeEjecucion]));
  const faltan = Object.entries(pedidas).filter(([clave, valor]) => {
    return CONTEXTO[clave as ClaveDeEjecucion] !== valor;
  });
  return {
    ...c.validado,
    compatible: faltan.length === 0,
    declaradas: Object.fromEntries(
      Object.entries(pedidas).map(([clave, valor]) => [
        TIPO_DE_EJECUCION[clave as ClaveDeEjecucion],
        valor,
      ]),
    ),
    ejecuciones: c.validado.ejecuciones.filter((e) => tipos.has(e.tipo)),
    problemas: faltan.map(([clave, valor]) => ({
      codigo: "EJECUCION_NO_ENCONTRADA",
      ejecuciones: [TIPO_DE_EJECUCION[clave as ClaveDeEjecucion]],
      mensaje: `No existe la ejecucion ${valor}.`,
    })),
  };
}

/** Un bloque del resumen cuya ejecucion no se declaro: NO_DECLARADA y sin un solo valor. */
export function noDeclarado<T extends { readonly estado_fuente: string }>(bloque: T): T {
  const vacio = Object.fromEntries(
    Object.entries(bloque).map(([llave, valor]) => [llave, Array.isArray(valor) ? [] : null]),
  );
  return { ...vacio, estado_fuente: "NO_DECLARADA" } as T;
}

export function resumenPara(url: URL): Respuesta<"/app/v1/resumen"> {
  const pedidas = declaradas(url);
  const r = c.resumen;
  return {
    ...r,
    analytics: pedidas.analytics !== undefined ? r.analytics : noDeclarado(r.analytics),
    decision: pedidas.decision !== undefined ? r.decision : noDeclarado(r.decision),
    predictivo: pedidas.scoring !== undefined ? r.predictivo : noDeclarado(r.predictivo),
    optimizacion: pedidas.optimizacion !== undefined ? r.optimizacion : noDeclarado(r.optimizacion),
    campo: pedidas.campo !== undefined ? r.campo : noDeclarado(r.campo),
    ruteo: pedidas.ruteo !== undefined ? r.ruteo : noDeclarado(r.ruteo),
  };
}

/** Una pagina de la captura con la pagina y el tamano que se pidieron: las mismas filas. */
function paginaPedida<T extends { pagina: number; por_pagina: number }>(datos: T, url: URL): T {
  return {
    ...datos,
    pagina: Number(url.searchParams.get("pagina") ?? "1"),
    por_pagina: Number(url.searchParams.get("por_pagina") ?? String(datos.por_pagina)),
  };
}

function deLaCuenta(id: string | readonly string[] | undefined, datos: JsonBodyType) {
  return id === CUENTA ? responder(datos) : errorDeApi(404, "CUENTA_NO_ENCONTRADA", "No existe.");
}

export const manejadores = [
  http.get("/api/app/v1/contextos/opciones", ({ request }) => {
    const url = anotar(request);
    return responder(url.searchParams.has("corte_id") ? c.opciones : c.opcionesSinCorte);
  }),
  http.get("/api/app/v1/contextos/validar", ({ request }) => {
    const url = anotar(request);
    if (url.searchParams.get("corte_id") !== CONTEXTO.corte) {
      return errorDeApi(404, "CORTE_NO_ENCONTRADO", "No existe el corte.");
    }
    return responder(validadoPara(url));
  }),
  http.get("/api/app/v1/resumen", ({ request }) => responder(resumenPara(anotar(request)))),
  http.get("/api/app/v1/cuentas", ({ request }) =>
    responder(paginaPedida(c.cuentas, anotar(request))),
  ),
  http.get("/api/app/v1/cuentas/:cuenta", ({ request, params }) => {
    anotar(request);
    return deLaCuenta(params["cuenta"], c.cuenta);
  }),
  http.get("/api/app/v1/cuentas/:cuenta/decision", ({ request, params }) => {
    anotar(request);
    return deLaCuenta(params["cuenta"], c.decisionDeCuenta);
  }),
  http.get("/api/app/v1/cuentas/:cuenta/historia", ({ request, params }) => {
    anotar(request);
    return deLaCuenta(params["cuenta"], c.historiaDeCuenta);
  }),
  http.get("/api/app/v1/cuentas/:cuenta/linea-de-tiempo", ({ request, params }) => {
    anotar(request);
    return deLaCuenta(params["cuenta"], c.lineaDeTiempo);
  }),
  http.get("/api/app/v1/cuentas/:cuenta/pagos", ({ request, params }) => {
    anotar(request);
    return deLaCuenta(params["cuenta"], c.pagosDeCuenta);
  }),
  http.get("/api/app/v1/cuentas/:cuenta/gestiones", ({ request, params }) => {
    anotar(request);
    return deLaCuenta(params["cuenta"], c.gestionesDeCuenta);
  }),
  http.get("/api/app/v1/ruteo/rutas/:ruta/paradas", ({ request }) => {
    anotar(request);
    return responder(c.paradas);
  }),
  http.get("/api/app/v1/linaje", ({ request }) => {
    anotar(request);
    return responder(c.linaje);
  }),
  http.get("/api/app/v1/predictivo/scorings/:scoring/distribucion", ({ request }) => {
    anotar(request);
    return responder(c.distribucion);
  }),
  http.get("/api/analytics/catalogo", ({ request }) => {
    anotar(request);
    return responder(c.catalogo);
  }),
  http.get("/api/analytics/ejecuciones/:analytics", ({ request }) => {
    anotar(request);
    return responder(c.ejecucionAnalytics);
  }),
  http.get("/api/analytics/ejecuciones/:analytics/agregados", ({ request }) => {
    const url = anotar(request);
    return responder(url.searchParams.get("vista") === "PERIODO" ? c.serie : c.categorias);
  }),
  http.get("/api/analytics/ejecuciones/:analytics/embudos", ({ request }) => {
    anotar(request);
    return responder(c.embudos);
  }),
  http.get("/api/analytics/ejecuciones/:analytics/controles", ({ request }) => {
    anotar(request);
    return responder(c.controles);
  }),
  http.get("/api/predictivo/modelos/:modelo", ({ request }) => {
    anotar(request);
    return responder(c.modelo);
  }),
  http.get("/api/predictivo/scorings/:scoring", ({ request }) => {
    anotar(request);
    return responder(c.scoring);
  }),
  http.get("/api/predictivo/scorings/:scoring/drift", ({ request }) => {
    anotar(request);
    return responder(c.drift);
  }),
  http.get("/api/predictivo/scorings/:scoring/predicciones", ({ request }) =>
    responder(paginaPedida(c.predicciones, anotar(request))),
  ),
  http.get("/api/predictivo/scorings/:scoring/cuentas/:cuenta/explicacion", ({ request }) => {
    anotar(request);
    return responder(c.explicacionPredictiva);
  }),
  http.get("/api/optimizacion/ejecuciones/:optimizacion", ({ request }) => {
    anotar(request);
    return responder(c.optimizacion);
  }),
  http.get("/api/optimizacion/ejecuciones/:optimizacion/capacidad", ({ request }) => {
    anotar(request);
    return responder(c.capacidad);
  }),
  http.get("/api/optimizacion/ejecuciones/:optimizacion/restricciones", ({ request }) => {
    anotar(request);
    return responder(c.restricciones);
  }),
  http.get("/api/optimizacion/ejecuciones/:optimizacion/resultados", ({ request }) =>
    responder(paginaPedida(c.resultados, anotar(request))),
  ),
  http.get("/api/optimizacion/ejecuciones/:optimizacion/diagnostico", ({ request }) => {
    anotar(request);
    return responder(c.diagnostico);
  }),
  http.get("/api/optimizacion/ejecuciones/:optimizacion/cuentas/:cuenta", ({ request }) => {
    anotar(request);
    return responder(c.explicacionOptimizacion);
  }),
  http.get("/api/campo-v2/planes/:campo", ({ request }) => {
    anotar(request);
    return responder(c.planCampo);
  }),
  http.get("/api/campo-v2/planes/:campo/cargas", ({ request }) => {
    anotar(request);
    return responder(c.cargas);
  }),
  http.get("/api/ruteo-v2/ejecuciones/:ruteo", ({ request }) => {
    anotar(request);
    return responder(c.ruteo);
  }),
  http.get("/api/ruteo-v2/ejecuciones/:ruteo/rutas", ({ request }) => {
    anotar(request);
    return responder(c.rutas);
  }),
  http.get("/api/ruteo-v2/rutas/:ruta", ({ request, params }) => {
    anotar(request);
    return params["ruta"] === RUTA
      ? responder(c.ruta)
      : errorDeApi(404, "RUTA_NO_ENCONTRADA", "No existe la ruta.");
  }),
];

export const servidor = setupServer(...manejadores);
