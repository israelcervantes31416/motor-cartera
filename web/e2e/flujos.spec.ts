/** Los flujos principales, contra la API real: del selector a cada seccion, con el contexto. */
import { abrir, datos, enContexto, expect, test } from "./datos";

test("del selector al resumen: elegir un corte y abrir un contexto compatible", async ({
  page,
}) => {
  const { cartera, contexto } = datos();
  await page.goto(`/?cartera=${cartera}`);
  await expect(page.getByRole("heading", { name: "Abrir un contexto operativo" })).toBeVisible();

  const fila = page.getByRole("row").filter({ hasText: contexto.corte.slice(0, 8) });
  await fila.getByRole("link", { name: "Usar este corte" }).click();
  await expect(page).toHaveURL(new RegExp(`corte=${contexto.corte}`));
  // Nada se abre solo: el selector sigue en pantalla hasta que se pide abrir.
  await expect(
    page.getByRole("heading", { name: "Contextos compatibles del corte" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/\?/);

  const sugerencia = page
    .getByRole("listitem")
    .filter({ hasText: "Una optimización y lo que declaró" })
    .first();
  await sugerencia.getByRole("link", { name: "Abrir este contexto" }).click();
  await expect(page).toHaveURL(/\/resumen\?/);
  await expect(page.getByRole("heading", { level: 1, name: "Resumen" })).toBeVisible();
  await expect(page.getByText("6 de 6 ejecuciones declaradas")).toBeVisible();
});

const SECCIONES: readonly (readonly [string, string | RegExp])[] = [
  ["/resumen", "Resumen"],
  ["/cuentas", "Cuentas"],
  ["/analytics", "Analytics"],
  ["/predictivo/modelo", "Predictivo"],
  ["/predictivo/desempeno", "Predictivo"],
  ["/predictivo/calibracion", "Predictivo"],
  ["/predictivo/scoring", "Predictivo"],
  ["/predictivo/drift", "Predictivo"],
  ["/optimizacion/resumen", "Optimización"],
  ["/optimizacion/capacidad", "Optimización"],
  ["/optimizacion/restricciones", "Optimización"],
  ["/optimizacion/asignaciones", "Optimización"],
  ["/optimizacion/solver", "Optimización"],
  ["/campo", "Campo"],
  ["/ruteo", "Ruteo"],
  ["/auditoria", "Auditoría y linaje"],
];

for (const [ruta, encabezado] of SECCIONES) {
  test(`${ruta} abre con su contenido y sin errores`, async ({ page }) => {
    await abrir(page, enContexto(ruta), encabezado);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

test("la Cuenta 360 y cada una de sus pestanas", async ({ page }) => {
  const { cuenta } = datos();
  await abrir(page, enContexto(`/cuentas/${cuenta}`), /Cuenta 360/);
  await expect(
    page.getByRole("heading", { name: "Lo que publicó cada ejecución del contexto" }),
  ).toBeVisible();

  const pestanas = page.getByRole("navigation", { name: "Secciones de la cuenta" });
  for (const nombre of [
    "Historia",
    "Pagos",
    "Gestiones",
    "Decisión",
    "Geografía y territorial",
    "Campo",
    "Ruteo",
    "Predictivo",
    "Optimización",
    "Evidencia",
  ]) {
    await pestanas.getByRole("link", { name: nombre, exact: true }).click();
    await expect(pestanas.getByRole("link", { name: nombre, exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(page.locator(".cargando")).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
  }
});

test("de la lista de cuentas a una Cuenta 360, con el contexto en la URL", async ({ page }) => {
  await abrir(page, enContexto("/cuentas"), "Cuentas");
  const enlace = page.getByRole("link", { name: /^Abrir la cuenta / }).first();
  await enlace.click();
  await expect(page).toHaveURL(/\/cuentas\/[0-9a-f-]{36}\?corte=/);
  await expect(page.getByRole("heading", { level: 1, name: /Cuenta 360/ })).toBeVisible();
});

test("el explorador pagina en el servidor y la URL lo recuerda", async ({ page }) => {
  await abrir(page, enContexto("/cuentas"), "Cuentas");
  const pedida = page.waitForRequest(
    (r) => r.url().includes("/api/app/v1/cuentas?") && r.url().includes("pagina=2"),
  );
  await page.getByRole("button", { name: /Siguiente/ }).click();
  await pedida;
  await expect(page).toHaveURL(/pagina=2/);

  // Recargar la misma URL reconstruye la misma vista.
  await page.reload();
  await expect(page.getByText(/Página 2 de/)).toBeVisible();
});

test("el detalle de una ruta: sus paradas en orden, sin mapa", async ({ page }) => {
  const { ruta } = datos();
  await abrir(page, enContexto(`/ruteo/rutas/${ruta}`), "Ruta");
  await expect(page.getByRole("heading", { name: "Paradas en orden de visita" })).toBeVisible();
  await expect(page.locator("canvas")).toHaveCount(0);
});

test("una fecha de negocio no se mueve con la zona del navegador", async ({ page }) => {
  await abrir(page, enContexto("/resumen"), "Resumen");
  // El corte de la prueba es un dia fijo; el navegador esta en America/Tijuana (UTC-7 o -8).
  const { contexto } = datos();
  const respuesta = await page.request.get(
    `/api/app/v1/contextos/validar?corte_id=${contexto.corte}`,
  );
  const { corte } = (await respuesta.json()) as { corte: { fecha_corte: string } };
  const [anio, , dia] = corte.fecha_corte.split("-");
  await expect(
    page.getByText(new RegExp(`corte del ${String(Number(dia))} \\w+ ${anio ?? ""}`)),
  ).toBeVisible();
});
