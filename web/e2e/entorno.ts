/**
 * Contra que entorno corren las pruebas, y lo que necesita el de produccion local.
 *
 * Con MC_E2E_PRODUCCION=1 corren contra el compose de produccion (docker-compose.production.yml):
 * su edge con TLS en https://motor-cartera.localhost:8443 y usuarios de verdad que inician sesion
 * en Dex. De secretos/ (scripts/generar_secretos_locales.py) se leen la CA efimera, el certificado
 * del edge y las contrasenas de esos usuarios. Ninguna verificacion de TLS se apaga: Chromium acepta
 * solo el certificado del edge, por su llave publica, y Node confia en la CA del compose.
 */
import { createHash, X509Certificate } from "node:crypto";
import dns, { type LookupAddress, type LookupOptions } from "node:dns";
import { readFileSync } from "node:fs";
import tls from "node:tls";
import { fileURLToPath } from "node:url";

import { expect, type Page } from "@playwright/test";

export const PRODUCCION = process.env["MC_E2E_PRODUCCION"] === "1";
export const BASE =
  process.env["MC_E2E_URL"] ??
  (PRODUCCION ? "https://motor-cartera.localhost:8443" : "http://localhost:4173");
const SECRETOS =
  process.env["MC_E2E_SECRETOS"] ?? fileURLToPath(new URL("../../secretos/", import.meta.url));

function secreto(nombre: string): string {
  return readFileSync(`${SECRETOS}/${nombre}`, "utf8");
}

/** El SHA-256 (base64) de la llave publica del certificado del edge, para Chromium. */
export function spkiDelEdge(): string {
  const certificado = new X509Certificate(secreto("edge_tls_certificado"));
  const llave = certificado.publicKey.export({ type: "spki", format: "der" });
  return createHash("sha256").update(llave).digest("base64");
}

type Retorno = (error: NodeJS.ErrnoException | null, ...resultado: unknown[]) => void;

/**
 * Node, como el resolvedor de Chromium, resuelve *.localhost a loopback (RFC 6761; en Windows
 * getaddrinfo no lo hace), y confia en la CA efimera del compose ademas de en las de siempre. Solo
 * en este proceso de pruebas: Playwright carga esta configuracion en cada worker.
 */
export function prepararNode(): void {
  const esLocal = (nombre: unknown): boolean =>
    typeof nombre === "string" && nombre.endsWith(".localhost");
  const loopback: LookupAddress = { address: "127.0.0.1", family: 4 };
  const original = dns.lookup.bind(dns) as (...args: unknown[]) => void;
  const lookup = (nombre: unknown, opciones: unknown, retorno?: unknown): void => {
    if (!esLocal(nombre)) {
      original(nombre, opciones, retorno);
      return;
    }
    const callback = (typeof opciones === "function" ? opciones : retorno) as Retorno;
    const todas =
      typeof opciones === "object" && opciones !== null && (opciones as LookupOptions).all === true;
    process.nextTick(() => {
      if (todas) callback(null, [loopback]);
      else callback(null, loopback.address, loopback.family);
    });
  };
  Object.assign(dns, { lookup });
  const promesas = dns.promises;
  const lookupPromesas = promesas.lookup.bind(promesas);
  Object.assign(promesas, {
    lookup: (nombre: string, opciones?: LookupOptions) =>
      esLocal(nombre)
        ? Promise.resolve(opciones?.all === true ? [loopback] : loopback)
        : lookupPromesas(nombre, opciones ?? {}),
  });
  tls.setDefaultCACertificates([...tls.getCACertificates("default"), secreto("ca_certificado")]);
}

export type NombreDeUsuario = "lector" | "auditor" | "operador" | "administrador" | "sinpermisos";

interface Usuario {
  readonly correo: string;
  readonly contrasena: string;
}

function usuario(nombre: NombreDeUsuario): Usuario {
  const usuarios = JSON.parse(secreto("usuarios.json")) as Partial<Record<string, Usuario>>;
  const encontrado = usuarios[nombre];
  if (encontrado === undefined) throw new Error(`secretos/usuarios.json no tiene a ${nombre}`);
  return encontrado;
}

/** Donde la preparacion deja la sesion de un usuario (e2e/.sesiones/, fuera del repositorio). */
export function archivoDeSesion(nombre: NombreDeUsuario): string {
  return fileURLToPath(new URL(`./.sesiones/${nombre}.json`, import.meta.url));
}

/**
 * El inicio de sesion de verdad, como lo hace una persona: la app ofrece iniciar sesion, oauth2-proxy
 * manda a Dex (con PKCE, state y nonce), se llena el formulario y el callback regresa a la app con
 * su cookie de sesion.
 */
export async function iniciarSesion(page: Page, nombre: NombreDeUsuario): Promise<void> {
  const { correo, contrasena } = usuario(nombre);
  await page.goto("/");
  await page.getByRole("link", { name: "Iniciar sesión" }).click();
  await page.locator('input[name="login"]').fill(correo);
  await page.locator('input[name="password"]').fill(contrasena);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(
    (url) =>
      url.origin === new URL(BASE).origin &&
      !url.pathname.startsWith("/dex/") &&
      !url.pathname.startsWith("/oauth2/"),
  );
  await expect(page.getByRole("heading", { name: "Inicia sesión" })).toHaveCount(0);
}
