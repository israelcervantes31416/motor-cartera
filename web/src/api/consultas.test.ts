import { describe, expect, it } from "vitest";

import { ErrorDeApi, ErrorDeRed } from "./cliente";
import { claves, crearClienteDeConsultas, EN_PROCESO_MS, LARGO } from "./consultas";

const CORTE = "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b";
const SCORING_A = "c1397868-406c-431b-ae0a-25a791e574ae";
const SCORING_B = "4c47e1c3-7a1c-4d0b-9a65-2a3c5b1a9d10";

describe("las llaves de cache", () => {
  it("dos contextos distintos nunca comparten una entrada", () => {
    const a = { corte: CORTE, scoring: SCORING_A };
    const b = { corte: CORTE, scoring: SCORING_B };

    expect(claves.resumen(a)).not.toEqual(claves.resumen(b));
    expect(claves.validar(a)).not.toEqual(claves.validar({ corte: CORTE }));
    expect(claves.cuenta("x", a)).not.toEqual(claves.cuenta("x", b));
    expect(claves.linaje(a)).not.toEqual(claves.linaje(b));
  });

  it("llevan los filtros, la pagina y el orden", () => {
    const contexto = { corte: CORTE };
    const primera = claves.cuentas(contexto, { pagina: 1, por_pagina: 50 });
    const segunda = claves.cuentas(contexto, { pagina: 2, por_pagina: 50 });
    const ordenada = claves.cuentas(contexto, { pagina: 1, por_pagina: 50, orden: "SALDO_TOTAL" });

    expect(primera).not.toEqual(segunda);
    expect(primera).not.toEqual(ordenada);
    expect(claves.agregados("a", { metrica: "x" })).not.toEqual(
      claves.agregados("a", { metrica: "y" }),
    );
    expect(claves.resultados("o", { canal: "CAMPO" })).not.toEqual(claves.resultados("o", {}));
  });
});

describe("el cliente de consultas", () => {
  it("guarda mucho tiempo lo publicado, no se refresca solo y reintenta solo lo que vale", () => {
    const opciones = crearClienteDeConsultas().getDefaultOptions().queries;

    expect(opciones?.staleTime).toBe(LARGO);
    expect(opciones?.refetchOnWindowFocus).toBe(false);
    expect(EN_PROCESO_MS).toBe(30_000);
    const reintentar = opciones?.retry;
    expect(typeof reintentar).toBe("function");
    if (typeof reintentar === "function") {
      expect(reintentar(0, new ErrorDeRed("SIN_CONEXION", "x", "/r"))).toBe(true);
      expect(reintentar(0, new ErrorDeApi(404, "NO", "x", [], "/r"))).toBe(false);
    }
  });
});
