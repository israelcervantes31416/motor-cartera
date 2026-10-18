import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import {
  AVISO_ANALYTICS,
  AVISO_ATRIBUCION,
  AVISO_OPTIMO,
  AVISO_PARADA,
  AVISO_PREDICCION,
} from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, errorDeApi, peticiones, resumenPara, servidor } from "../../test/servidor";

// La pagina, cargada antes que las pruebas: su import diferido ya no cuenta en su tiempo.
import "./PaginaResumen";

async function bloque(titulo: string): Promise<HTMLElement> {
  const encabezado = await screen.findByRole("heading", { name: titulo });
  const seccion = encabezado.closest("section");
  if (seccion === null) throw new Error(`${titulo} no esta en una seccion`);
  return seccion;
}

describe("el resumen", () => {
  it("muestra cada bloque de la ejecucion que el contexto declara, con sus avisos", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    for (const titulo of [
      "Cartera, recuperación y gestión",
      "Decisión (decision/v2)",
      "Predictivo (predictive/model-v1)",
      "Optimización (optimization/v1)",
      "Campo (campo/v2)",
      "Ruteo (ruteo/v2)",
    ]) {
      expect(await bloque(titulo)).toBeInTheDocument();
    }
    for (const aviso of [
      AVISO_ANALYTICS,
      AVISO_ATRIBUCION,
      AVISO_PREDICCION,
      AVISO_OPTIMO,
      AVISO_PARADA,
    ]) {
      expect(screen.getByText(aviso)).toBeInTheDocument();
    }
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("un indicador NO_DISPONIBLE dice su estado, nunca un cero", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const analytics = await bloque("Cartera, recuperación y gestión");
    const indicador = within(analytics).getByText("Recuperación neta").closest(".indicador");
    expect(indicador).not.toBeNull();
    const valor = within(indicador as HTMLElement).getByText("No disponible");
    expect(valor).toBeInTheDocument();
    expect(indicador).not.toHaveTextContent(/\d/);
    expect(
      c.resumen.analytics.indicadores?.find((i) => i.nombre === "recuperacion_neta")?.estado_valor,
    ).toBe("NO_DISPONIBLE");
  });

  it("muestra los numeros como los publico cada motor, sin recalcularlos", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const optimizacion = await bloque("Optimización (optimization/v1)");
    expect(within(optimizacion).getByText("301,974.37")).toBeInTheDocument();
    const predictivo = await bloque("Predictivo (predictive/model-v1)");
    expect(within(predictivo).getByRole("table", { name: "Predicciones por décimo" })).toBeTruthy();
    const ruteo = await bloque("Ruteo (ruteo/v2)");
    // No enrutables y con base no enrutable son dos numeros publicados: no se suman.
    expect(within(ruteo).getByText("No enrutables")).toBeInTheDocument();
    expect(within(ruteo).getByText("Con base no enrutable")).toBeInTheDocument();
  });

  it("un bloque cuya ejecucion no se declaro es NO_DECLARADA, sin un solo valor", async () => {
    renderizar(rutaConContexto("/resumen", { corte: CONTEXTO.corte, decision: CONTEXTO.decision }));

    const predictivo = await bloque("Predictivo (predictive/model-v1)");
    expect(predictivo).toHaveTextContent("El contexto no declara el scoring");
    expect(within(predictivo).queryByText(AVISO_PREDICCION)).toBeNull();
    for (const titulo of [
      "Optimización (optimization/v1)",
      "Campo (campo/v2)",
      "Ruteo (ruteo/v2)",
    ]) {
      const b = await bloque(titulo);
      expect(b).toHaveTextContent("El contexto no declara");
      expect(within(b).queryAllByRole("definition")).toEqual([]);
    }
    const decision = await bloque("Decisión (decision/v2)");
    expect(decision).toHaveTextContent("Cuentas decididas");
  });

  it("una ejecucion EN_PROCESO dice que todavia no publica", async () => {
    servidor.use(
      http.get("/api/app/v1/resumen", ({ request }) => {
        const r = resumenPara(new URL(request.url));
        return HttpResponse.json({
          ...r,
          predictivo: { ...r.predictivo, estado_fuente: "EN_PROCESO", estado: "EN_PROCESO" },
        });
      }),
    );
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const predictivo = await bloque("Predictivo (predictive/model-v1)");
    expect(predictivo).toHaveTextContent("sigue en proceso: todavía no publica");
  });

  it("pide pocas cosas: la validacion, el resumen y el catalogo", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    await bloque("Ruteo (ruteo/v2)");
    await waitFor(() => {
      expect(peticiones.some((u) => u.pathname === "/api/analytics/catalogo")).toBe(true);
    });
    const deDatos = peticiones.map((u) => u.pathname);
    expect(deDatos.length).toBeLessThanOrEqual(8);
    expect(new Set(deDatos)).toEqual(
      new Set(["/api/app/v1/contextos/validar", "/api/app/v1/resumen", "/api/analytics/catalogo"]),
    );
  });

  it("si la API falla, lo dice y deja reintentar", async () => {
    servidor.use(http.get("/api/app/v1/resumen", () => errorDeApi(500, "ERROR_INTERNO")));
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("La API no está disponible");
    expect(within(alerta).getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });
});
