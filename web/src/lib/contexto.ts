/**
 * El contexto operativo de la app: el corte y las ejecuciones que una pantalla lee, cada una por su
 * identificador publico, siempre en la URL. Copiar la URL reproduce la misma vista logica: el
 * contexto no vive en el estado de React.
 *
 * Nada se completa: lo que la URL no declara no se lee. Un identificador que no tiene forma de UUID
 * no se usa ni se corrige: se reporta.
 */

/** Cada parametro de la URL y el nombre con que lo pide la API. */
export const PARAMETROS = {
  corte: "corte_id",
  decision: "decision_run_id",
  analytics: "analytics_run_id",
  scoring: "scoring_run_id",
  optimizacion: "optimizacion_run_id",
  campo: "campo_run_id",
  ruteo: "ruteo_run_id",
} as const;

export type ClaveDeContexto = keyof typeof PARAMETROS;
export const CLAVES: readonly ClaveDeContexto[] = [
  "corte",
  "decision",
  "analytics",
  "scoring",
  "optimizacion",
  "campo",
  "ruteo",
];
export type ClaveDeEjecucion = Exclude<ClaveDeContexto, "corte">;
export const EJECUCIONES: readonly ClaveDeEjecucion[] = [
  "decision",
  "analytics",
  "scoring",
  "optimizacion",
  "campo",
  "ruteo",
];

/** El tipo con que la API nombra cada ejecucion del contexto. */
export const TIPO_DE_EJECUCION: Readonly<Record<ClaveDeEjecucion, string>> = {
  decision: "DECISION_V2",
  analytics: "ANALYTICS",
  scoring: "SCORING",
  optimizacion: "OPTIMIZACION",
  campo: "CAMPO_V2",
  ruteo: "RUTEO_V2",
};

export const NOMBRE_DE_EJECUCION: Readonly<Record<ClaveDeEjecucion, string>> = {
  decision: "Decisión (decision/v2)",
  analytics: "Analytics (analytics/v1)",
  scoring: "Scoring (predictive/model-v1)",
  optimizacion: "Optimización (optimization/v1)",
  campo: "Campo (campo/v2)",
  ruteo: "Ruteo (ruteo/v2)",
};

export type Contexto = { readonly corte: string } & {
  readonly [C in ClaveDeEjecucion]?: string;
};

const FORMA_DE_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function esUuid(valor: string): boolean {
  return FORMA_DE_UUID.test(valor);
}

export interface ContextoLeido {
  /** El contexto, si la URL trae un corte con forma de UUID. */
  readonly contexto: Contexto | null;
  /** Los parametros que la URL trae sin forma de UUID: no se usan. */
  readonly invalidos: readonly ClaveDeContexto[];
}

export function leerContexto(busqueda: URLSearchParams): ContextoLeido {
  const invalidos: ClaveDeContexto[] = [];
  const valores: Partial<Record<ClaveDeContexto, string>> = {};
  for (const clave of CLAVES) {
    const valor = busqueda.get(clave);
    if (valor === null || valor === "") continue;
    if (esUuid(valor)) valores[clave] = valor.toLowerCase();
    else invalidos.push(clave);
  }
  const corte = valores.corte;
  if (corte === undefined) return { contexto: null, invalidos };
  const contexto: { corte: string } & Partial<Record<ClaveDeEjecucion, string>> = { corte };
  for (const clave of EJECUCIONES) {
    const valor = valores[clave];
    if (valor !== undefined) contexto[clave] = valor;
  }
  return { contexto, invalidos };
}

/** El contexto como query string, en un orden fijo: la misma vista, la misma URL. */
export function contextoComoBusqueda(
  contexto: Contexto,
  extra: Readonly<Record<string, string | number | undefined | null>> = {},
): URLSearchParams {
  const busqueda = new URLSearchParams();
  for (const clave of CLAVES) {
    const valor = contexto[clave];
    if (valor !== undefined) busqueda.set(clave, valor);
  }
  for (const [llave, valor] of Object.entries(extra)) {
    if (valor !== undefined && valor !== null && valor !== "") busqueda.set(llave, String(valor));
  }
  return busqueda;
}

/** Una ruta de la app con el contexto y sus filtros en la query string. */
export function rutaConContexto(
  ruta: string,
  contexto: Contexto,
  extra: Readonly<Record<string, string | number | undefined | null>> = {},
): string {
  const texto = contextoComoBusqueda(contexto, extra).toString();
  return texto === "" ? ruta : `${ruta}?${texto}`;
}

/** El contexto como lo pide la API: corte_id y cada *_run_id declarado. */
export function contextoParaApi(contexto: Contexto): {
  corte_id: string;
  decision_run_id?: string;
  analytics_run_id?: string;
  scoring_run_id?: string;
  optimizacion_run_id?: string;
  campo_run_id?: string;
  ruteo_run_id?: string;
} {
  const api: Record<string, string> = { corte_id: contexto.corte };
  for (const clave of EJECUCIONES) {
    const valor = contexto[clave];
    if (valor !== undefined) api[PARAMETROS[clave]] = valor;
  }
  return api as ReturnType<typeof contextoParaApi>;
}

/** La parte de una llave de cache que fija el contexto: un lugar por ejecucion, vacio si no se
 * declara. Dos contextos distintos nunca comparten una entrada de cache. */
export function claveDeContexto(contexto: Contexto): readonly string[] {
  return CLAVES.map((clave) => `${clave}:${contexto[clave] ?? ""}`);
}

/** El mismo contexto con una ejecucion mas, o sin ella. */
export function conEjecucion(
  contexto: Contexto,
  clave: ClaveDeEjecucion,
  valor: string | undefined,
): Contexto {
  const copia: { corte: string } & Partial<Record<ClaveDeEjecucion, string>> = {
    corte: contexto.corte,
  };
  for (const otra of EJECUCIONES) {
    const v = otra === clave ? valor : contexto[otra];
    if (v !== undefined) copia[otra] = v;
  }
  return copia;
}
