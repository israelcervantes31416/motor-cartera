import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  AVISO_ATRIBUCION,
  AVISO_OPTIMO,
  AVISO_PARADA,
  AVISO_PREDICCION,
} from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, CUENTA, NO_EXISTE, peticiones, servidor } from "../../test/servidor";

// La pagina, cargada antes que las pruebas: su import diferido ya no cuenta en su tiempo.
import "./PaginaCuenta";

function url(pestana = ""): string {
  return rutaConContexto(`/cuentas/${CUENTA}${pestana === "" ? "" : `/${pestana}`}`, CONTEXTO);
}

async function seccion(titulo: string | RegExp): Promise<HTMLElement> {
  const encabezado = await screen.findByRole("heading", { name: titulo });
  const s = encabezado.closest("section");
  if (s === null) throw new Error("sin seccion");
  return s;
}

describe("la Cuenta 360", () => {
  it("muestra la cuenta en el corte y lo que publico cada ejecucion del contexto", async () => {
    renderizar(url());

    expect(await seccion(/En el corte del/)).toHaveTextContent(c.cuenta.snapshot?.producto ?? "");
    const publicado = await seccion("Lo que publicó cada ejecución del contexto");
    // Asignada en campo y asignada en la optimizacion: dos insignias, cada una de su ejecucion.
    expect(within(publicado).getAllByText("Asignada")).toHaveLength(2);
    expect(screen.getByText(AVISO_PREDICCION)).toBeInTheDocument();
    expect(screen.getByText(AVISO_OPTIMO)).toBeInTheDocument();
    const pestanas = screen.getByRole("navigation", { name: "Secciones de la cuenta" });
    expect(within(pestanas).getAllByRole("link")).toHaveLength(11);
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("pide pocas cosas: la validacion y la cuenta", async () => {
    renderizar(url());

    await seccion("Lo que publicó cada ejecución del contexto");
    const pedidas = peticiones.map((u) => u.pathname);
    expect(pedidas.length).toBeLessThanOrEqual(10);
    expect(new Set(pedidas)).toEqual(
      new Set(["/api/app/v1/contextos/validar", `/api/app/v1/cuentas/${CUENTA}`]),
    );
  });

  it("una cuenta que no existe es un aviso, no una pantalla rota", async () => {
    renderizar(rutaConContexto(`/cuentas/${NO_EXISTE}`, CONTEXTO));

    expect(
      await screen.findByRole("heading", { name: "Cuenta no encontrada" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Volver a las cuentas" })).toBeInTheDocument();
  });

  it("un identificador que no es UUID no se pide", async () => {
    renderizar(rutaConContexto("/cuentas/CU0000000001", CONTEXTO));

    expect(await screen.findByText(/no es un identificador de cuenta/)).toBeInTheDocument();
    expect(peticiones.some((u) => u.pathname.startsWith("/api/app/v1/cuentas/"))).toBe(false);
  });

  it("una seccion que no existe lo dice", async () => {
    renderizar(url("inventada"));

    expect(await screen.findByText("La cuenta no tiene la sección inventada.")).toBeInTheDocument();
  });
});

describe("las pestanas de la cuenta", () => {
  it("historia: distingue ocurrido_en de registrado_en y senala lo registrado tarde", async () => {
    servidor.use(
      http.get("/api/app/v1/cuentas/:cuenta/linea-de-tiempo", () =>
        HttpResponse.json(c.lineaDeTiempoConEventos),
      ),
    );
    renderizar(url("historia"));

    const linea = await seccion("Línea de tiempo");
    expect(
      (await within(linea).findAllByText("Registrado después del known_at del contexto")).length,
    ).toBeGreaterThan(0);
    expect(linea).toHaveTextContent(/ocurrido/i);
    expect(linea).toHaveTextContent(/registrado/i);
    expect(await seccion("Snapshots por corte")).toBeInTheDocument();
    sinDatosPersonales();
  });

  it("pagos: la asociacion unica y la ambigua, como asociacion operacional y no causalidad", async () => {
    servidor.use(
      http.get("/api/app/v1/cuentas/:cuenta/pagos", () =>
        HttpResponse.json(c.pagosConAsociacionUnica),
      ),
    );
    const { unmount } = renderizar(url("pagos"));

    const pagos = await seccion(/Pagos: movimientos económicos canónicos/);
    expect(within(pagos).getByText(AVISO_ATRIBUCION)).toBeInTheDocument();
    expect(await within(pagos).findByText("Asociación única")).toBeInTheDocument();
    unmount();

    servidor.use(
      http.get("/api/app/v1/cuentas/:cuenta/pagos", () => HttpResponse.json(c.pagosAmbiguos)),
    );
    renderizar(url("pagos"));
    const ambiguos = await seccion(/Pagos: movimientos económicos canónicos/);
    expect(await within(ambiguos).findByText("Ambigua")).toBeInTheDocument();
  });

  it("gestiones: cada una con sus dos relojes, sin texto libre", async () => {
    servidor.use(
      http.get("/api/app/v1/cuentas/:cuenta/gestiones", () =>
        HttpResponse.json(c.gestionesConEventos),
      ),
    );
    renderizar(url("gestiones"));

    const gestiones = await seccion("Gestiones");
    expect(await within(gestiones).findByRole("table")).toBeInTheDocument();
    expect(gestiones).not.toHaveTextContent("Acepta revisar su estado de cuenta.");
    sinDatosPersonales();
  });

  it("decision: prioridad, canal y razones, sin probabilidades inventadas", async () => {
    renderizar(url("decision"));

    const decision = await seccion("Decisión (decision/v2)");
    expect(await within(decision).findByText("Prioridad final")).toBeInTheDocument();
    expect(within(decision).getByText("Canal recomendado")).toBeInTheDocument();
    expect(decision).toHaveTextContent("la prioridad no es una probabilidad");
  });

  it("geografia: estado y calidad, municipio y base, sin coordenadas", async () => {
    renderizar(url("geografia"));

    const geografia = await seccion("Geografía y territorial");
    expect(geografia).toHaveTextContent(c.cuenta.geografia.codigo_base ?? "");
    sinDatosPersonales();
  });

  it("ruteo: la parada como plan, sin mapa", async () => {
    renderizar(url("ruteo"));

    const ruteo = await seccion("Ruteo (ruteo/v2)");
    expect(within(ruteo).getByText(AVISO_PARADA)).toBeInTheDocument();
    expect(ruteo.querySelector("svg, canvas, img")).toBeNull();
  });

  it("predictivo: la prediccion con su modelo y sus relojes, y contribuciones al score", async () => {
    renderizar(url("predictivo"));

    const predictivo = await seccion("Predictivo (predictive/model-v1)");
    expect(within(predictivo).getByText(AVISO_PREDICCION)).toBeInTheDocument();
    expect(within(predictivo).getByText("Versión del modelo")).toBeInTheDocument();
    expect(within(predictivo).getByText("known_at")).toBeInTheDocument();
    expect(
      (await within(predictivo).findAllByText(/contribución al score/)).length,
    ).toBeGreaterThan(0);
    // "causa" solo aparece negada: una contribucion nunca se presenta como causa.
    expect(predictivo.textContent.replaceAll("no una causa", "")).not.toMatch(/\bcausas?\b/i);
  });

  it("optimizacion: su resultado, su motivo y su aviso", async () => {
    renderizar(url("optimizacion"));

    const optimizacion = await seccion("Optimización (optimization/v1)");
    expect(within(optimizacion).getByText(AVISO_OPTIMO)).toBeInTheDocument();
    expect(within(optimizacion).getAllByText("Asignada").length).toBeGreaterThan(0);
  });

  it("campo y evidencia se muestran sin datos personales", async () => {
    const { unmount } = renderizar(url("campo"));
    expect(await seccion("Campo (campo/v2)")).toBeInTheDocument();
    sinDatosPersonales();
    unmount();

    renderizar(url("evidencia"));
    expect(await seccion("Evidencia")).toBeInTheDocument();
    sinDatosPersonales();
  });
});
