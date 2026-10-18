/**
 * Antes de las pruebas: encuentra, por la misma API que usa la app, un contexto completo (un corte
 * con una sugerencia que declara las seis ejecuciones), una cuenta de campo de ese contexto y una
 * de sus rutas, y los deja en e2e/.contexto.json. Nada se inventa: si la base no los tiene, falla.
 */
import { writeFileSync } from "node:fs";

import { request, type FullConfig } from "@playwright/test";

import { ARCHIVO, type DatosDeLaPrueba } from "./datos";

interface Sugerencia {
  readonly codigo: string;
  readonly contexto: Readonly<Record<string, string | null>>;
}

const EJECUCIONES = [
  "decision_run_id",
  "analytics_run_id",
  "scoring_run_id",
  "optimizacion_run_id",
  "campo_run_id",
  "ruteo_run_id",
] as const;

export default async function preparacion(config: FullConfig): Promise<void> {
  const base = config.projects[0]?.use.baseURL;
  const api = await request.newContext(base !== undefined ? { baseURL: base } : {});
  try {
    const leer = async <T>(ruta: string): Promise<T> => {
      const respuesta = await api.get(`/api${ruta}`);
      if (!respuesta.ok()) throw new Error(`${ruta}: HTTP ${String(respuesta.status())}`);
      return (await respuesta.json()) as T;
    };
    const inicio = await leer<{ carteras: { cartera_id: string }[] }>(
      "/app/v1/contextos/opciones?por_pagina=1",
    );
    for (const { cartera_id } of inicio.carteras) {
      const cortes = await leer<{ cortes: { corte_id: string }[] }>(
        `/app/v1/contextos/opciones?cartera_id=${cartera_id}&por_pagina=100`,
      );
      for (const { corte_id } of cortes.cortes) {
        const opciones = await leer<{ sugerencias: Sugerencia[] }>(
          `/app/v1/contextos/opciones?corte_id=${corte_id}`,
        );
        const completa = opciones.sugerencias.find((s) =>
          EJECUCIONES.every((e) => typeof s.contexto[e] === "string"),
        );
        if (completa === undefined) continue;
        const c = completa.contexto;
        const contexto = {
          corte: corte_id,
          decision: c["decision_run_id"] ?? "",
          analytics: c["analytics_run_id"] ?? "",
          scoring: c["scoring_run_id"] ?? "",
          optimizacion: c["optimizacion_run_id"] ?? "",
          campo: c["campo_run_id"] ?? "",
          ruteo: c["ruteo_run_id"] ?? "",
        };
        const consulta = new URLSearchParams({
          corte_id,
          decision_run_id: contexto.decision,
          canal: "CAMPO",
          por_pagina: "25",
        });
        const cuentas = await leer<{ elementos: { cuenta_id: string }[] }>(
          `/app/v1/cuentas?${consulta.toString()}`,
        );
        const rutas = await leer<{ elementos: { ruta_id: string }[] }>(
          `/ruteo-v2/ejecuciones/${contexto.ruteo}/rutas?por_pagina=1`,
        );
        const cuenta = cuentas.elementos[0]?.cuenta_id;
        const ruta = rutas.elementos[0]?.ruta_id;
        if (cuenta === undefined || ruta === undefined) continue;
        const datos: DatosDeLaPrueba = { cartera: cartera_id, contexto, cuenta, ruta };
        writeFileSync(ARCHIVO, `${JSON.stringify(datos, null, 2)}\n`);
        return;
      }
    }
    throw new Error(
      "La base no tiene un corte con un contexto completo: corre antes la prueba de humo.",
    );
  } finally {
    await api.dispose();
  }
}
