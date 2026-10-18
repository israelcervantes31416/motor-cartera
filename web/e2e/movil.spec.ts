/** A 390 x 844: el menu se pliega, nada desborda la pagina a lo ancho y las tablas se desplazan
 * dentro de su contenedor. */
import { abrir, datos, enContexto, expect, test } from "./datos";

async function sinDesborde(page: import("@playwright/test").Page): Promise<void> {
  const { ancho, contenido } = await page.evaluate(() => ({
    ancho: window.innerWidth,
    contenido: document.documentElement.scrollWidth,
  }));
  expect(contenido).toBeLessThanOrEqual(ancho);
}

test("el menu se abre, navega y se cierra", async ({ page }) => {
  await abrir(page, enContexto("/resumen"), "Resumen");
  const navegacion = page.getByRole("navigation", { name: "Secciones" });
  await expect(navegacion).toBeHidden();
  await page.getByRole("button", { name: "Menú" }).click();
  await expect(navegacion).toBeVisible();
  await navegacion.getByRole("link", { name: "Cuentas" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Cuentas" })).toBeVisible();
  await expect(navegacion).toBeHidden();
});

for (const [nombre, ruta, encabezado] of [
  ["el selector", () => `/?corte=${datos().contexto.corte}`, "Abrir un contexto operativo"],
  ["el resumen", () => enContexto("/resumen"), "Resumen"],
  ["las cuentas", () => enContexto("/cuentas"), "Cuentas"],
  ["la Cuenta 360", () => enContexto(`/cuentas/${datos().cuenta}`), /Cuenta 360/],
  ["analytics", () => enContexto("/analytics"), "Analytics"],
  ["la optimizacion", () => enContexto("/optimizacion/restricciones"), "Optimización"],
  ["la auditoria", () => enContexto("/auditoria"), "Auditoría y linaje"],
] as const) {
  test(`${nombre} no desborda a lo ancho`, async ({ page }) => {
    await abrir(page, ruta(), encabezado);
    await sinDesborde(page);
  });
}
