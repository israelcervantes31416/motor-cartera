/**
 * Como se muestra cada valor: montos, porcentajes, probabilidades, conteos, duraciones, fechas,
 * instantes e identificadores, con Intl. Formatear no es calcular: ninguna funcion de aqui cambia
 * un valor, solo su forma.
 *
 * - Un monto se muestra en la moneda de la fuente, sin simbolo: el dominio no declara moneda.
 * - Un decimal del backend llega como texto exacto ("1234.50") y se formatea como texto, sin
 *   pasar por un numero de punto flotante.
 * - Una fecha de negocio (AAAA-MM-DD) no tiene zona: no se convierte a la del navegador.
 * - Un instante se muestra en la zona que se pida (la de la ejecucion) o en UTC, siempre con su
 *   zona escrita.
 * - null no es cero: el que llama decide como mostrarlo (ver components/Estado.tsx).
 */

export const LOCALE = "es-MX";

export type Numerico = string | number;

function aNumerico(valor: Numerico): Intl.StringNumericLiteral | number {
  return typeof valor === "number" ? valor : (valor as Intl.StringNumericLiteral);
}

const monto = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const entero = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const porcentaje = new Intl.NumberFormat(LOCALE, {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const decimal = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 4 });

export function formatearMonto(valor: Numerico): string {
  return monto.format(aNumerico(valor));
}

const montosPrecisos = new Map<number, Intl.NumberFormat>();

function montoConDecimales(decimales: number): Intl.NumberFormat {
  const maximo = Math.min(Math.max(decimales, 2), 20);
  let formato = montosPrecisos.get(maximo);
  if (formato === undefined) {
    formato = new Intl.NumberFormat(LOCALE, {
      minimumFractionDigits: 2,
      maximumFractionDigits: maximo,
    });
    montosPrecisos.set(maximo, formato);
  }
  return formato;
}

function comoTextoDecimal(valor: Numerico): string {
  if (typeof valor === "string") return valor.trim();
  const texto = String(valor);
  return texto.includes("e") ? valor.toFixed(20).replace(/0+$/, "") : texto;
}

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Centavos del backend (optimization/v1: el objetivo, la cota, el valor de oportunidad) como monto:
 * la misma cantidad en unidades, corriendo el punto dos lugares en el texto, sin aritmetica de
 * punto flotante y sin redondear (los decimales que traiga se muestran).
 */
export function formatearCentavos(centavos: Numerico): string {
  const texto = comoTextoDecimal(centavos);
  const partes = DECIMAL.exec(texto);
  if (partes === null) return texto;
  const [, signo = "", enteros = "0", decimales = ""] = partes;
  const relleno = enteros.padStart(3, "0");
  const fraccion = `${relleno.slice(-2)}${decimales}`;
  return montoConDecimales(fraccion.length).format(
    aNumerico(`${signo}${relleno.slice(0, -2)}.${fraccion}`),
  );
}

export function formatearEntero(valor: Numerico): string {
  return entero.format(aNumerico(valor));
}

/** Una razon de 0 a 1 como porcentaje: 0.4523 es 45.2 %. */
export function formatearPorcentaje(razon: Numerico): string {
  return porcentaje.format(aNumerico(razon));
}

const porcentajePreciso = new Intl.NumberFormat(LOCALE, {
  style: "percent",
  maximumSignificantDigits: 3,
});

/** Una razon pequena (el gap de un solver) como porcentaje, sin que 0.0001 se vuelva "0.0 %". */
export function formatearPorcentajePreciso(razon: Numerico): string {
  return porcentajePreciso.format(aNumerico(razon));
}

export function formatearDecimal(valor: Numerico): string {
  return decimal.format(aNumerico(valor));
}

const segundosCortos = new Intl.NumberFormat(LOCALE, { maximumSignificantDigits: 2 });
const segundosConDecimales = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 });

/**
 * Segundos como duracion legible: 3725 es "1 h 2 min 5 s". Menos de diez segundos con fraccion (el
 * tiempo de un solver) conservan sus decimales: 0.031 es "0.031 s", no "0 s".
 */
