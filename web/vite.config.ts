import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

import { proxyDeLaApi } from "./proxy-api.ts";

/** El preview guarda los archivos con hash como nginx: un ano, `immutable`; no cambian nunca. */
function cacheDeLosArchivos(): Plugin {
  return {
    name: "motor-cartera:cache-de-los-archivos",
    configurePreviewServer(servidor) {
      servidor.middlewares.use((peticion, respuesta, siguiente) => {
        if (peticion.url?.startsWith("/assets/")) {
          respuesta.setHeader("Cache-Control", "public, max-age=31536000, immutable");
        }
        siguiente();
      });
    },
  };
}

// La API local (uvicorn) y su clave de desarrollo, las mismas del compose. La clave se queda en el
// servidor de Vite: el proxy la agrega; nunca entra al codigo del navegador.
const API = process.env["MC_URL_API"] ?? "http://localhost:8000";
const CLAVE = process.env["MC_API_KEY"] ?? "clave-local-de-desarrollo";

// Las mismas cabeceras de seguridad que pone nginx en el compose (nginx/default.conf.template).
const CABECERAS = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "no-referrer",
};

export default defineConfig({
  plugins: [react(), cacheDeLosArchivos()],
  server: { port: 5173, strictPort: true, headers: CABECERAS, proxy: proxyDeLaApi(API, CLAVE) },
  preview: { port: 4173, strictPort: true, headers: CABECERAS, proxy: proxyDeLaApi(API, CLAVE) },
  build: {
    // Sin source maps publicos: llevarian el codigo fuente entero al navegador de cualquiera.
    sourcemap: false,
    target: "es2023",
    reportCompressedSize: false,
  },
});
