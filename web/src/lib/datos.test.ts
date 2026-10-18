import { describe, expect, it } from "vitest";

import {
  comoLista,
  comoNumero,
  comoRegistro,
  comoTexto,
  conteosDe,
  formatearSegunUnidad,
  textoDe,
} from "./datos";

describe("leer objetos libres sin suponer su forma", () => {
  it("devuelve null o vacio cuando la forma no es la esperada, nunca un cero", () => {
    expect(comoRegistro({ a: 1 })).toEqual({ a: 1 });
    expect(comoRegistro([1])).toBeNull();
    expect(comoRegistro(null)).toBeNull();
    expect(comoLista([1, 2])).toEqual([1, 2]);
    expect(comoLista("x")).toEqual([]);
    expect(comoNumero("12.5")).toBe(12.5);
    expect(comoNumero(3)).toBe(3);
    expect(comoNumero("")).toBeNull();
    expect(comoNumero("abc")).toBeNull();
    expect(comoNumero(null)).toBeNull();
    expect(comoNumero(Number.POSITIVE_INFINITY)).toBeNull();
    expect(comoTexto("x")).toBe("x");
    expect(comoTexto(1)).toBeNull();
  });

  it("escribe un valor libre como texto", () => {
    expect(textoDe("ALTA")).toBe("ALTA");
    expect(textoDe(3)).toBe("3");
    expect(textoDe(true)).toBe("true");
    expect(textoDe({ a: 1 })).toBe('{"a":1}');
    expect(textoDe(null)).toBe("");
    expect(textoDe(undefined, "—")).toBe("—");
  });

  it("lee conteos en el orden en que llegan y descarta lo que no es numero", () => {
    expect(conteosDe({ ALTA: 301, BAJA: "208", RARO: "x" })).toEqual([
      { clave: "ALTA", valor: 301 },
      { clave: "BAJA", valor: 208 },
    ]);
    expect(conteosDe(null)).toEqual([]);
  });
});

describe("formatearSegunUnidad", () => {
  it("formatea en la unidad que declara el catalogo", () => {
    expect(formatearSegunUnidad("MONEDA", "194910.59")).toBe("194,910.59");
    expect(formatearSegunUnidad("RAZON", "0.5")).toMatch(/^50\.0\s?%$/);
    expect(formatearSegunUnidad("DIAS", "2.5")).toBe("2.5 días");
    expect(formatearSegunUnidad("SEGUNDOS", "65")).toBe("1 min 5 s");
    expect(formatearSegunUnidad("SEGUNDOS", 65)).toBe("1 min 5 s");
    expect(formatearSegunUnidad("METROS", 1500)).toBe("1.5 km");
    expect(formatearSegunUnidad("CUENTAS", "1000")).toBe("1,000");
  });
});
