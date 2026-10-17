import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LimiteDeErrores } from "../app/Errores";
import { ErrorDeApi, ErrorDeRed } from "../api/cliente";
import { ErrorDeCarga, mensajeDeError, Vacio } from "./Carga";
import { Copiable } from "./Copiar";
import { Figura } from "./Graficas";
import { Ficha, Indicador, Indicadores } from "./Kpi";

describe("Copiable", () => {
  it("muestra el identificador abreviado y copia el completo", async () => {
    const escribir = vi.fn<(texto: string) => Promise<void>>().mockResolvedValue();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: escribir },
      configurable: true,
    });
    const valor = "8bd3f8bb-0aad-5f6e-939f-cba3e42a560b";
    render(<Copiable valor={valor} etiqueta="el corte" />);

    expect(screen.getByText("8bd3f8bb…")).toHaveAttribute("title", valor);
    await userEvent.click(screen.getByRole("button", { name: `Copiar el corte: ${valor}` }));
    expect(escribir).toHaveBeenCalledWith(valor);
    expect(await screen.findByText("Copiado")).toBeInTheDocument();
  });

  it("dice si no pudo copiar, sin romperse", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: () => Promise.reject(new Error("sin permiso")) },
      configurable: true,
    });
    render(<Copiable valor="abc" etiqueta="x" />);

    await userEvent.click(screen.getByRole("button"));
    expect(await screen.findByText("No se pudo copiar")).toBeInTheDocument();
  });
});

describe("los estados de una carga", () => {
  it("explica cada falla sin un traceback", () => {
    expect(mensajeDeError(new ErrorDeRed("TIEMPO_AGOTADO", "tardo", "/r")).titulo).toBe(
      "La API tardó demasiado en responder",
    );
    expect(mensajeDeError(new ErrorDeRed("SIN_CONEXION", "x", "/r")).titulo).toBe(
      "La API no está disponible",
    );
    expect(mensajeDeError(new ErrorDeApi(422, "ENTRADA_INVALIDA", "x", [], "/r")).titulo).toBe(
      "La API respondió ENTRADA_INVALIDA",
    );
    expect(mensajeDeError(new Error("interno")).detalle).toBe("La pantalla no se pudo mostrar.");
  });

  it("muestra los detalles de un error de la API y deja reintentar", async () => {
    const reintentar = vi.fn();
    render(
      <ErrorDeCarga
        error={
          new ErrorDeApi(
            422,
            "ENTRADA_INVALIDA",
            "No cumple el contrato.",
            [{ campo: "query.pagina", problema: "Debe ser mayor que 0." }],
            "/app/v1/cuentas",
          )
        }
        reintentar={reintentar}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("query.pagina Debe ser mayor que 0.");
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(reintentar).toHaveBeenCalledOnce();
  });

  it("distingue sin filas de fuente no disponible", () => {
    render(<Vacio>Sin filas.</Vacio>);
    expect(screen.getByText("Sin filas.")).toBeInTheDocument();
  });
});

describe("figuras, indicadores y fichas", () => {
  it("toda grafica tiene titulo, descripcion y su tabla detras de Ver datos", () => {
    render(
      <Figura titulo="Serie" descripcion="Dos periodos." datos={<table aria-label="datos" />}>
        <svg />
      </Figura>,
    );

    const figura = screen.getByRole("figure", { name: "Serie" });
    expect(figura).toHaveAccessibleDescription("Dos periodos.");
    expect(screen.getByText("Ver datos")).toBeInTheDocument();
  });

  it("los indicadores y las fichas son listas de definiciones", () => {
    render(
      <>
        <Indicadores etiqueta="Del plan">
          <Indicador etiqueta="Asignadas" detalle="de 1,000">
            39
          </Indicador>
        </Indicadores>
        <Ficha etiqueta="La ficha" filas={[["Version", "v1"], null, false]} />
      </>,
    );

    expect(screen.getAllByRole("term").map((t) => t.textContent)).toEqual(["Asignadas", "Version"]);
    expect(screen.getByText("de 1,000")).toBeInTheDocument();
  });
});

describe("el limite de errores", () => {
  it("un error de render muestra un mensaje util, no una pantalla en blanco", () => {
    const silencio = vi.spyOn(console, "error").mockImplementation(() => undefined);
    function Rota(): never {
      throw new Error("fallo de render");
    }
    render(
      <LimiteDeErrores>
        <Rota />
      </LimiteDeErrores>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("La pantalla no se pudo mostrar");
    expect(screen.getByRole("alert")).not.toHaveTextContent("fallo de render");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
    silencio.mockRestore();
  });
});
