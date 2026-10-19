/**
 * El cliente HTTP de la app: `fetch` con un envoltorio pequeno, tipado por el contrato que genera
 * openapi-typescript de la API (src/api/esquema.ts). Solo hay GET: la superficie es de solo lectura,
 * y su proxy no deja pasar otro metodo.
 *
 * Cada ruta es una del contrato, cada parametro de ruta se codifica con encodeURIComponent y cada
 * filtro va en la query string con URLSearchParams. Una respuesta 4xx o 5xx de la API trae su
 * `ErrorRespuesta` (codigo estable, mensaje para personas y detalles); una que no llega es un
 * ErrorDeRed: sin conexion o con el tiempo agotado.
 */
import type { paths } from "./esquema";

export const BASE_API = "/api";
export const TIEMPO_MAXIMO_MS = 30_000;

/** Lo que el cliente avisa cuando la API (o el edge) responde 401: la sesion no existe o vencio. La
 * app lo escucha para mostrar como volver a iniciarla, sin recargar ni perder la URL. */
export const EVENTO_SIN_SESION = "motor-cartera:sin-sesion";

export type Ruta = keyof paths;
type Operacion<R extends Ruta> = paths[R]["get"];
type Parametros<R extends Ruta> = Operacion<R> extends { parameters: infer P } ? P : never;
export type ParametrosDeRuta<R extends Ruta> = Parametros<R> extends { path: infer X } ? X : never;
export type ParametrosDeConsulta<R extends Ruta> =
  Parametros<R> extends { query?: infer Q } ? NonNullable<Q> : never;
export type Respuesta<R extends Ruta> =
  Operacion<R> extends { responses: { 200: { content: { "application/json": infer T } } } }
    ? T
    : never;

export interface DetalleDeError {
  readonly campo: string | null;
  readonly problema: string;
}

/** Una respuesta 4xx o 5xx: la API dijo que no, con su codigo estable. */
export class ErrorDeApi extends Error {
  override readonly name = "ErrorDeApi";
  constructor(
    readonly estado: number,
    readonly codigo: string,
    mensaje: string,
    readonly detalles: readonly DetalleDeError[],
    readonly ruta: string,
  ) {
    super(mensaje);
  }
}

export type CausaDeRed = "SIN_CONEXION" | "TIEMPO_AGOTADO" | "RESPUESTA_ILEGIBLE";

/** La API no contesto, tardo demasiado o contesto algo que no es JSON. */
export class ErrorDeRed extends Error {
  override readonly name = "ErrorDeRed";
  constructor(
    readonly causa: CausaDeRed,
    mensaje: string,
    readonly ruta: string,
  ) {
    super(mensaje);
  }
}

export type ValorDeConsulta = string | number | boolean | null | undefined;

/** La query string, en el orden de sus llaves: lo que no tiene valor no se manda. */
export function consultaComoTexto(consulta: Readonly<Record<string, ValorDeConsulta>>): string {
  const busqueda = new URLSearchParams();
  for (const [llave, valor] of Object.entries(consulta)) {
    if (valor === undefined || valor === null || valor === "") continue;
    busqueda.append(llave, String(valor));
  }
  const texto = busqueda.toString();
  return texto === "" ? "" : `?${texto}`;
}

/** La ruta con cada {parametro} reemplazado por su valor, codificado: un id es dato, no ruta. */
export function rutaConParametros(
  ruta: string,
  parametros: Readonly<Record<string, string>>,
): string {
  return ruta.replace(/\{([^}]+)\}/g, (_, nombre: string) => {
    const valor = parametros[nombre];
    if (valor === undefined || valor === "") {
      throw new Error(`Falta el parametro de ruta ${nombre} de ${ruta}.`);
    }
    return encodeURIComponent(valor);
  });
}

export interface OpcionesDeLectura<R extends Ruta> {
  readonly ruta?: ParametrosDeRuta<R>;
  readonly consulta?: ParametrosDeConsulta<R>;
  readonly signal?: AbortSignal | undefined;
  readonly tiempoMaximoMs?: number;
}

function senales(propia: AbortSignal | undefined, tiempo: AbortSignal): AbortSignal {
  if (propia === undefined) return tiempo;
  return AbortSignal.any([propia, tiempo]);
}

function esJson(respuesta: Response): boolean {
  return (respuesta.headers.get("content-type") ?? "").includes("application/json");
}

/** GET de una ruta del contrato: su respuesta 200 tipada, o un ErrorDeApi o un ErrorDeRed. Una
 * cancelacion de quien la pidio (otra pagina, otra busqueda) se propaga tal cual. */
export async function leer<R extends Ruta>(
  ruta: R,
  opciones: OpcionesDeLectura<R> = {},
): Promise<Respuesta<R>> {
  const camino =
    rutaConParametros(ruta, opciones.ruta ?? {}) + consultaComoTexto(opciones.consulta ?? {});
  const tiempo = AbortSignal.timeout(opciones.tiempoMaximoMs ?? TIEMPO_MAXIMO_MS);
  let respuesta: Response;
  try {
    respuesta = await fetch(BASE_API + camino, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: senales(opciones.signal, tiempo),
    });
  } catch (error) {
    if (opciones.signal?.aborted) throw error;
    if (tiempo.aborted) {
      throw new ErrorDeRed("TIEMPO_AGOTADO", "La API tardó demasiado en responder.", camino);
    }
    throw new ErrorDeRed("SIN_CONEXION", "No se pudo contactar a la API.", camino);
  }
  if (!respuesta.ok) {
    if (respuesta.status === 401) window.dispatchEvent(new Event(EVENTO_SIN_SESION));
    if (esJson(respuesta)) {
      const cuerpo = (await respuesta.json().catch(() => null)) as {
        codigo?: unknown;
        mensaje?: unknown;
        detalles?: unknown;
      } | null;
      if (cuerpo !== null && typeof cuerpo.codigo === "string") {
        throw new ErrorDeApi(
          respuesta.status,
          cuerpo.codigo,
          typeof cuerpo.mensaje === "string" ? cuerpo.mensaje : `HTTP ${respuesta.status}`,
          Array.isArray(cuerpo.detalles) ? (cuerpo.detalles as DetalleDeError[]) : [],
          camino,
        );
      }
    }
    throw new ErrorDeApi(
      respuesta.status,
      `HTTP_${respuesta.status}`,
      respuesta.status >= 500
        ? "La API no está disponible o falló al responder."
        : `La API respondió ${respuesta.status}.`,
      [],
      camino,
    );
  }
  try {
    return (await respuesta.json()) as Respuesta<R>;
  } catch {
    throw new ErrorDeRed("RESPUESTA_ILEGIBLE", "La API respondió algo que no es JSON.", camino);
  }
}

/** Si vale la pena reintentar: una falla de red o un 5xx, una vez. Un 4xx no cambia al repetirlo. */
export function reintentable(conteo: number, error: unknown): boolean {
  if (conteo >= 1) return false;
  if (error instanceof ErrorDeRed) return error.causa !== "RESPUESTA_ILEGIBLE";
  return error instanceof ErrorDeApi && error.estado >= 500;
}
