/**
 * El proxy de solo lectura del servidor de desarrollo y del de preview de Vite: el mismo que nginx
 * en el compose. Lee la lista blanca de nginx/rutas-api.conf (la unica fuente), deja pasar solo GET
 * de esas rutas, y agrega la clave de la API del lado del servidor: el navegador nunca la ve.
 */
import { readFileSync } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import { fileURLToPath } from "node:url";
import type { ProxyOptions } from "vite";

const ARCHIVO = fileURLToPath(new URL("./nginx/rutas-api.conf", import.meta.url));

/** Las expresiones de la lista blanca, en el orden del archivo: `"~<expresion>" 1;`, entre
 * comillas como las exige nginx a una expresion con llaves. */
export function rutasPermitidas(texto = readFileSync(ARCHIVO, "utf8")): RegExp[] {
  return texto
    .split(/\r?\n/)
    .map((linea) => /^"?~([^"\s]+)"?\s+1;$/.exec(linea.trim()))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => new RegExp(m[1] ?? "$^"));
}

/** La ruta como la ve nginx en $uri: sin la query string y con sus segmentos `.` y `..` resueltos. */
export function rutaNormalizada(url: string): { ruta: string; consulta: string } {
  const analizada = new URL(url, "http://superficie.local");
  return { ruta: analizada.pathname, consulta: analizada.search };
}

function responder(res: ServerResponse, estado: number, codigo: string, mensaje: string): void {
  res.statusCode = estado;
  res.setHeader("Content-Type", "application/json");
  if (estado === 405) res.setHeader("Allow", "GET");
  res.end(JSON.stringify({ codigo, mensaje, detalles: [] }));
}

export function proxyDeLaApi(objetivo: string, clave: string): Record<string, ProxyOptions> {
  const permitidas = rutasPermitidas();
  return {
    "/api": {
      target: objetivo,
      changeOrigin: true,
      headers: { "X-API-Key": clave },
      rewrite: (url) => {
        const { ruta, consulta } = rutaNormalizada(url);
        return ruta.replace(/^\/api/, "") + consulta;
      },
      bypass: (req: IncomingMessage, res: ServerResponse | undefined) => {
        if (res === undefined) return false;
        const { ruta } = rutaNormalizada(req.url ?? "/");
        if (req.method !== "GET") {
          responder(res, 405, "METODO_NO_PERMITIDO", "La superficie web es de solo lectura.");
          return ruta;
        }
        if (!permitidas.some((expresion) => expresion.test(ruta))) {
          responder(res, 404, "RUTA_NO_EXPUESTA", "La superficie web no expone esa ruta.");
          return ruta;
        }
        return undefined;
      },
    },
  };
}
