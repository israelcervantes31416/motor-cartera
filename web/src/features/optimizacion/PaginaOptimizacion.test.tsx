import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { AVISO_OPTIMO } from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, peticiones, servidor } from "../../test/servidor";

// La pagina, cargada antes que las pruebas: su import diferido ya no cuenta en su tiempo.
import "./PaginaOptimizacion";

function vista(nombre: string, extra: Record<string, string> = {}): string {
  return rutaConContexto(`/optimizacion/${nombre}`, CONTEXTO, extra);
}

describe("optimizacion", () => {
  it("el plan: el solver, el valor de oportunidad y la cota, con su aviso", async () => {
    renderizar(vista("resumen"));

    const plan = await screen.findByRole("heading", { name: "El plan" });
    const tarjeta = plan.closest("section") as HTMLElement;
    expect(within(tarjeta).getByText("Óptimo demostrado")).toBeInTheDocument();
    expect(within(tarjeta).getAllByText("301,974.37")).toHaveLength(2);
    expect(screen.getByText(AVISO_OPTIMO)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Lo que el plan no dice" })).toBeInTheDocument();
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("la capacidad de cada pool y la utilizacion por canal", async () => {
    renderizar(vista("capacidad"));

    expect(
      await screen.findByRole("table", { name: /Capacidad de cada pool/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Utilización por canal")).toBeInTheDocument();
  });

  it("las restricciones activas se distinguen con texto, no solo con color", async () => {
    renderizar(vista("restricciones"));

    const tabla = await screen.findByRole("table", { name: /Cada restricción del plan/ });
    const activas = c.restricciones.elementos.filter((r) => r.binding).length;
    expect(within(tabla).getAllByText("Activa (binding)")).toHaveLength(activas);
  });

  it("las asignaciones se filtran y se paginan en el servidor", async () => {
    renderizar(vista("asignaciones", { canal: "CAMPO", estado: "ASIGNADA" }));

    expect(
      await screen.findByRole("table", { name: /El resultado de cada cuenta en el plan/ }),
    ).toBeInTheDocument();
    await waitFor(() => {
      const pedida = peticiones.find((u) => u.pathname.endsWith("/resultados"));
      expect(Object.fromEntries(pedida?.searchParams ?? [])).toMatchObject({
        canal: "CAMPO",
        estado: "ASIGNADA",
        pagina: "1",
        por_pagina: "50",
      });
    });
  });

  it("el solver y su auditoria: huella, certificado y diagnostico", async () => {
    renderizar(vista("solver"));

    expect(await screen.findByRole("heading", { name: "Solver" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Certificado de optimalidad" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Hashes y diagnóstico" })).toBeInTheDocument();
  });

  it("una ejecucion sin optimo demostrado no publica plan y no se esconde", async () => {
    servidor.use(
      http.get("/api/optimizacion/ejecuciones/:optimizacion", () =>
        HttpResponse.json({
          ...c.optimizacion,
          estado: "FALLIDA",
          resultado: "SIN_OPTIMO_DEMOSTRADO",
          solver_status: "TIME_LIMIT",
        }),
      ),
    );
    renderizar(vista("capacidad"));

    expect(await screen.findByRole("heading", { name: "Sin plan publicado" })).toBeInTheDocument();
    expect(screen.getAllByText(/SIN_OPTIMO_DEMOSTRADO/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Fallida").length).toBeGreaterThan(0);
    expect(peticiones.some((u) => u.pathname.endsWith("/capacidad"))).toBe(false);
  });
});
