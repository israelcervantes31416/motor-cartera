import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { formatearMonto } from "../lib/formato";
import { definicionDe, ESTADOS, InsigniaEstado, ValorConEstado, ValorOpcional } from "./Estado";

const SIN_VALOR_PUBLICADO = ["NO_DISPONIBLE", "SUPRIMIDO", "NO_APLICA", "CENSURADO"] as const;

describe("ValorConEstado", () => {
  it.each(SIN_VALOR_PUBLICADO)("%s nunca se muestra como cero ni como el valor", (estado) => {
    const { container } = render(
      <ValorConEstado estado={estado} valor="0" formatear={formatearMonto} />,
    );

    expect(container).not.toHaveTextContent(/\b0(\.00)?\b/);
    expect(screen.getByText(ESTADOS[estado]?.etiqueta ?? "")).toBeInTheDocument();
    expect(container.querySelector(`[data-estado="${estado}"]`)).not.toBeNull();
  });

  it("solo un DISPONIBLE con valor se formatea", () => {
    render(<ValorConEstado estado="DISPONIBLE" valor="1234.5" formatear={formatearMonto} />);
    expect(screen.getByText("1,234.50")).toBeInTheDocument();
  });

  it("un DISPONIBLE sin valor es SIN_VALOR, y sin estado es NO_DISPONIBLE: nunca un cero", () => {
    const { container, rerender } = render(
      <ValorConEstado estado="DISPONIBLE" valor={null} formatear={formatearMonto} />,
    );
    expect(container.querySelector('[data-estado="SIN_VALOR"]')).not.toBeNull();

    rerender(<ValorConEstado estado={null} valor="5" formatear={formatearMonto} />);
    expect(container.querySelector('[data-estado="NO_DISPONIBLE"]')).not.toBeNull();
    expect(container).not.toHaveTextContent("5");
  });
});

describe("ValorOpcional", () => {
  it("muestra el valor formateado o su ausencia, nunca un cero", () => {
    const { container, rerender } = render(
      <ValorOpcional valor={null} formatear={formatearMonto} />,
    );
    expect(container.querySelector('[data-estado="SIN_VALOR"]')).not.toBeNull();

    rerender(<ValorOpcional valor="0" formatear={formatearMonto} />);
    expect(container).toHaveTextContent("0.00");
  });
});

describe("InsigniaEstado", () => {
  it("lleva texto e icono, no solo color", () => {
    render(<InsigniaEstado estado="SUPRIMIDO" />);
    const insignia = screen.getByText("Suprimido");
    expect(insignia).toHaveClass("insignia");
    expect(insignia).toHaveAttribute("title", ESTADOS["SUPRIMIDO"]?.descripcion);
    expect(insignia.querySelector('[aria-hidden="true"]')).toHaveTextContent("▨");
  });

  it("un estado que no conoce se muestra con su codigo legible", () => {
    expect(definicionDe("ESTADO_NUEVO").etiqueta).toBe("Estado nuevo");
    render(<InsigniaEstado estado="ESTADO_NUEVO" />);
    expect(screen.getByText("Estado nuevo")).toBeInTheDocument();
  });

  it("cada estado tiene etiqueta, icono y descripcion", () => {
    for (const [codigo, d] of Object.entries(ESTADOS)) {
      expect(d.etiqueta, codigo).not.toBe("");
      expect(d.icono, codigo).not.toBe("");
      expect(d.descripcion, codigo).not.toBe("");
    }
  });
});
