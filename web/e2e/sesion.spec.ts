/**
 * La sesion de verdad, contra el compose de produccion (MC_E2E_PRODUCCION=1): iniciar sesion en
 * Dex, lo que cada permiso deja ver, cerrar sesion (tambien en el servidor), una sesion que expira
 * mientras se usa la app y que todo vaya por HTTPS sin un token en el navegador. Contra el compose de
 * desarrollo no hay usuarios: se saltan.
 */
import { abrir, enContexto, expect, test } from "./datos";
import { archivoDeSesion, BASE, iniciarSesion, PRODUCCION } from "./entorno";

test.skip(!PRODUCCION, "solo contra el compose de produccion, que tiene usuarios");

const SIN_SESION = { cookies: [], origins: [] };

test.describe("sin sesion", () => {
  test.use({ storageState: SIN_SESION });

  test("la app pide iniciar sesion y la API no responde ningun dato", async ({ page }) => {
    await page.goto(enContexto("/resumen"));
    await expect(page.getByRole("heading", { level: 1, name: "Inicia sesión" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Iniciar sesión" })).toHaveAttribute(
      "href",
      /^\/oauth2\/start\?rd=%2Fresumen/,
    );

    const respuesta = await page.request.get("/api/app/v1/contextos/opciones?por_pagina=1");
    expect(respuesta.status()).toBe(401);
    expect(((await respuesta.json()) as { codigo: string }).codigo).toBe("SESION_REQUERIDA");
  });

  test("al iniciar sesion regresa a la pantalla que se pidio", async ({ page }) => {
    await iniciarSesion(page, "operador");

    await expect(page.getByText(/Sesión iniciada/)).toBeVisible();
    await page.goto("/resumen");
    await expect(page.getByRole("heading", { level: 1, name: "Inicia sesión" })).toHaveCount(0);
  });
});

test.describe("con permiso de lectura y sin el de auditoria", () => {
  test.use({ storageState: archivoDeSesion("lector") });

  test("lee, pero la auditoria ni se ofrece ni responde", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Sesión iniciada · lectura", { exact: true })).toBeVisible();
    const secciones = page.getByRole("navigation", { name: "Secciones" });
    await expect(secciones.getByRole("link", { name: "Resumen" })).toBeVisible();
    await expect(secciones.getByRole("link", { name: "Auditoría" })).toHaveCount(0);

    const linaje = await page.request.get("/api/app/v1/linaje");
    expect(linaje.status()).toBe(403);
    expect(((await linaje.json()) as { codigo: string }).codigo).toBe("PERMISO_INSUFICIENTE");
  });
});

test.describe("sin ningun permiso", () => {
  test.use({ storageState: archivoDeSesion("sinpermisos") });

  test("la sesion existe, pero la app dice que no puede leer nada", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { level: 1, name: "Sin permiso de lectura" }),
    ).toBeVisible();
    const respuesta = await page.request.get("/api/app/v1/contextos/opciones?por_pagina=1");
    expect(respuesta.status()).toBe(403);
  });
});

test.describe("una sesion propia", () => {
  // Estas pruebas cierran o pierden su sesion: abren la suya, nunca la compartida del auditor.
  test.use({ storageState: SIN_SESION });

  test("cerrar sesion la termina tambien en el servidor", async ({ page, browser }) => {
    await iniciarSesion(page, "operador");
    const guardada = await page.context().storageState();

    await page.getByRole("button", { name: "Cerrar sesión" }).click();

    await expect(page.getByRole("heading", { level: 1, name: "Inicia sesión" })).toBeVisible();
    // La cookie de antes ya no abre nada: la sesion se borro del almacen de sesiones.
    const otro = await browser.newContext({ baseURL: BASE, storageState: guardada });
    try {
      const respuesta = await otro.request.get("/api/app/v1/sesion");
      expect(respuesta.status()).toBe(401);
    } finally {
      await otro.close();
    }
  });

  test("una sesion que expira mientras se usa la app lleva a iniciar otra", async ({ page }) => {
    await iniciarSesion(page, "operador");
    await abrir(page, enContexto("/resumen"), "Resumen");

    await page.context().clearCookies();
    await page.getByRole("navigation", { name: "Secciones" }).getByRole("link", {
      name: "Cuentas",
    }).click();

    await expect(page.getByRole("heading", { level: 1, name: "Tu sesión expiró" })).toBeVisible();
  });
});

test("todo va por HTTPS y el navegador no guarda ningun token", async ({ page, request }) => {
  const http = await request.get("http://motor-cartera.localhost:8081/resumen", {
    maxRedirects: 0,
  });
  expect(http.status()).toBe(301);
  expect(http.headers()["location"]).toBe(`${BASE}/resumen`);

  const respuesta = await page.goto("/");
  expect(respuesta?.headers()["strict-transport-security"]).toContain("max-age=");
  await expect(page.getByText(/Sesión iniciada/)).toBeVisible();

  const almacenado = await page.evaluate(() => ({
    local: window.localStorage.length,
    sesion: window.sessionStorage.length,
    cookies: document.cookie,
  }));
  expect(almacenado).toEqual({ local: 0, sesion: 0, cookies: "" });
  const sesion = (await page.context().cookies()).find((c) => c.name === "__Host-mc_sesion");
  expect(sesion).toMatchObject({ httpOnly: true, secure: true, path: "/" });
  expect(["Lax", "Strict"]).toContain(sesion?.sameSite);
});
