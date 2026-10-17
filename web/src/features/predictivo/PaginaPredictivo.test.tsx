import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AVISO_DRIFT, AVISO_PREDICCION } from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, peticiones } from "../../test/servidor";

// La pagina, cargada antes que las pruebas: su import diferido ya no cuenta en su tiempo.
import "./PaginaPredictivo";

function vista(nombre: string): string {
  return rutaConContexto(`/predictivo/${nombre}`, CONTEXTO);
}

describe("predictivo", () => {
  it("la ficha del modelo: version, periodos, hashes y limitaciones", async () => {
    renderizar(vista("modelo"));

    const ficha = await screen.findByRole("heading", { name: "Ficha del modelo" });
    expect(ficha.closest("section")).toHaveTextContent(c.modelo.version_modelo);
    expect(screen.getByRole("heading", { name: "Componentes" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Limitaciones, privacidad y ética" }),
    ).toBeInTheDocument();
    expect(screen.getByText(AVISO_PREDICCION)).toBeInTheDocument();
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("el desempeno de cada componente se muestra con su baseline", async () => {
    renderizar(vista("desempeno"));

    const componente = c.modelo.componentes[0];
    const encabezado = await screen.findByRole("heading", {
      name: new RegExp(componente?.target ?? "sin-target"),
    });
    expect(encabezado.closest("section")).toHaveTextContent(/baseline/i);
  });

  it("la calibracion: la prediccion media contra lo observado, con su tabla", async () => {
    renderizar(vista("calibracion"));

    const componente = c.modelo.componentes[0];
    expect(
      await screen.findByText(`Calibración de ${componente?.target ?? ""}`),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("table").length).toBeGreaterThan(0);
  });

  it("el scoring y sus predicciones, paginadas en el servidor", async () => {
    renderizar(vista("scoring"));

    expect(await screen.findByRole("heading", { name: "Scoring" })).toBeInTheDocument();
    expect(
      await screen.findByRole("table", {
        name: /Predicciones publicadas, ordenadas en el servidor/,
      }),
    ).toBeInTheDocument();
    await waitFor(() => {
      const pedida = peticiones.find((u) => u.pathname.endsWith("/predicciones"));
      expect(pedida?.searchParams.get("por_pagina")).toBe("25");
    });
  });

  it("el drift: por feature, con su aviso de que no implica degradacion", async () => {
    renderizar(vista("drift"));

    expect(await screen.findByText(AVISO_DRIFT)).toBeInTheDocument();
    const tabla = await screen.findByRole("table", { name: /PSI por feature y por salida/ });
    expect(within(tabla).getAllByRole("row").length).toBeGreaterThan(1);
  });
});
