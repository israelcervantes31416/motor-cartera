import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { AVISO_ANALYTICS } from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar, urlActual } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, peticiones, servidor } from "../../test/servidor";

// La pagina, cargada antes que las pruebas: su import diferido ya no cuenta en su tiempo.
import "./PaginaAnalytics";

type Celda = (typeof c.categorias.elementos)[number];

function celda(producto: string, cambios: Partial<Celda>): Celda {
  const base = c.categorias.elementos[0];
  if (base === undefined) throw new Error("la captura no tiene celdas");
  return { ...base, dimensiones: { producto }, motivos: [], ...cambios };
}

/** Tres celdas: dos publicadas y una suprimida por la politica de grupo minimo. */
const CON_SUPRESION = {
  ...c.categorias,
  total: 3,
  elementos: [
    celda("CONSUMO", { estado_valor: "DISPONIBLE", valor: "1500.25" }),
    celda("NOMINA", { estado_valor: "SUPRIMIDO", valor: null }),
    celda("TARJETA", { estado_valor: "DISPONIBLE", valor: "730.10" }),
  ],
};

const RECUPERACION = c.catalogo.metricas.find((m) => m.codigo === "recuperacion.neta");

describe("analytics", () => {
  it("lee la metrica del catalogo: su definicion, su formula y sus limitaciones", async () => {
    renderizar(rutaConContexto("/analytics", CONTEXTO));

    const definicion = await screen.findByRole("heading", { name: RECUPERACION?.nombre ?? "" });
    const tarjeta = definicion.closest("section") as HTMLElement;
    expect(tarjeta).toHaveTextContent(RECUPERACION?.formula ?? "");
    expect(within(tarjeta).getByRole("heading", { name: "Limitaciones" })).toBeInTheDocument();
    expect(screen.getByText(AVISO_ANALYTICS)).toBeInTheDocument();
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("una celda suprimida no se grafica, no se reconstruye y no es un cero", async () => {
    servidor.use(
      http.get("/api/analytics/ejecuciones/:analytics/agregados", ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(
          url.searchParams.get("vista") === "PERIODO" ? c.serie : CON_SUPRESION,
        );
      }),
    );
    renderizar(rutaConContexto("/analytics", CONTEXTO));

    expect(
      await screen.findByText(/1 suprimidas: no se grafican ni se reconstruyen\./),
    ).toBeInTheDocument();
    const tabla = screen.getByRole("table", { name: "Por producto" });
    const fila = within(tabla).getByRole("row", { name: /NOMINA/ });
    expect(within(fila).getAllByText("Suprimido").length).toBeGreaterThan(0);
    expect(fila).not.toHaveTextContent(/\d/);
    // La composicion muestra solo las visibles y dice que esta incompleta: no hay TOTAL - resto.
    expect(
      screen.getByText(/Las suprimidas no se muestran: la composición está incompleta\./),
    ).toBeInTheDocument();
    expect(screen.queryByText("2,230.35")).toBeNull();
  });

  it("un periodo sin valor es un hueco en la serie, nunca un cero", async () => {
    renderizar(rutaConContexto("/analytics", CONTEXTO));

    const serie = await screen.findByRole("table", { name: /La serie, periodo por periodo/ });
    const filas = within(serie).getAllByRole("row").slice(1);
    expect(filas).toHaveLength(c.serie.elementos.length);
    expect(within(serie).getAllByText("No disponible").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/1 sin valor \(no disponibles o suprimidos\): huecos, no ceros\./),
    ).toBeTruthy();
  });

  it("cambiar de metrica queda en la URL y pide sus agregados", async () => {
    const { router } = renderizar(rutaConContexto("/analytics", CONTEXTO));

    const selector = await screen.findByLabelText("Métrica");
    const otra = c.catalogo.metricas.find((m) => m.codigo !== "recuperacion.neta");
    await userEvent.selectOptions(selector, otra?.codigo ?? "");

    await waitFor(() => {
      expect(urlActual(router).searchParams.get("metrica")).toBe(otra?.codigo);
    });
    await waitFor(() => {
      expect(
        peticiones.some(
          (u) =>
            u.pathname.endsWith("/agregados") && u.searchParams.get("metrica") === otra?.codigo,
        ),
      ).toBe(true);
    });
  });

  it("muestra los embudos como los publico la ejecucion", async () => {
    renderizar(rutaConContexto("/analytics", CONTEXTO));

    expect(await screen.findByText("Embudo de contacto")).toBeInTheDocument();
  });
});
