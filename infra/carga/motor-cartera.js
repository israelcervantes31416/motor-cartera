// Las pruebas de carga de motor-cartera (k6), con su perfil y sus umbrales registrados antes de
// correrlas: docs/carga.md. Las corre scripts/carga.py en un contenedor de k6 dentro de la red del
// compose de produccion; nada se publica al host para esto.
//
// MC_CARGA_ESCENARIO elige uno:
//   api     directo a la API por la red interna, con la clave de servicio de lectura: la mezcla de
//           lecturas de la app a una tasa constante.
//   saldo   el orden caro, aparte: las cuentas de un corte ordenadas por saldo, paginas al azar.
//   edge    la misma mezcla por el edge: TLS, la cookie de sesion, auth_request a oauth2-proxy y su
//           almacen de sesiones. Comparada con `api` a la misma tasa, es el costo de la seguridad.
//   limite  mas lecturas por segundo de las que el edge deja pasar a una IP: las de mas son 429 con
//           Retry-After, y ninguna es un 5xx.
//
// Las credenciales llegan en archivos montados de solo lectura (/secretos), nunca en el entorno.

import http from "k6/http";
import { check, fail } from "k6";
import { Counter, Rate } from "k6/metrics";

const ESCENARIO = __ENV.MC_CARGA_ESCENARIO || "api";
const TASA = Number(__ENV.MC_CARGA_TASA || "25");
const DURACION = __ENV.MC_CARGA_DURACION || "60s";
const EDGE = ESCENARIO === "edge" || ESCENARIO === "limite";
const BASE = EDGE ? "https://motor-cartera.localhost:8443/api" : "http://api:8000";
const CABECERAS = EDGE
  ? { Cookie: `__Host-mc_sesion=${open("/secretos/sesion").trim()}` }
  : { "X-API-Key": open("/secretos/clave").trim() };

const respuestas5xx = new Counter("respuestas_5xx");
const limitadas = new Rate("limitadas");

function escenario() {
  if (ESCENARIO === "saldo") {
    return {
      executor: "constant-arrival-rate",
      rate: TASA,
      timeUnit: "1s",
      duration: DURACION,
      preAllocatedVUs: 10,
      maxVUs: 40,
    };
  }
  if (ESCENARIO === "limite") {
    return {
      executor: "constant-arrival-rate",
      rate: TASA,
      timeUnit: "1s",
      duration: DURACION,
      preAllocatedVUs: 20,
      maxVUs: 60,
    };
  }
  return {
    executor: "constant-arrival-rate",
    rate: TASA,
    timeUnit: "1s",
    duration: DURACION,
    preAllocatedVUs: 20,
    maxVUs: 100,
  };
}

const UMBRALES = {
  api: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500", "p(99)<1500"],
    checks: ["rate>0.99"],
  },
  saldo: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1500", "p(99)<3000"],
    checks: ["rate>0.99"],
  },
  edge: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<800", "p(99)<2000"],
    checks: ["rate>0.99"],
  },
  limite: {
    respuestas_5xx: ["count==0"],
    limitadas: ["rate>0.2"],
    checks: ["rate>0.99"],
  },
};

export const options = {
  scenarios: { [ESCENARIO]: escenario() },
  thresholds: UMBRALES[ESCENARIO],
  // Las peticiones del setup (encontrar un contexto) no cuentan en los umbrales.
  setupTimeout: "60s",
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
  // El edge se verifica con la CA del compose (SSL_CERT_FILE); nunca se ignora un certificado.
  insecureSkipTLSVerify: false,
};

function leer(ruta) {
  const respuesta = http.get(`${BASE}${ruta}`, {
    headers: CABECERAS,
    tags: { name: "setup" },
  });
  if (respuesta.status !== 200) fail(`${ruta}: HTTP ${respuesta.status}`);
  return respuesta.json();
}

// Un corte con una sugerencia que declara la decision y analytics, y una cuenta de ese corte: lo
// que la base tiene, sin inventar nada (igual que la preparacion de las pruebas E2E).
export function setup() {
  const inicio = leer("/app/v1/contextos/opciones?por_pagina=1");
  for (const { cartera_id } of inicio.carteras) {
    const cortes = leer(`/app/v1/contextos/opciones?cartera_id=${cartera_id}&por_pagina=100`);
    for (const { corte_id } of cortes.cortes) {
      const opciones = leer(`/app/v1/contextos/opciones?corte_id=${corte_id}`);
      const sugerencia = opciones.sugerencias.find(
        (s) => s.contexto.decision_run_id && s.contexto.analytics_run_id,
      );
      if (!sugerencia) continue;
      // Solo las ejecuciones: el explorador no acepta ningun otro parametro (extra="forbid").
      const contexto = Object.entries(sugerencia.contexto)
        .filter(([llave, valor]) => llave.endsWith("_run_id") && typeof valor === "string")
        .map(([llave, valor]) => `${llave}=${valor}`)
        .join("&");
      const consulta = `corte_id=${corte_id}&${contexto}`;
      const cuentas = leer(`/app/v1/cuentas?${consulta}&por_pagina=100`);
      if (cuentas.elementos.length === 0) continue;
      return {
        corte: corte_id,
        consulta,
        cuentas: cuentas.elementos.map((c) => c.cuenta_id),
        paginas: Math.max(1, Math.ceil(cuentas.total / 50)),
      };
    }
  }
  fail("La base no tiene un corte con decision y analytics: corre antes la prueba de humo.");
}

function azar(lista) {
  return lista[Math.floor(Math.random() * lista.length)];
}

// La mezcla de lecturas de la app: el selector, el explorador, la Cuenta 360, el resumen (caro) y el
// catalogo de analytics.
function mezcla(datos) {
  const r = Math.random();
  if (r < 0.3) return ["opciones", `/app/v1/contextos/opciones?corte_id=${datos.corte}`];
  if (r < 0.5) return ["cuentas", `/app/v1/cuentas?${datos.consulta}&por_pagina=50`];
  if (r < 0.7) return ["cuenta_360", `/app/v1/cuentas/${azar(datos.cuentas)}?${datos.consulta}`];
  if (r < 0.85) return ["resumen", `/app/v1/resumen?${datos.consulta}`];
  return ["catalogo", "/analytics/catalogo"];
}

export default function (datos) {
  let nombre;
  let ruta;
  if (ESCENARIO === "saldo") {
    const pagina = 1 + Math.floor(Math.random() * datos.paginas);
    nombre = "cuentas_por_saldo";
    ruta = `/app/v1/cuentas?${datos.consulta}&orden=SALDO_TOTAL&sentido=DESC&pagina=${pagina}`;
  } else if (ESCENARIO === "limite") {
    nombre = "opciones";
    ruta = `/app/v1/contextos/opciones?corte_id=${datos.corte}`;
  } else {
    [nombre, ruta] = mezcla(datos);
  }
  const respuesta = http.get(`${BASE}${ruta}`, {
    headers: CABECERAS,
    tags: { name: nombre },
    responseCallback:
      ESCENARIO === "limite" ? http.expectedStatuses(200, 429) : http.expectedStatuses(200),
  });
  if (respuesta.status >= 500) respuestas5xx.add(1);
  if (ESCENARIO === "limite") {
    limitadas.add(respuesta.status === 429);
    check(respuesta, {
      "200, o 429 con Retry-After": (r) =>
        r.status === 200 || (r.status === 429 && r.headers["Retry-After"] !== undefined),
    });
  } else {
    check(respuesta, { "200": (r) => r.status === 200 });
  }
}
