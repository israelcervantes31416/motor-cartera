import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { describe, expect, it } from "vitest";

import { rutaConContexto } from "../lib/contexto";
import * as c from "../test/capturas";
import { renderizar, urlActual } from "../test/renderizar";
import { sinAcciones, sinViolacionesGraves } from "../test/revisar";
import { CONTEXTO, errorDeApi, NO_EXISTE, peticiones, servidor } from "../test/servidor";

// La pagina, cargada antes que las pruebas: su import diferido ya no cuenta en su tiempo.
import "../features/resumen/PaginaResumen";

function pedidas(ruta: string): URL[] {
  return peticiones.filter((u) => u.pathname === ruta);
}

describe("el selector de contexto", () => {
  it("lista las carteras y los cortes, y no abre nada solo", async () => {
    const { router } = renderizar("/");

    expect(
      await screen.findByRole("heading", { name: "Abrir un contexto operativo" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: /Del más reciente/ })).toBeInTheDocument();
    for (const cartera of c.opcionesSinCorte.carteras) {
      expect(
        screen.getByRole("radio", { name: new RegExp(cartera.cartera_id) }),
      ).toBeInTheDocument();
    }
    expect(urlActual(router).pathname).toBe("/");
    sinAcciones();
    await sinViolacionesGraves();
  });

  it("con un corte, propone contextos compatibles y abre uno solo cuando se le pide", async () => {
    const { router } = renderizar(`/?corte=${CONTEXTO.corte}`);

    const propuesta = await screen.findByText(
      "Una optimización y lo que declaró, con analytics si encadena.",
    );
    expect(urlActual(router).pathname).toBe("/");
    const tarjeta = propuesta.closest("li");
    expect(tarjeta).not.toBeNull();
    await userEvent.click(
      within(tarjeta as HTMLElement).getByRole("link", { name: "Abrir este contexto" }),
    );

    // La pagina del resumen se carga diferida: la navegacion termina cuando llega su paquete.
    await waitFor(() => {
      expect(urlActual(router).pathname).toBe("/resumen");
    });
    const url = urlActual(router);
    expect(Object.fromEntries(url.searchParams)).toEqual({
      corte: CONTEXTO.corte,
      decision: CONTEXTO.decision,
      analytics: CONTEXTO.analytics,
      scoring: CONTEXTO.scoring,
      optimizacion: CONTEXTO.optimizacion,
      campo: CONTEXTO.campo,
      ruteo: CONTEXTO.ruteo,
    });
  });

  it("declara cada ejecucion a mano y la valida en la API", async () => {
    const { router } = renderizar(`/?corte=${CONTEXTO.corte}`);

    const scoring = await screen.findByLabelText(/Scoring \(predictive\/model-v1\)/);
    await userEvent.selectOptions(scoring, CONTEXTO.scoring);

    await waitFor(() => {
      expect(urlActual(router).searchParams.get("scoring")).toBe(CONTEXTO.scoring);
    });
    expect(await screen.findByText(/Cada ejecución existe, es del corte/)).toBeInTheDocument();
    const validaciones = pedidas("/api/app/v1/contextos/validar");
    expect(validaciones.at(-1)?.searchParams.get("scoring_run_id")).toBe(CONTEXTO.scoring);
    expect(screen.getByRole("link", { name: "Abrir el contexto" })).toHaveAttribute(
      "href",
      rutaConContexto("/resumen", { corte: CONTEXTO.corte, scoring: CONTEXTO.scoring }),
    );
  });
});

describe("el guardia de contexto", () => {
  it("sin contexto, una pagina no pide nada y ofrece el selector", async () => {
    renderizar("/resumen");

    expect(await screen.findByRole("link", { name: "Elegir un contexto" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(pedidas("/api/app/v1/resumen")).toEqual([]);
  });

  it("reporta los identificadores que no son UUID y no los usa", async () => {
    renderizar("/resumen?corte=ultimo");

    expect(
      await screen.findByText(
        /La URL trae identificadores sin forma de UUID, que no se usan: corte/,
      ),
    ).toBeInTheDocument();
  });

  it("un contexto incompatible no se combina: dice por que y no pide el resumen", async () => {
    renderizar(rutaConContexto("/resumen", { ...CONTEXTO, scoring: NO_EXISTE }));

    expect(
      await screen.findByRole("heading", { name: /El contexto no encadena: no se combina/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(`No existe la ejecucion ${NO_EXISTE}.`)).toBeInTheDocument();
    expect(screen.getAllByText("Incompatible").length).toBeGreaterThan(0);
    expect(pedidas("/api/app/v1/resumen")).toEqual([]);
  });

  it("una pagina que necesita una ejecucion no declarada no busca otra", async () => {
    renderizar(rutaConContexto("/analytics", { corte: CONTEXTO.corte }));

    expect(
      await screen.findByText(/El contexto no declara Analytics \(analytics\/v1\)/),
    ).toBeInTheDocument();
    expect(peticiones.some((u) => u.pathname.startsWith("/api/analytics/"))).toBe(false);
  });

  it("una falla de la API al validar se muestra con su codigo y se puede reintentar", async () => {
    servidor.use(
      http.get("/api/app/v1/contextos/validar", () => errorDeApi(503, "BASE_NO_DISPONIBLE")),
    );
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("La API no está disponible");
    expect(within(alerta).getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });
});

describe("el marco", () => {
  it("muestra la cabecera del contexto y recuerda si se abrio", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const resumen = await screen.findByText("6 de 6 ejecuciones declaradas");
    const cabecera = resumen.closest("details");
    expect(cabecera).not.toHaveAttribute("open");
    expect(within(cabecera as HTMLElement).getByText(c.validado.corte.cartera_id)).toBeVisible();

    await userEvent.click(screen.getByText("Ver las ejecuciones"));
    expect(cabecera).toHaveAttribute("open");
    expect(window.localStorage.getItem("motor-cartera:contexto-abierto")).toBe("si");
    expect(screen.getByText("Ocultar las ejecuciones")).toBeInTheDocument();
  });

  it("cada enlace de la navegacion conserva el contexto", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const navegacion = await screen.findByRole("navigation", { name: "Secciones" });
    for (const enlace of within(navegacion).getAllByRole("link")) {
      const url = new URL(enlace.getAttribute("href") ?? "", "http://localhost");
      expect(url.searchParams.get("corte")).toBe(CONTEXTO.corte);
      expect(url.searchParams.get("scoring")).toBe(CONTEXTO.scoring);
    }
    expect(within(navegacion).getByRole("link", { name: "Resumen" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("una ruta que no existe es un 404 util", async () => {
    renderizar("/no-existe");

    expect(
      await screen.findByRole("heading", { name: "Página no encontrada" }),
    ).toBeInTheDocument();
    expect(screen.getByText("/no-existe")).toBeInTheDocument();
  });

  it("el menu movil se cierra al navegar", async () => {
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    const menu = await screen.findByRole("button", { name: "Menú" });
    await userEvent.click(menu);
    expect(screen.getByRole("button", { name: "Cerrar menú" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await userEvent.click(screen.getByRole("link", { name: "Cuentas" }));
    expect(await screen.findByRole("button", { name: "Menú" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });
});
