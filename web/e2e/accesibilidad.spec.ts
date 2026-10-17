/** axe-core en el navegador (con contraste incluido) sobre las pantallas principales: cero
 * violaciones serias o criticas. No es una certificacion: es lo que una herramienta puede medir. */
import AxeBuilder from "@axe-core/playwright";

import { abrir, datos, enContexto, expect, test } from "./datos";

type PaginaDeAxe = ConstructorParameters<typeof AxeBuilder>[0]["page"];

const PANTALLAS: readonly (readonly [string, () => string, string | RegExp])[] = [
  ["el selector", () => `/?corte=${datos().contexto.corte}`, "Abrir un contexto operativo"],
  ["el resumen", () => enContexto("/resumen"), "Resumen"],
  ["las cuentas", () => enContexto("/cuentas"), "Cuentas"],
  ["la Cuenta 360", () => enContexto(`/cuentas/${datos().cuenta}`), /Cuenta 360/],
  ["analytics", () => enContexto("/analytics"), "Analytics"],
  ["el modelo predictivo", () => enContexto("/predictivo/modelo"), "Predictivo"],
  ["el drift", () => enContexto("/predictivo/drift"), "Predictivo"],
  ["la optimizacion", () => enContexto("/optimizacion/resumen"), "Optimización"],
  ["las asignaciones", () => enContexto("/optimizacion/asignaciones"), "Optimización"],
  ["la auditoria", () => enContexto("/auditoria"), "Auditoría y linaje"],
];

for (const [nombre, ruta, encabezado] of PANTALLAS) {
  test(`${nombre}: sin violaciones serias ni criticas de accesibilidad`, async ({ page }) => {
    await abrir(page, ruta(), encabezado);
    // Las tablas equivalentes de las graficas, abiertas: tambien se revisan.
    for (const detalle of await page.locator("details.figura__datos").all()) {
      await detalle.evaluate((d) => {
        (d as HTMLDetailsElement).open = true;
      });
    }
    // @axe-core/playwright declara el Page de su propia version de Playwright, sin los metodos
    // nuevos de esta: es la misma pagina, y solo el tipo difiere.
    const resultado = await new AxeBuilder({ page: page as unknown as PaginaDeAxe })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    const graves = resultado.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map(
        (v) => `${v.id} (${v.impact ?? ""}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
      );
    expect(graves).toEqual([]);
  });
}

test("se usa con el teclado: saltar al contenido y un foco visible", async ({ page }) => {
  await abrir(page, enContexto("/resumen"), "Resumen");
  await page.keyboard.press("Tab");
  const saltar = page.getByRole("link", { name: "Saltar al contenido" });
  await expect(saltar).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#contenido")).toBeFocused();
});
