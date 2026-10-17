import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AVISO_PARADA } from "../../components/Avisos";
import { rutaConContexto } from "../../lib/contexto";
import * as c from "../../test/capturas";
import { renderizar } from "../../test/renderizar";
import { sinAcciones, sinDatosPersonales, sinViolacionesGraves } from "../../test/revisar";
import { CONTEXTO, NO_EXISTE, peticiones, RUTA } from "../../test/servidor";

// Las paginas, cargadas antes que las pruebas: sus imports diferidos ya no cuentan en su tiempo.
import "../auditoria/PaginaAuditoria";
import "../campo/PaginaCampo";
import "./PaginaRuta";
import "./PaginaRuteo";

describe("campo", () => {
  it("el plan de campo y la carga de cada gestor sintetico", async () => {
    renderizar(rutaConContexto("/campo", CONTEXTO));

    expect(await screen.findByRole("heading", { name: "Campo" })).toBeInTheDocument();
    const cargas = await screen.findByRole("table", { name: /Gestores de la configuración/ });
    expect(within(cargas).getAllByRole("row")).toHaveLength(c.cargas.elementos.length + 1);
    expect(screen.getAllByText("Candidatas").length).toBeGreaterThan(0);
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });
});

describe("ruteo", () => {
  it("las rutas de la ejecucion, sin mapas", async () => {
    renderizar(rutaConContexto("/ruteo", CONTEXTO));

    const rutas = await screen.findByRole("table", { name: /La ruta de cada gestor/ });
    expect(within(rutas).getAllByRole("link")).toHaveLength(c.rutas.elementos.length);
    expect(screen.getByText(AVISO_PARADA)).toBeInTheDocument();
    expect(document.querySelector("canvas")).toBeNull();
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });

  it("el detalle de una ruta: sus paradas en orden de visita", async () => {
    renderizar(rutaConContexto(`/ruteo/rutas/${RUTA}`, CONTEXTO));

    const paradas = await screen.findByRole("table", { name: /Las programadas por su orden/ });
    expect(within(paradas).getAllByRole("row").length).toBeGreaterThan(1);
    expect(screen.getByText(AVISO_PARADA)).toBeInTheDocument();
    sinDatosPersonales();
  });

  it("una ruta que no existe lo dice; un identificador invalido no se pide", async () => {
    const { unmount } = renderizar(rutaConContexto(`/ruteo/rutas/${NO_EXISTE}`, CONTEXTO));
    expect(await screen.findByRole("alert")).toHaveTextContent("RUTA_NO_ENCONTRADA");
    unmount();

    peticiones.length = 0;
    renderizar(rutaConContexto("/ruteo/rutas/no-es-uuid", CONTEXTO));
    expect(await screen.findByText(/no es un identificador de ruta/)).toBeInTheDocument();
    expect(peticiones.some((u) => u.pathname.startsWith("/api/ruteo-v2/rutas/"))).toBe(false);
  });
});

describe("auditoria", () => {
  it("cada nodo del linaje con su identificador completo copiable, y sus relaciones", async () => {
    renderizar(rutaConContexto("/auditoria", CONTEXTO));

    expect(await screen.findByRole("heading", { name: "Auditoría y linaje" })).toBeInTheDocument();
    expect(
      await screen.findByText(
        new RegExp(
          `${String(c.linaje.nodos.length)} nodos y ${String(c.linaje.aristas.length)} relaciones`,
        ),
      ),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: new RegExp(`Copiar .*${CONTEXTO.scoring}`) }).length,
    ).toBeGreaterThan(0);
    sinAcciones();
    sinDatosPersonales();
    await sinViolacionesGraves();
  });
});
