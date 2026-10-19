/** Lo que la app muestra y lo que su proxy deja pasar, recorrido entero: ningun dato personal, ni
 * en el DOM ni en una respuesta de /api (la prueba base revisa cada respuesta). */
import { abrir, datos, enContexto, expect, PERSONALES, test } from "./datos";
import { BASE } from "./entorno";

test("ninguna pantalla ni respuesta lleva un dato personal ni una coordenada", async ({
  page,
  revision,
}) => {
  const { cuenta, ruta } = datos();
  const pantallas: readonly (readonly [string, string | RegExp])[] = [
    [enContexto("/resumen"), "Resumen"],
    [enContexto("/cuentas"), "Cuentas"],
    [enContexto(`/cuentas/${cuenta}`), /Cuenta 360/],
    [enContexto(`/cuentas/${cuenta}/historia`), /Cuenta 360/],
    [enContexto(`/cuentas/${cuenta}/pagos`), /Cuenta 360/],
    [enContexto(`/cuentas/${cuenta}/gestiones`), /Cuenta 360/],
    [enContexto(`/cuentas/${cuenta}/geografia`), /Cuenta 360/],
    [enContexto(`/cuentas/${cuenta}/ruteo`), /Cuenta 360/],
    [enContexto(`/cuentas/${cuenta}/evidencia`), /Cuenta 360/],
    [enContexto("/optimizacion/asignaciones"), "Optimización"],
    [enContexto("/campo"), "Campo"],
    [enContexto(`/ruteo/rutas/${ruta}`), "Ruta"],
  ];
  for (const [url, encabezado] of pantallas) {
    await abrir(page, url, encabezado);
    const texto = await page.locator("body").innerText();
    for (const patron of PERSONALES) expect(texto, url).not.toMatch(patron);
    // Ni coordenadas: un par de decimales largos.
    expect(texto, url).not.toMatch(/-?\d{1,2}\.\d{5,},\s*-?\d{2,3}\.\d{5,}/);
  }
  expect(revision.respuestas.length).toBeGreaterThan(10);
});

test("nada se carga de fuera: todas las peticiones van al mismo origen", async ({ page }) => {
  // El origen de la app, sea el de desarrollo o el edge de produccion: ni otro host ni otro puerto.
  const origen = new URL(BASE).origin;
  const externas: string[] = [];
  page.on("request", (peticion) => {
    const url = new URL(peticion.url());
    if (url.origin !== origen && url.protocol !== "data:") externas.push(peticion.url());
  });
  await abrir(page, enContexto("/resumen"), "Resumen");
  await abrir(page, enContexto("/analytics"), "Analytics");
  expect(externas).toEqual([]);
});

test("no hay botones de accion: la app es de solo lectura", async ({ page }) => {
  for (const [url, encabezado] of [
    [enContexto("/resumen"), "Resumen"],
    [enContexto(`/cuentas/${datos().cuenta}`), /Cuenta 360/],
    [enContexto("/optimizacion/resumen"), "Optimización"],
  ] as const) {
    await abrir(page, url, encabezado);
    const acciones = page.getByRole("button", {
      name: /^(ejecutar|aplicar|enviar|llamar|visitar|optimizar|reentrenar|guardar|crear|registrar)/i,
    });
    await expect(acciones).toHaveCount(0);
  }
});
