import { delay, http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { CONTEXTO, errorDeApi, peticiones, servidor } from "../test/servidor";
import {
  consultaComoTexto,
  ErrorDeApi,
  ErrorDeRed,
  leer,
  reintentable,
  rutaConParametros,
} from "./cliente";

describe("leer", () => {
  it("devuelve la respuesta 200 de la ruta, por /api, con la consulta en la URL", async () => {
    const datos = await leer("/app/v1/contextos/validar", {
      consulta: { corte_id: CONTEXTO.corte, scoring_run_id: CONTEXTO.scoring },
    });

    expect(datos.compatible).toBe(true);
    const [url] = peticiones;
    expect(url?.pathname).toBe("/api/app/v1/contextos/validar");
    expect(url?.searchParams.get("corte_id")).toBe(CONTEXTO.corte);
    expect(url?.searchParams.get("scoring_run_id")).toBe(CONTEXTO.scoring);
  });

  it("codifica cada parametro de ruta: un identificador es dato, no ruta", async () => {
    servidor.use(
      http.get("/api/app/v1/cuentas/:cuenta", ({ request }) => {
        peticiones.push(new URL(request.url));
        return errorDeApi(404, "CUENTA_NO_ENCONTRADA");
      }),
    );

    await expect(
      leer("/app/v1/cuentas/{cuenta_id}", {
        ruta: { cuenta_id: "a/b?c" },
        consulta: { corte_id: CONTEXTO.corte },
      }),
    ).rejects.toBeInstanceOf(ErrorDeApi);
    expect(peticiones[0]?.pathname).toBe("/api/app/v1/cuentas/a%2Fb%3Fc");
  });

  it("convierte un 4xx de la API en un ErrorDeApi con su codigo, su mensaje y sus detalles", async () => {
    servidor.use(
      http.get("/api/app/v1/resumen", () =>
        HttpResponse.json(
          {
            codigo: "CONTEXTO_INCOMPATIBLE",
            mensaje: "El contexto no es compatible.",
            detalles: [{ campo: "scoring_run_id", problema: "No existe." }],
            run_id: null,
            pagos_run_id: null,
          },
          { status: 409 },
        ),
      ),
    );

    const error = await leer("/app/v1/resumen", { consulta: { corte_id: CONTEXTO.corte } }).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ErrorDeApi);
    const e = error as ErrorDeApi;
    expect([e.estado, e.codigo, e.message]).toEqual([
      409,
      "CONTEXTO_INCOMPATIBLE",
      "El contexto no es compatible.",
    ]);
    expect(e.detalles).toEqual([{ campo: "scoring_run_id", problema: "No existe." }]);
    expect(e.ruta).toContain("/app/v1/resumen?corte_id=");
  });

  it("convierte un 5xx sin cuerpo de la API en un ErrorDeApi generico", async () => {
    servidor.use(
      http.get("/api/app/v1/linaje", () => new HttpResponse("Bad gateway", { status: 502 })),
    );

    const error = await leer("/app/v1/linaje", { consulta: { corte_id: CONTEXTO.corte } }).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ErrorDeApi);
    expect((error as ErrorDeApi).codigo).toBe("HTTP_502");
    expect((error as ErrorDeApi).message).toBe("La API no está disponible o falló al responder.");
  });

  it("dice RESPUESTA_ILEGIBLE si un 200 no es JSON", async () => {
    servidor.use(http.get("/api/analytics/catalogo", () => new HttpResponse("<html>")));

    const error = await leer("/analytics/catalogo").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ErrorDeRed);
    expect((error as ErrorDeRed).causa).toBe("RESPUESTA_ILEGIBLE");
  });

  it("dice SIN_CONEXION si la API no contesta", async () => {
    servidor.use(http.get("/api/analytics/catalogo", () => HttpResponse.error()));

    const error = await leer("/analytics/catalogo").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ErrorDeRed);
    expect((error as ErrorDeRed).causa).toBe("SIN_CONEXION");
  });

  it("dice TIEMPO_AGOTADO si la API tarda mas que el maximo", async () => {
    servidor.use(
      http.get("/api/analytics/catalogo", async () => {
        await delay(500);
        return HttpResponse.json({});
      }),
    );

    const error = await leer("/analytics/catalogo", { tiempoMaximoMs: 20 }).catch(
      (e: unknown) => e,
    );

    expect(error).toBeInstanceOf(ErrorDeRed);
    expect((error as ErrorDeRed).causa).toBe("TIEMPO_AGOTADO");
  });

  it("propaga tal cual la cancelacion de quien la pidio", async () => {
    servidor.use(
      http.get("/api/analytics/catalogo", async () => {
        await delay(500);
        return HttpResponse.json({});
      }),
    );
    const controlador = new AbortController();

    const promesa = leer("/analytics/catalogo", { signal: controlador.signal });
    controlador.abort();
    const error = await promesa.catch((e: unknown) => e);

    expect(error).not.toBeInstanceOf(ErrorDeRed);
    expect(error).not.toBeInstanceOf(ErrorDeApi);
    expect((error as Error).name).toBe("AbortError");
  });
});

describe("consultaComoTexto y rutaConParametros", () => {
  it("omite lo que no tiene valor y conserva el orden de las llaves", () => {
    expect(
      consultaComoTexto({ b: "2", a: 1, vacio: "", nulo: null, falta: undefined, si: true }),
    ).toBe("?b=2&a=1&si=true");
    expect(consultaComoTexto({})).toBe("");
  });

  it("codifica cada parametro y falla si falta uno", () => {
    expect(rutaConParametros("/x/{id}/y", { id: "a b" })).toBe("/x/a%20b/y");
    expect(() => rutaConParametros("/x/{id}", {})).toThrow("Falta el parametro de ruta id");
  });
});

describe("reintentable", () => {
  it("reintenta una vez una falla de red o un 5xx, nunca un 4xx ni una respuesta ilegible", () => {
    const red = new ErrorDeRed("SIN_CONEXION", "x", "/r");
    const tiempo = new ErrorDeRed("TIEMPO_AGOTADO", "x", "/r");
    const ilegible = new ErrorDeRed("RESPUESTA_ILEGIBLE", "x", "/r");
    const caida = new ErrorDeApi(503, "HTTP_503", "x", [], "/r");
    const negada = new ErrorDeApi(404, "CUENTA_NO_ENCONTRADA", "x", [], "/r");

    expect(reintentable(0, red)).toBe(true);
    expect(reintentable(0, tiempo)).toBe(true);
    expect(reintentable(0, caida)).toBe(true);
    expect(reintentable(1, caida)).toBe(false);
    expect(reintentable(0, ilegible)).toBe(false);
    expect(reintentable(0, negada)).toBe(false);
    expect(reintentable(0, new Error("otro"))).toBe(false);
  });
});
