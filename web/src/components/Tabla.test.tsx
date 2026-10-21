import { createColumnHelper } from "@tanstack/react-table";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Paginacion, RegionDeTabla, TablaDatos } from "./Tabla";

interface Fila {
  readonly id: string;
  readonly saldo: number;
}

const columna = createColumnHelper<Fila>();
const COLUMNAS = [
  columna.display({ id: "id", header: "Cuenta", cell: ({ row }) => row.original.id }),
  columna.display({
    id: "saldo",
    header: "Saldo",
    cell: ({ row }) => row.original.saldo,
    meta: { orden: "SALDO_TOTAL", numerica: true },
  }),
];

describe("TablaDatos", () => {
  it("es una tabla semantica: caption, encabezados con scope y filas en el orden recibido", () => {
    render(
      <TablaDatos
        titulo="Cuentas del corte"
        columnas={COLUMNAS}
        filas={[
          { id: "b", saldo: 2 },
          { id: "a", saldo: 1 },
        ]}
      />,
    );

    const tabla = screen.getByRole("table", { name: "Cuentas del corte" });
    const encabezados = within(tabla).getAllByRole("columnheader");
    expect(encabezados.map((e) => e.getAttribute("scope"))).toEqual(["col", "col"]);
    const filas = within(tabla).getAllByRole("row").slice(1);
    expect(filas.map((f) => within(f).getAllByRole("cell")[0]?.textContent)).toEqual(["b", "a"]);
  });

  it("pide otro orden a quien la usa y marca el orden actual con aria-sort", async () => {
    const onOrden = vi.fn();
    const { rerender } = render(
      <TablaDatos titulo="T" columnas={COLUMNAS} filas={[]} onOrden={onOrden} />,
    );

    await userEvent.click(screen.getByRole("button", { name: /Saldo/ }));
    expect(onOrden).toHaveBeenCalledWith({ columna: "SALDO_TOTAL", descendente: false });

    rerender(
      <TablaDatos
        titulo="T"
        columnas={COLUMNAS}
        filas={[]}
        orden={{ columna: "SALDO_TOTAL", descendente: false }}
        onOrden={onOrden}
      />,
    );
    expect(screen.getByRole("columnheader", { name: /Saldo/ })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    await userEvent.click(screen.getByRole("button", { name: /Saldo/ }));
    expect(onOrden).toHaveBeenLastCalledWith({ columna: "SALDO_TOTAL", descendente: true });
  });

  it("dice que no hay filas en lugar de una tabla vacia", () => {
    render(<TablaDatos titulo="T" columnas={COLUMNAS} filas={[]} vacio="Ninguna cuenta." />);
    expect(screen.getByText("Ninguna cuenta.")).toBeInTheDocument();
  });
});

describe("RegionDeTabla", () => {
  it("es una region con nombre que se alcanza con el teclado, para desplazar su tabla", async () => {
    render(
      <RegionDeTabla etiqueta="Reglas de la política">
        <table>
          <caption>Reglas</caption>
        </table>
      </RegionDeTabla>,
    );

    const region = screen.getByRole("region", { name: "Reglas de la política" });
    expect(region).toHaveClass("tabla-contenedor");
    expect(region).not.toHaveClass("tabla-contenedor--compacta");
    expect(within(region).getByRole("table", { name: "Reglas" })).toBeInTheDocument();
    await userEvent.tab();
    expect(region).toHaveFocus();
  });

  it("la compacta va sin marco, dentro de su tarjeta", () => {
    render(
      <RegionDeTabla etiqueta="Por base" compacta>
        <table />
      </RegionDeTabla>,
    );

    expect(screen.getByRole("region", { name: "Por base" })).toHaveClass(
      "tabla-contenedor",
      "tabla-contenedor--compacta",
    );
  });
});

describe("Paginacion", () => {
  it("pide la pagina anterior y la siguiente, y deshabilita lo que no existe", async () => {
    const onPagina = vi.fn();
    const { rerender } = render(
      <Paginacion total={120} pagina={1} porPagina={50} onPagina={onPagina} etiqueta="cuentas" />,
    );

    expect(screen.getByText(/Página 1 de 3 · 120 filas/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Anterior/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Siguiente/ }));
    expect(onPagina).toHaveBeenCalledWith(2);

    rerender(
      <Paginacion total={120} pagina={3} porPagina={50} onPagina={onPagina} etiqueta="cuentas" />,
    );
    expect(screen.getByRole("button", { name: /Siguiente/ })).toBeDisabled();
  });

  it("ofrece 25, 50 y 100 filas por pagina", async () => {
    const onPorPagina = vi.fn();
    render(
      <Paginacion
        total={1}
        pagina={1}
        porPagina={50}
        onPagina={vi.fn()}
        onPorPagina={onPorPagina}
        etiqueta="cuentas"
      />,
    );

    const selector = screen.getByLabelText("Filas por página");
    expect(
      within(selector)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["25", "50", "100"]);
    expect(screen.getByText(/1 fila$/)).toBeInTheDocument();
    await userEvent.selectOptions(selector, "100");
    expect(onPorPagina).toHaveBeenCalledWith(100);
  });
});
