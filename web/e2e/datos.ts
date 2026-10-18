/**
 * Lo que las pruebas comparten: el contexto que encontro la preparacion y una prueba base que
 * falla si la pagina escribio un error en la consola o si una respuesta de /api trae un dato
 * personal.
 */
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { test as base, expect, type Page, type Response } from "@playwright/test";

export const ARCHIVO = fileURLToPath(new URL("./.contexto.json", import.meta.url));

export interface DatosDeLaPrueba {
  readonly cartera: string;
  readonly contexto: {
    readonly corte: string;
    readonly decision: string;
    readonly analytics: string;
    readonly scoring: string;
    readonly optimizacion: string;
    readonly campo: string;
    readonly ruteo: string;
  };
  readonly cuenta: string;
  readonly ruta: string;
}

export function datos(): DatosDeLaPrueba {
  if (!existsSync(ARCHIVO)) throw new Error("Falta e2e/.contexto.json: corre la preparacion.");
  return JSON.parse(readFileSync(ARCHIVO, "utf8")) as DatosDeLaPrueba;
}

export const NO_EXISTE = "00000000-0000-4000-8000-00000000dead";

/** Una ruta de la app con el contexto de la prueba (o otro) y sus parametros. */
export function enContexto(
  ruta: string,
  contexto: Partial<DatosDeLaPrueba["contexto"]> = datos().contexto,
  extra: Record<string, string> = {},
): string {
  const busqueda = new URLSearchParams();
  for (const clave of [
    "corte",
    "decision",
    "analytics",
    "scoring",
    "optimizacion",
    "campo",
    "ruteo",
  ] as const) {
    const valor = contexto[clave];
    if (valor !== undefined && valor !== "") busqueda.set(clave, valor);
  }
  for (const [llave, valor] of Object.entries(extra)) busqueda.set(llave, valor);
  return `${ruta}?${busqueda.toString()}`;
}

/** Valores personales, no palabras (los avisos nombran CLIENTE_UNICO para decir que no se usa). */
export const PERSONALES = [
  /CU\d{10}/,
  /"(cliente_unico|actor_ref|idempotency_key|observacion|telefono|curp|rfc|domicilio|latitud|longitud|lat|lon|lng|geometry|geometria|polyline|polilinea)"\s*:/i,
];

interface Revision {
  readonly respuestas: { url: string; cuerpo: string }[];
}

export const test = base.extend<{ revision: Revision }>({
  revision: [
    async ({ page }, usar) => {
      const errores: string[] = [];
      const respuestas: { url: string; cuerpo: string }[] = [];
      const pendientes: Promise<void>[] = [];
      page.on("console", (mensaje) => {
        // Una respuesta 4xx o 5xx que una prueba provoca a proposito tambien se anota en la
        // consola: lo que se revisa es como la muestra la pantalla, no esa linea.
        if (mensaje.type() !== "error" || mensaje.text().startsWith("Failed to load resource")) {
          return;
        }
        errores.push(mensaje.text());
      });
      page.on("pageerror", (error) => errores.push(error.message));
      page.on("response", (respuesta: Response) => {
        if (!new URL(respuesta.url()).pathname.startsWith("/api/")) return;
        pendientes.push(
          respuesta
            .text()
            .then((cuerpo) => {
              respuestas.push({ url: respuesta.url(), cuerpo });
            })
            .catch(() => undefined),
        );
      });
      await usar({ respuestas });
      await Promise.all(pendientes);
      const personales = respuestas.flatMap(({ url, cuerpo }) =>
        PERSONALES.filter((p) => p.test(cuerpo)).map((p) => `${url}: ${String(p)}`),
      );
      expect(personales, "una respuesta de /api trae un dato personal").toEqual([]);
      expect(errores, "la pagina escribio errores en la consola").toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/** Espera el encabezado principal de una pantalla, despues de que su contenido llego. */
export async function abrir(page: Page, ruta: string, encabezado: string | RegExp): Promise<void> {
  await page.goto(ruta);
  await expect(page.getByRole("heading", { level: 1, name: encabezado })).toBeVisible();
  await expect(page.locator(".cargando")).toHaveCount(0);
}
