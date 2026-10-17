/**
 * La preparacion de cada archivo de pruebas: los matchers de jest-dom, el servidor de MSW (una
 * peticion sin manejador es un error, nunca una red real) y lo que jsdom no trae.
 */
import "@testing-library/jest-dom/vitest";

import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { peticiones, servidor } from "./servidor";

// Recharts mide su contenedor con ResizeObserver, que jsdom no trae: sin medidas no dibuja, y las
// pruebas leen la tabla equivalente de cada grafica, no sus pixeles.
class ObservadorDeTamano {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (!("ResizeObserver" in globalThis)) {
  Object.assign(globalThis, { ResizeObserver: ObservadorDeTamano });
}

// La primera pagina diferida que abre un archivo de pruebas se transforma al importarse (Recharts
// incluido): las esperas de Testing Library le dan tiempo, sin cambiar lo que se espera.
configure({ asyncUtilTimeout: 5_000 });

beforeAll(() => {
  servidor.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  cleanup();
  servidor.resetHandlers();
  peticiones.length = 0;
  window.localStorage.clear();
});

afterAll(() => {
  servidor.close();
});
