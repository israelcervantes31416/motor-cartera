/** A 390 x 844: el menu se pliega, nada desborda la pagina a lo ancho y las tablas se desplazan
 * dentro de su contenedor. */
import { abrir, datos, enContexto, expect, test } from "./datos";

async function sinDesborde(page: import("@playwright/test").Page): Promise<void> {
  // Contra el ancho del dispositivo, no contra window.innerWidth: en movil, Chrome achica la pagina
  // para que quepa lo que desborda, y innerWidth crece con ella (asi la comparacion nunca fallaba).
  const ancho = page.viewportSize()?.width ?? 0;
  const contenido = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(ancho).toBeGreaterThan(0);
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
  // Sus reglas, con sus condiciones en codigo, son la tabla mas ancha de la app.
  [
    "la decision de una cuenta",
    () => enContexto(`/cuentas/${datos().cuenta}/decision`),
    /Cuenta 360/,
  ],
  ["analytics", () => enContexto("/analytics"), "Analytics"],
  ["el desempeno predictivo", () => enContexto("/predictivo/desempeno"), "Predictivo"],
  ["campo", () => enContexto("/campo"), "Campo"],
  ["ruteo", () => enContexto("/ruteo"), "Ruteo"],
  ["la optimizacion", () => enContexto("/optimizacion/restricciones"), "Optimización"],
  ["la auditoria", () => enContexto("/auditoria"), "Auditoría y linaje"],
] as const) {
  test(`${nombre} no desborda a lo ancho`, async ({ page }) => {
    await abrir(page, ruta(), encabezado);
    await sinDesborde(page);
  });
}
