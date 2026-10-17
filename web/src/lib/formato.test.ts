import { describe, expect, it } from "vitest";

import {
  abreviar,
  etiquetaDeCodigo,
  formatearCentavos,
  formatearDecimal,
  formatearDistancia,
  formatearDuracion,
  formatearEntero,
  formatearFecha,
  formatearInstante,
  formatearMonto,
  formatearPorcentaje,
  formatearPorcentajePreciso,
} from "./formato";

describe("montos y numeros", () => {
  it("formatea un decimal del backend como texto exacto, sin pasar por punto flotante", () => {
    expect(formatearMonto("37691314.06")).toBe("37,691,314.06");
    expect(formatearMonto("0.1")).toBe("0.10");
    // Un float binario no representa 9007199254740993.01: el texto si.
    expect(formatearMonto("9007199254740993.01")).toBe("9,007,199,254,740,993.01");
    expect(formatearMonto(-12.5)).toBe("-12.50");
  });

  it("formatea centavos como la misma cantidad en unidades, corriendo el punto en el texto", () => {
    expect(formatearCentavos(30197437)).toBe("301,974.37");
    expect(formatearCentavos("30197437.0000")).toBe("301,974.37");
    expect(formatearCentavos("30197437.4567")).toBe("301,974.374567");
    expect(formatearCentavos(5)).toBe("0.05");
    expect(formatearCentavos(0)).toBe("0.00");
    expect(formatearCentavos(-1234)).toBe("-12.34");
    expect(formatearCentavos("no es numero")).toBe("no es numero");
  });

  it("formatea enteros, decimales y porcentajes", () => {
    expect(formatearEntero(1000)).toBe("1,000");
    expect(formatearEntero("20331")).toBe("20,331");
    expect(formatearDecimal("0.123456")).toBe("0.1235");
    expect(formatearPorcentaje("0.47956791")).toMatch(/^48\.0\s?%$/);
    expect(formatearPorcentaje(0)).toMatch(/^0\.0\s?%$/);
  });

  it("no vuelve cero un gap pequeno", () => {
    expect(formatearPorcentajePreciso("0.000000000000")).toMatch(/^0\s?%$/);
    expect(formatearPorcentajePreciso(0.000123)).toMatch(/^0\.0123\s?%$/);
  });
});

describe("duraciones y distancias", () => {
  it("escribe horas, minutos y segundos", () => {
    expect(formatearDuracion(3725)).toBe("1 h 2 min 5 s");
    expect(formatearDuracion(0)).toBe("0 s");
    expect(formatearDuracion(-65)).toBe("-1 min 5 s");
    expect(formatearDuracion(Number.NaN)).toBe("NaN");
  });

  it("conserva los decimales de un tiempo corto, como el de un solver", () => {
    expect(formatearDuracion(0.011)).toBe("0.011 s");
    expect(formatearDuracion(2.345)).toBe("2.35 s");
  });

  it("usa metros hasta un kilometro y kilometros despues", () => {
    expect(formatearDistancia(950)).toBe("950 m");
    expect(formatearDistancia("10600")).toBe("10.6 km");
  });
});

describe("fechas e instantes", () => {
  it("muestra una fecha de negocio el mismo dia, sin zona", () => {
    expect(formatearFecha("2026-09-30")).toMatch(/^30 sept? 2026$/);
    expect(formatearFecha("2026-01-01")).toBe("1 ene 2026");
    expect(formatearFecha("no-es-fecha")).toBe("no-es-fecha");
  });

  it("muestra un instante en la zona pedida, siempre con su zona escrita", () => {
    const instante = "2026-10-17T13:15:35.619443Z";
    expect(formatearInstante(instante)).toMatch(/17 oct 2026.*13:15.*UTC/);
    expect(formatearInstante(instante, "America/Mexico_City")).toMatch(/07:15.*GMT-6/);
  });

  it("usa UTC con una zona que el navegador no conoce, y deja intacto lo que no es instante", () => {
    expect(formatearInstante("2026-10-17T13:15:00Z", "Zona/Inventada")).toMatch(/13:15.*UTC/);
    expect(formatearInstante("ayer")).toBe("ayer");
  });
});

describe("identificadores y codigos", () => {
  it("abrevia un identificador largo y deja uno corto", () => {
    expect(abreviar("8bd3f8bb-0aad-5f6e-939f-cba3e42a560b")).toBe("8bd3f8bb…");
    expect(abreviar("corto")).toBe("corto");
  });

  it("vuelve legible un codigo del backend, con sus acentos", () => {
    expect(etiquetaDeCodigo("SIN_TIEMPO")).toBe("Sin tiempo");
    expect(etiquetaDeCodigo("TELEFONICA")).toBe("Telefónica");
    expect(etiquetaDeCodigo("VALIDA")).toBe("Válida");
    expect(etiquetaDeCodigo("ASOCIACION_UNICA")).toBe("Asociación única");
  });
});
