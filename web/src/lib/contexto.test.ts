import { describe, expect, it } from "vitest";

import {
  claveDeContexto,
  conEjecucion,
  contextoComoBusqueda,
  contextoParaApi,
  esUuid,
  leerContexto,
  rutaConContexto,
  type Contexto,
} from "./contexto";

const CORTE = "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b";
const DECISION = "13c166a9-218f-435d-a36c-5aea9eaee03e";
const SCORING = "c1397868-406c-431b-ae0a-25a791e574ae";
const OTRO_SCORING = "4c47e1c3-7a1c-4d0b-9a65-2a3c5b1a9d10";

describe("leerContexto", () => {
  it("lee el corte y cada ejecucion declarada, y nada mas", () => {
    const { contexto, invalidos } = leerContexto(
      new URLSearchParams({ corte: CORTE, decision: DECISION, scoring: SCORING, pagina: "2" }),
    );

    expect(contexto).toEqual({ corte: CORTE, decision: DECISION, scoring: SCORING });
    expect(invalidos).toEqual([]);
  });

  it("no hay contexto sin un corte, y un identificador sin forma de UUID se reporta, no se usa", () => {
    expect(leerContexto(new URLSearchParams({ decision: DECISION })).contexto).toBeNull();

    const { contexto, invalidos } = leerContexto(
      new URLSearchParams({ corte: CORTE, scoring: "ultimo", analytics: "x" }),
    );
    expect(contexto).toEqual({ corte: CORTE });
    expect(invalidos).toEqual(["analytics", "scoring"]);
  });

  it("normaliza a minusculas: la misma ejecucion, la misma URL", () => {
    const { contexto } = leerContexto(new URLSearchParams({ corte: CORTE.toUpperCase() }));
    expect(contexto?.corte).toBe(CORTE);
  });
});

describe("la URL reproduce la vista", () => {
  it("escribe el contexto en un orden fijo, con sus filtros", () => {
    const contexto: Contexto = { scoring: SCORING, corte: CORTE, decision: DECISION };

    expect(rutaConContexto("/cuentas", contexto, { pagina: 2, vacio: "", nada: undefined })).toBe(
      `/cuentas?corte=${CORTE}&decision=${DECISION}&scoring=${SCORING}&pagina=2`,
    );
    expect(rutaConContexto("/", { corte: CORTE })).toBe(`/?corte=${CORTE}`);
  });

  it("ida y vuelta: leer la URL que se escribio da el mismo contexto", () => {
    const contexto: Contexto = { corte: CORTE, decision: DECISION, scoring: SCORING };
    expect(leerContexto(contextoComoBusqueda(contexto)).contexto).toEqual(contexto);
  });
});

describe("contextoParaApi", () => {
  it("nombra cada parametro como lo pide la API y omite lo no declarado", () => {
    expect(contextoParaApi({ corte: CORTE, scoring: SCORING })).toEqual({
      corte_id: CORTE,
      scoring_run_id: SCORING,
    });
  });
});

describe("claveDeContexto", () => {
  it("dos contextos distintos nunca comparten una llave de cache", () => {
    const a = claveDeContexto({ corte: CORTE, scoring: SCORING });
    const b = claveDeContexto({ corte: CORTE, scoring: OTRO_SCORING });
    const sinScoring = claveDeContexto({ corte: CORTE });

    expect(a).not.toEqual(b);
    expect(a).not.toEqual(sinScoring);
    expect(a).toContain(`scoring:${SCORING}`);
    expect(sinScoring).toContain("scoring:");
  });
});

describe("conEjecucion", () => {
  it("agrega, cambia o quita una ejecucion sin tocar las demas", () => {
    const base: Contexto = { corte: CORTE, decision: DECISION };

    expect(conEjecucion(base, "scoring", SCORING)).toEqual({ ...base, scoring: SCORING });
    expect(conEjecucion({ ...base, scoring: SCORING }, "scoring", undefined)).toEqual(base);
    expect(conEjecucion(base, "decision", undefined)).toEqual({ corte: CORTE });
  });

  it("reconoce un UUID", () => {
    expect(esUuid(CORTE)).toBe(true);
    expect(esUuid("8bd3f8bb")).toBe(false);
  });
});
