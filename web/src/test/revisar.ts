/**
 * Lo que toda pantalla cumple, revisado sobre el DOM que renderizo: sin botones de accion (la app es
 * de solo lectura), sin datos personales y sin violaciones serias o criticas de accesibilidad que
 * axe-core pueda medir en jsdom (el contraste se mide en el navegador, con Playwright).
 */
import { screen } from "@testing-library/react";
import axe from "axe-core";
import { expect } from "vitest";

const ACCIONES =
  /^(ejecutar|aplicar|enviar|llamar|visitar|optimizar|reentrenar|entrenar|publicar|crear|registrar|borrar|eliminar|editar|guardar)\b/i;

export function sinAcciones(): void {
  const controles = [...screen.queryAllByRole("button"), ...screen.queryAllByRole("link")];
  const acciones = controles
    .map((c) => c.textContent.trim())
    .filter((texto) => ACCIONES.test(texto));
  expect(acciones, "la app es de solo lectura: ningun boton ni enlace de accion").toEqual([]);
}

// Valores, no palabras: los avisos y el catalogo nombran CLIENTE_UNICO o CURP para decir que no se
// usan. Un CLIENTE_UNICO tiene la forma CU y diez digitos; una coordenada, un par de decimales
// largos; y un objeto volcado como JSON dejaria ver sus llaves.
const PERSONALES = [
  /CU\d{10}/,
  /-?\d{1,2}\.\d{4,},\s*-?\d{2,3}\.\d{4,}/,
  /"(cliente_unico|actor_ref|latitud|longitud|lat|lon|geometry)"\s*:/i,
];

export function sinDatosPersonales(raiz: HTMLElement = document.body): void {
  const texto = raiz.textContent;
  for (const patron of PERSONALES) {
    expect(texto, `la pantalla muestra ${String(patron)}`).not.toMatch(patron);
  }
}

export async function sinViolacionesGraves(raiz: HTMLElement = document.body): Promise<void> {
  const resultado = await axe.run(raiz, {
    resultTypes: ["violations"],
    rules: { "color-contrast": { enabled: false } },
  });
  const graves = resultado.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(graves).toEqual([]);
}
