/** Los casos de error: la API que falla, un contexto que no encadena, lo que no existe y el proxy
 * de solo lectura. */
import { abrir, datos, enContexto, expect, NO_EXISTE, test } from "./datos";

test("si la API falla, la pantalla lo dice y deja reintentar, sin quedarse cargando", async ({
  page,
}) => {
  await page.route("**/api/app/v1/resumen?*", (ruta) =>
    ruta.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        codigo: "BASE_NO_DISPONIBLE",
        mensaje: "La base no responde.",
        detalles: [],
        run_id: null,
        pagos_run_id: null,
      }),
    }),
  );
  await page.goto(enContexto("/resumen"));
  const alerta = page.getByRole("alert");
  await expect(alerta).toContainText("La API no está disponible");
  await expect(alerta.getByRole("button", { name: "Reintentar" })).toBeVisible();
  await expect(page.locator(".cargando")).toHaveCount(0);
});

test("si la API no responde, la pantalla lo dice", async ({ page }) => {
  await page.route("**/api/app/v1/linaje?*", (ruta) => ruta.abort("connectionrefused"));
  await page.goto(enContexto("/auditoria"));
  await expect(page.getByRole("alert")).toContainText("La API no está disponible");
});

test("un contexto con una ejecucion que no existe no se combina", async ({ page }) => {
  await abrir(
    page,
    enContexto("/resumen", { ...datos().contexto, optimizacion: NO_EXISTE }),
    "Resumen",
  );
  await expect(page.getByRole("heading", { name: /El contexto no encadena/ })).toBeVisible();
  await expect(page.getByText(`No existe la optimizacion ${NO_EXISTE}.`)).toBeVisible();
});

test("un contexto que mezcla cortes no se combina, y dice que ejecuciones", async ({ page }) => {
  const { cartera, contexto } = datos();
  const respuesta = await page.request.get(
    `/api/app/v1/contextos/opciones?cartera_id=${cartera}&por_pagina=100`,
  );
  const { cortes } = (await respuesta.json()) as { cortes: { corte_id: string }[] };
  const otro = cortes.find((c) => c.corte_id !== contexto.corte);
  test.skip(otro === undefined, "la cartera de la prueba tiene un solo corte");
  await abrir(
    page,
    enContexto("/resumen", { ...contexto, corte: otro?.corte_id ?? "" }),
    "Resumen",
  );
  await expect(page.getByRole("heading", { name: /El contexto no encadena/ })).toBeVisible();
  await expect(page.getByText("Otro corte").first()).toBeVisible();
});

test("una cuenta que no existe es un aviso", async ({ page }) => {
  await page.goto(enContexto(`/cuentas/${NO_EXISTE}`));
  await expect(page.getByRole("heading", { name: "Cuenta no encontrada" })).toBeVisible();
});

test("una ruta de la app que no existe es un 404 util", async ({ page }) => {
  await page.goto("/esto-no-existe");
  await expect(page.getByRole("heading", { name: "Página no encontrada" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ir al selector de contexto" })).toBeVisible();
});

test("el proxy solo deja leer: otro metodo es 405 y una ruta fuera de la lista es 404", async ({
  request,
}) => {
  const escritura = await request.post("/api/app/v1/contextos/opciones", { data: {} });
  expect(escritura.status()).toBe(405);
  expect(((await escritura.json()) as { codigo: string }).codigo).toBe("METODO_NO_PERMITIDO");

  const borrado = await request.delete("/api/optimizacion/ejecuciones");
  expect(borrado.status()).toBe(405);

  // Las rutas de dominio que llevan datos personales no pasan por el proxy de la app.
  for (const ruta of ["/api/cuentas", "/api/gestiones", `/api/cuentas/${NO_EXISTE}`]) {
    const respuesta = await request.get(ruta);
    expect(respuesta.status(), ruta).toBe(404);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("RUTA_NO_EXPUESTA");
  }

  const salud = await request.get("/api/salud");
  expect(salud.ok()).toBe(true);
});
