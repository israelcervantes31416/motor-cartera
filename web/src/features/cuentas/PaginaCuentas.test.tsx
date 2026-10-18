import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AVISO_OPTIMO, AVISO_PREDICCION } from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar, urlActual } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, peticiones } from "../../test/servidor";
import { filtrosDeLaUrl } from "./PaginaCuentas";

function pedidasDeCuentas(): URLSearchParams[] {
  return peticiones.filter((u) => u.pathname === "/api/app/v1/cuentas").map((u) => u.searchParams);
}

async function tabla(): Promise<HTMLElement> {
  return screen.findByRole("table", { name: /Cuentas del corte/ });
}

describe("el explorador de cuentas", () => {
  it("pide la primera pagina de 50 en el servidor, con el contexto entero", async () => {
    renderizar(rutaConContexto("/cuentas", CONTEXTO));

    const filas = within(await tabla())
      .getAllByRole("row")
      .slice(1);
    expect(filas).toHaveLength(c.cuentas.elementos.length);
    const [pedida] = pedidasDeCuentas();
    expect(Object.fromEntries(pedida ?? [])).toMatchObject({
      pagina: "1",
      por_pagina: "50",
      orden: "CUENTA",
      sentido: "ASC",
      corte_id: CONTEXTO.corte,
      decision_run_id: CONTEXTO.decision,
      scoring_run_id: CONTEXTO.scoring,
      optimizacion_run_id: CONTEXTO.optimizacion,
    });
    expect(screen.getByText(AVISO_PREDICCION)).toBeInTheDocument();
    expect(screen.getByText(AVISO_OPTIMO)).toBeInTheDocument();
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("cada cuenta abre su Cuenta 360 en el mismo contexto", async () => {
    renderizar(rutaConContexto("/cuentas", CONTEXTO));

    const primera = c.cuentas.elementos[0];
    const enlace = within(await tabla()).getByRole("link", {
      name: `Abrir la cuenta ${primera?.cuenta_id ?? ""}`,
    });
    const url = new URL(enlace.getAttribute("href") ?? "", "http://localhost");
    expect(url.pathname).toBe(`/cuentas/${primera?.cuenta_id ?? ""}`);
    expect(url.searchParams.get("scoring")).toBe(CONTEXTO.scoring);
  });

  it("pagina en el servidor y deja la pagina en la URL", async () => {
    const { router } = renderizar(rutaConContexto("/cuentas", CONTEXTO));

    await tabla();
    await userEvent.click(screen.getByRole("button", { name: /Siguiente/ }));

    await waitFor(() => {
      expect(urlActual(router).searchParams.get("pagina")).toBe("2");
    });
    await waitFor(() => {
      expect(pedidasDeCuentas().at(-1)?.get("pagina")).toBe("2");
    });
  });

  it("una URL profunda reconstruye los filtros, el orden y la pagina", async () => {
    renderizar(
      rutaConContexto("/cuentas", CONTEXTO, {
        prioridad: "ALTA",
        canal: "CAMPO",
        estado_optimizacion: "ASIGNADA",
        orden: "SALDO_TOTAL",
        sentido: "DESC",
        pagina: 3,
        por_pagina: 25,
      }),
    );

    await tabla();
    expect(Object.fromEntries(pedidasDeCuentas()[0] ?? [])).toMatchObject({
      prioridad: "ALTA",
      canal: "CAMPO",
      estado_optimizacion: "ASIGNADA",
      orden: "SALDO_TOTAL",
      sentido: "DESC",
      pagina: "3",
      por_pagina: "25",
    });
    expect(screen.getByLabelText("Prioridad")).toHaveValue("ALTA");
    expect(screen.getByLabelText("Canal recomendado")).toHaveValue("CAMPO");
    expect(screen.getByRole("columnheader", { name: /Saldo total/ })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("ordena en el servidor al pedirlo desde un encabezado", async () => {
    const { router } = renderizar(rutaConContexto("/cuentas", CONTEXTO));

    await tabla();
    await userEvent.click(screen.getByRole("button", { name: /Días de atraso/ }));

    await waitFor(() => {
      expect(urlActual(router).searchParams.get("orden")).toBe("DIAS_ATRASO");
    });
    await waitFor(() => {
      expect(pedidasDeCuentas().at(-1)?.get("orden")).toBe("DIAS_ATRASO");
    });
  });

  it("busca por el principio del cuenta_id cuando se deja de escribir: una sola peticion", async () => {
    renderizar(rutaConContexto("/cuentas", CONTEXTO));

    await tabla();
    await userEvent.type(screen.getByLabelText("Identificador técnico (principio)"), "41095c46");

    await waitFor(() => {
      expect(pedidasDeCuentas().at(-1)?.get("cuenta")).toBe("41095c46");
    });
    const conBusqueda = pedidasDeCuentas().filter((p) => p.has("cuenta"));
    expect(conBusqueda.map((p) => p.get("cuenta"))).toEqual(["41095c46"]);
  });
});

describe("filtrosDeLaUrl", () => {
  it("manda solo filtros validos, y solo los de las ejecuciones declaradas", () => {
    const busqueda = new URLSearchParams({
      prioridad: "URGENTE",
      canal: "CAMPO",
      estado_prediccion: "COMPLETA",
      orden: "P_RECUPERACION_POSITIVA_30D",
      pagina: "-4",
      por_pagina: "1000",
      cuenta: "zz",
      producto: "consumo; drop",
    });

    const sinScoring = filtrosDeLaUrl(busqueda, {
      corte: CONTEXTO.corte,
      decision: CONTEXTO.decision,
    });
    expect(sinScoring).toEqual({
      pagina: 1,
      por_pagina: 50,
      orden: "CUENTA",
      sentido: "ASC",
      canal: "CAMPO",
    });

    const conScoring = filtrosDeLaUrl(busqueda, CONTEXTO);
    expect(conScoring).toMatchObject({
      orden: "P_RECUPERACION_POSITIVA_30D",
      estado_prediccion: "COMPLETA",
    });
    expect(conScoring).not.toHaveProperty("prioridad");
    expect(conScoring).not.toHaveProperty("cuenta");
    expect(conScoring).not.toHaveProperty("producto");
  });
});