export function formatearDuracion(segundos: number): string {
  if (!Number.isFinite(segundos)) return String(segundos);
  if (Math.abs(segundos) < 10 && !Number.isInteger(segundos)) {
    const formato = Math.abs(segundos) < 1 ? segundosCortos : segundosConDecimales;
    return `${formato.format(segundos)} s`;
  }
  const total = Math.round(Math.abs(segundos));
  const horas = Math.floor(total / 3600);
  const minutos = Math.floor((total % 3600) / 60);
  const resto = total % 60;
  const partes: string[] = [];
  if (horas > 0) partes.push(`${entero.format(horas)} h`);
  if (minutos > 0) partes.push(`${minutos} min`);
  if (resto > 0 || partes.length === 0) partes.push(`${resto} s`);
  return (segundos < 0 ? "-" : "") + partes.join(" ");
}

/** Metros como distancia: hasta 1 km en metros; despues, en kilometros con un decimal. */
export function formatearDistancia(metros: Numerico): string {
  const valor = typeof metros === "number" ? metros : Number(metros);
  if (Math.abs(valor) < 1000) return `${entero.format(valor)} m`;
  return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 }).format(valor / 1000)} km`;
}

const FECHA = /^(\d{4})-(\d{2})-(\d{2})$/;
const fecha = new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeZone: "UTC" });

/** Una fecha de negocio AAAA-MM-DD: el mismo dia, sin zona. */
export function formatearFecha(valor: string): string {
  const partes = FECHA.exec(valor);
  if (partes === null) return valor;
  const [, anio, mes, dia] = partes;
  return fecha.format(new Date(Date.UTC(Number(anio), Number(mes) - 1, Number(dia))));
}

// Intl no admite dateStyle/timeStyle junto con timeZoneName: los componentes van explicitos.
const INSTANTE: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZoneName: "short",
};
const instantes = new Map<string, Intl.DateTimeFormat>();

function formatoDeInstante(zona: string): Intl.DateTimeFormat {
  let formato = instantes.get(zona);
  if (formato === undefined) {
    try {
      formato = new Intl.DateTimeFormat(LOCALE, { ...INSTANTE, timeZone: zona });
    } catch (error) {
      // Una zona que este navegador no conoce: UTC, que siempre se escribe.
      if (!(error instanceof RangeError)) throw error;
      formato = new Intl.DateTimeFormat(LOCALE, { ...INSTANTE, timeZone: "UTC" });
    }
    instantes.set(zona, formato);
  }
  return formato;
}

/** Un instante con su zona: el de la ejecucion si se da, si no UTC. Siempre dice cual. */
export function formatearInstante(valor: string, zona = "UTC"): string {
  const instante = new Date(valor);
  if (Number.isNaN(instante.getTime())) return valor;
  return formatoDeInstante(zona).format(instante);
}

/** Un identificador largo, abreviado a la vista: el completo se copia (components/Copiar.tsx). */
export function abreviar(valor: string, largo = 8): string {
  return valor.length <= largo ? valor : `${valor.slice(0, largo)}…`;
}

// Los codigos del backend van sin acentos; su etiqueta en pantalla los lleva.
const ACENTOS: Readonly<Record<string, string>> = {
  valida: "válida",
  invalida: "inválida",
  telefonica: "telefónica",
  nomina: "nómina",
  geografia: "geografía",
  optimizacion: "optimización",
  decision: "decisión",
  prediccion: "predicción",
  imputacion: "imputación",
  asociacion: "asociación",
  unica: "única",
  gestion: "gestión",
  revision: "revisión",
  ejecucion: "ejecución",
  publicacion: "publicación",
  validacion: "validación",
  calibracion: "calibración",
  atribucion: "atribución",
};

/** Un codigo del backend (ASIGNADA, SIN_TIEMPO...) legible: "Sin tiempo". */
export function etiquetaDeCodigo(codigo: string): string {
  const texto = codigo
    .toLowerCase()
    .split("_")
    .filter((palabra) => palabra !== "")
    .map((palabra) => ACENTOS[palabra] ?? palabra)
    .join(" ");
  return texto.charAt(0).toUpperCase() + texto.slice(1);
}
