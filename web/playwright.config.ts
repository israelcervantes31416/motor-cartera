import { defineConfig } from "@playwright/test";

// Contra que corre: la app del compose (nginx en 8080, con la API y su prueba de humo detras) si
// MC_E2E_URL lo dice; si no, el build de produccion servido por `vite preview`, que pasa por el
// mismo proxy de solo lectura hacia la API local (MC_URL_API).
const URL_E2E = process.env["MC_E2E_URL"];
const BASE = URL_E2E ?? "http://localhost:4173";
// En el CI, el Chromium de Playwright; en una maquina sin el, el navegador instalado ("chrome" o
// "msedge").
const CANAL = process.env["MC_E2E_CANAL"];

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/preparacion.ts",
  // Una sola app y una sola base: las pruebas no compiten por ellas.
  workers: 1,
  fullyParallel: false,
  forbidOnly: process.env["CI"] !== undefined,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: BASE,
    locale: "es-MX",
    // Una zona del navegador distinta de UTC: una fecha de negocio no debe moverse por ella.
    timezoneId: "America/Tijuana",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...(CANAL !== undefined ? { channel: CANAL } : {}),
  },
  projects: [
    {
      name: "escritorio",
      testIgnore: /movil\.spec\.ts/,
      use: { browserName: "chromium", viewport: { width: 1440, height: 900 } },
    },
    {
      name: "movil",
      testMatch: /movil\.spec\.ts/,
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 2,
      },
    },
  ],
  ...(URL_E2E === undefined
    ? {
        webServer: {
          command: "npm run preview",
          url: BASE,
          reuseExistingServer: true,
          timeout: 60_000,
        },
      }
    : {}),
});
