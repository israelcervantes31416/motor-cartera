/**
 * Leer con cuidado lo que el contrato declara como un objeto libre (`dict[str, Any]` en el backend:
 * una distribucion, un certificado, una huella). Nada se supone: si la forma no es la esperada, se
 * devuelve null y la pantalla dice que no hay dato, en lugar de romperse o de inventar un cero.
 */
import {
  formatearDecimal,
  formatearDistancia,
  formatearDuracion,
  formatearEntero,
  formatearMonto,
  formatearPorcentaje,
  type Numerico,
} from "./formato";

export type Registro = Readonly<Record<string, unknown>>;

export function comoRegistro(valor: unknown): Registro | null {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor)
    ? (valor as Registro)
    : null;
}

export function comoLista(valor: unknown): readonly unknown[] {
  return Array.isArray(valor) ? valor : [];
}

export function comoNumero(valor: unknown): number | null {
  if (typeof valor === "number" && Number.isFinite(valor)) return valor;
  if (typeof valor === "string" && valor.trim() !== "" && Number.isFinite(Number(valor))) {
    return Number(valor);
  }
  return null;
}

export function comoTexto(valor: unknown): string | null {
  return typeof valor === "string" ? valor : null;
}

/** Un valor de un objeto libre del backend como texto: el texto tal cual, un numero o un booleano
 * escritos, un objeto como JSON, y `siFalta` si no hay valor. */
export function textoDe(valor: unknown, siFalta = ""): string {
  if (valor === null || valor === undefined) return siFalta;
  if (typeof valor === "string") return valor;
  if (typeof valor === "number" || typeof valor === "boolean" || typeof valor === "bigint") {
    return String(valor);
  }
  return JSON.stringify(valor);
}

/** Un objeto de conteos ({"ALTA": 301, "BAJA": 208}) como filas, en el orden en que llego. */
export function conteosDe(valor: unknown): { clave: string; valor: number }[] {
  const registro = comoRegistro(valor);
  if (registro === null) return [];
  return Object.entries(registro).flatMap(([clave, v]) => {
    const n = comoNumero(v);
    return n === null ? [] : [{ clave, valor: n }];
  });
}

/** Un valor en la unidad que declara su metrica en el catalogo de analytics/v1. */
export function formatearSegunUnidad(unidad: string, valor: Numerico): string {
  switch (unidad) {
    case "MONEDA":
      return formatearMonto(valor);
    case "RAZON":
      return formatearPorcentaje(valor);
    case "DIAS":
      return `${formatearDecimal(valor)} días`;
    case "SEGUNDOS":
      return formatearDuracion(typeof valor === "number" ? valor : Number(valor));
    case "METROS":
      return formatearDistancia(valor);
    default:
      return formatearEntero(valor);
  }
}

export const NOMBRE_DE_UNIDAD: Readonly<Record<string, string>> = {
  MONEDA: "monto (moneda de la fuente)",
  RAZON: "razón",
  CUENTAS: "cuentas",
  PROMESAS: "promesas",
  MOVIMIENTOS: "movimientos",
  RUTAS: "rutas",
  PARADAS: "paradas",
  SEGUNDOS: "segundos",
  CUPOS: "cupos",
  DIAS: "días",
  GESTIONES: "gestiones",
  VISITAS: "visitas",
  METROS: "metros",
};
