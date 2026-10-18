/**
 * El estado del servidor de la app: una consulta de TanStack Query por recurso, con su llave.
 *
 * Cada llave lleva todo lo que fija la respuesta: el contexto entero (un lugar por ejecucion, vacio
 * si no se declara), los identificadores, los filtros, la pagina y el orden. Dos contextos, dos
 * paginas o dos filtros distintos nunca comparten una entrada de cache. Lo publicado por una
 * ejecucion EXITOSA no cambia: se guarda mucho tiempo; un contexto con una ejecucion EN_PROCESO se
 * vuelve a validar cada 30 segundos, sin mas.
 */
import { keepPreviousData, QueryClient, useQuery } from "@tanstack/react-query";

import { claveDeContexto, contextoParaApi, type Contexto } from "../lib/contexto";
import { leer, reintentable, type ParametrosDeConsulta } from "./cliente";

export const LARGO = 10 * 60_000;
export const EN_PROCESO_MS = 30_000;

export function crearClienteDeConsultas(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: LARGO,
        gcTime: 30 * 60_000,
        retry: reintentable,
        retryDelay: 1_000,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
}

type Consulta<R extends Parameters<typeof leer>[0]> = ParametrosDeConsulta<R>;
export type FiltrosDeCuentas = Omit<
  Consulta<"/app/v1/cuentas">,
  keyof ReturnType<typeof contextoParaApi>
>;
export type FiltrosDeAgregados = Consulta<"/analytics/ejecuciones/{analytics_run_id}/agregados">;
export type FiltrosDePredicciones = Consulta<"/predictivo/scorings/{scoring_run_id}/predicciones">;
export type FiltrosDeResultados =
  Consulta<"/optimizacion/ejecuciones/{optimizacion_run_id}/resultados">;
export type FiltrosDeRestricciones =
  Consulta<"/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones">;
export type Pagina = { pagina?: number; por_pagina?: number };

export const claves = {
  opciones: (consulta: Consulta<"/app/v1/contextos/opciones">) => ["app", "opciones", consulta],
  validar: (c: Contexto) => ["app", "validar", ...claveDeContexto(c)],
  resumen: (c: Contexto) => ["app", "resumen", ...claveDeContexto(c)],
  cuentas: (c: Contexto, filtros: FiltrosDeCuentas) => [
    "app",
    "cuentas",
    ...claveDeContexto(c),
    filtros,
  ],
  cuenta: (id: string, c: Contexto) => ["app", "cuenta", id, ...claveDeContexto(c)],
  decision: (id: string, decision: string) => ["app", "cuenta", id, "decision", decision],
  historia: (id: string, p: Pagina) => ["app", "cuenta", id, "historia", p],
  lineaDeTiempo: (id: string, p: Pagina) => ["app", "cuenta", id, "linea", p],
  pagos: (id: string, p: Pagina) => ["app", "cuenta", id, "pagos", p],
  gestiones: (id: string, p: Pagina) => ["app", "cuenta", id, "gestiones", p],
  paradas: (ruta: string, p: Pagina) => ["app", "paradas", ruta, p],
  linaje: (c: Contexto) => ["app", "linaje", ...claveDeContexto(c)],
  distribucion: (scoring: string) => ["app", "distribucion", scoring],
  catalogo: () => ["analytics", "catalogo"],
  analytics: (id: string) => ["analytics", id],
  agregados: (id: string, f: FiltrosDeAgregados) => ["analytics", id, "agregados", f],
  resumenAnalytics: (id: string) => ["analytics", id, "resumen"],
  embudos: (id: string) => ["analytics", id, "embudos"],
  controles: (id: string) => ["analytics", id, "controles"],
  modelo: (id: string) => ["predictivo", "modelo", id],
  scoring: (id: string) => ["predictivo", "scoring", id],
  drift: (id: string) => ["predictivo", "scoring", id, "drift"],
  predicciones: (id: string, f: FiltrosDePredicciones) => ["predictivo", "scoring", id, "p", f],
  explicacionPredictiva: (scoring: string, cuenta: string) => [
    "predictivo",
    "scoring",
    scoring,
    "explicacion",
    cuenta,
  ],
  optimizacion: (id: string) => ["optimizacion", id],
  capacidad: (id: string, p: Pagina) => ["optimizacion", id, "capacidad", p],
  restricciones: (id: string, f: FiltrosDeRestricciones) => ["optimizacion", id, "restr", f],
  resultados: (id: string, f: FiltrosDeResultados) => ["optimizacion", id, "resultados", f],
  diagnostico: (id: string) => ["optimizacion", id, "diagnostico"],
  configuracion: (id: string) => ["optimizacion", "configuracion", id],
  explicacionOptimizacion: (id: string, cuenta: string) => ["optimizacion", id, "cuenta", cuenta],
  planCampo: (id: string) => ["campo", id],
  cargas: (id: string, p: Pagina) => ["campo", id, "cargas", p],
  ruteo: (id: string) => ["ruteo", id],
  rutas: (id: string, p: Pagina) => ["ruteo", id, "rutas", p],
  ruta: (id: string) => ["ruteo", "ruta", id],
} as const;

const NADA = "";

// --- el contexto y app/v1 -------------------------------------------------------------------------

export function useOpciones(consulta: Consulta<"/app/v1/contextos/opciones">) {
  return useQuery({
    queryKey: claves.opciones(consulta),
    queryFn: ({ signal }) => leer("/app/v1/contextos/opciones", { consulta, signal }),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useContextoValidado(contexto: Contexto | null) {
  return useQuery({
    queryKey: contexto === null ? ["app", "validar", NADA] : claves.validar(contexto),
    queryFn: ({ signal }) =>
      leer("/app/v1/contextos/validar", {
        consulta: contextoParaApi(contexto ?? { corte: NADA }),
        signal,
      }),
    enabled: contexto !== null,
    refetchInterval: (query) =>
      query.state.data?.ejecuciones.some((e) => e.estado === "EN_PROCESO") ? EN_PROCESO_MS : false,
  });
}

export function useResumen(contexto: Contexto, habilitada: boolean) {
  return useQuery({
    queryKey: claves.resumen(contexto),
    queryFn: ({ signal }) =>
      leer("/app/v1/resumen", { consulta: contextoParaApi(contexto), signal }),
    enabled: habilitada,
  });
}

export function useCuentas(contexto: Contexto, filtros: FiltrosDeCuentas, habilitada: boolean) {
  return useQuery({
    queryKey: claves.cuentas(contexto, filtros),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas", { consulta: { ...filtros, ...contextoParaApi(contexto) }, signal }),
    enabled: habilitada,
    placeholderData: keepPreviousData,
  });
}

export function useCuenta(id: string, contexto: Contexto, habilitada: boolean) {
  return useQuery({
    queryKey: claves.cuenta(id, contexto),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas/{cuenta_id}", {
        ruta: { cuenta_id: id },
        consulta: contextoParaApi(contexto),
        signal,
      }),
    enabled: habilitada,
  });
}

export function useDecisionDeCuenta(id: string, decision: string | undefined) {
  return useQuery({
    queryKey: claves.decision(id, decision ?? NADA),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas/{cuenta_id}/decision", {
        ruta: { cuenta_id: id },
        consulta: { decision_run_id: decision ?? NADA },
        signal,
      }),
    enabled: decision !== undefined,
  });
}

export function useHistoriaDeCuenta(id: string, pagina: Pagina) {
  return useQuery({
    queryKey: claves.historia(id, pagina),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas/{cuenta_id}/historia", {
        ruta: { cuenta_id: id },
        consulta: pagina,
        signal,
      }),
    placeholderData: keepPreviousData,
  });
}

export function useLineaDeTiempo(id: string, pagina: Pagina) {
  return useQuery({
    queryKey: claves.lineaDeTiempo(id, pagina),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas/{cuenta_id}/linea-de-tiempo", {
        ruta: { cuenta_id: id },
        consulta: pagina,
        signal,
      }),
    placeholderData: keepPreviousData,
  });
}

export function usePagosDeCuenta(id: string, pagina: Pagina) {
  return useQuery({
    queryKey: claves.pagos(id, pagina),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas/{cuenta_id}/pagos", {
        ruta: { cuenta_id: id },
        consulta: pagina,
        signal,
      }),
    placeholderData: keepPreviousData,
  });
}

export function useGestionesDeCuenta(id: string, pagina: Pagina) {
  return useQuery({
    queryKey: claves.gestiones(id, pagina),
    queryFn: ({ signal }) =>
      leer("/app/v1/cuentas/{cuenta_id}/gestiones", {
        ruta: { cuenta_id: id },
        consulta: pagina,
        signal,
      }),
    placeholderData: keepPreviousData,
  });
}

export function useParadas(ruta: string, pagina: Pagina, activa = true) {
  return useQuery({
    queryKey: claves.paradas(ruta, pagina),
    queryFn: ({ signal }) =>
      leer("/app/v1/ruteo/rutas/{ruta_id}/paradas", {
        ruta: { ruta_id: ruta },
        consulta: pagina,
        signal,
      }),
    enabled: activa,
    placeholderData: keepPreviousData,
  });
}

export function useLinaje(contexto: Contexto) {
  return useQuery({
    queryKey: claves.linaje(contexto),
    queryFn: ({ signal }) =>
      leer("/app/v1/linaje", { consulta: contextoParaApi(contexto), signal }),
  });
}

export function useDistribucion(scoring: string | undefined) {
  return useQuery({
    queryKey: claves.distribucion(scoring ?? NADA),
    queryFn: ({ signal }) =>
      leer("/app/v1/predictivo/scorings/{scoring_run_id}/distribucion", {
        ruta: { scoring_run_id: scoring ?? NADA },
        signal,
      }),
    enabled: scoring !== undefined,
  });
}

// --- analytics/v1 ---------------------------------------------------------------------------------

export function useCatalogo() {
  return useQuery({
    queryKey: claves.catalogo(),
    queryFn: ({ signal }) => leer("/analytics/catalogo", { signal }),
  });
}

export function useEjecucionAnalytics(id: string | undefined) {
  return useQuery({
    queryKey: claves.analytics(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/analytics/ejecuciones/{analytics_run_id}", {
        ruta: { analytics_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

export function useAgregados(id: string | undefined, filtros: FiltrosDeAgregados, activa = true) {
  return useQuery({
    queryKey: claves.agregados(id ?? NADA, filtros),
    queryFn: ({ signal }) =>
      leer("/analytics/ejecuciones/{analytics_run_id}/agregados", {
        ruta: { analytics_run_id: id ?? NADA },
        consulta: filtros,
        signal,
      }),
    enabled: id !== undefined && activa,
    placeholderData: keepPreviousData,
  });
}

export function useEmbudos(id: string | undefined) {
  return useQuery({
    queryKey: claves.embudos(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/analytics/ejecuciones/{analytics_run_id}/embudos", {
        ruta: { analytics_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

export function useControles(id: string | undefined) {
  return useQuery({
    queryKey: claves.controles(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/analytics/ejecuciones/{analytics_run_id}/controles", {
        ruta: { analytics_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

// --- predictive/model-v1 --------------------------------------------------------------------------

export function useModelo(id: string | undefined) {
  return useQuery({
    queryKey: claves.modelo(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/predictivo/modelos/{modelo_version_id}", {
        ruta: { modelo_version_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

export function useScoring(id: string | undefined) {
  return useQuery({
    queryKey: claves.scoring(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/predictivo/scorings/{scoring_run_id}", {
        ruta: { scoring_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

export function useDrift(id: string | undefined) {
  return useQuery({
    queryKey: claves.drift(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/predictivo/scorings/{scoring_run_id}/drift", {
        ruta: { scoring_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

export function usePredicciones(id: string | undefined, filtros: FiltrosDePredicciones) {
  return useQuery({
    queryKey: claves.predicciones(id ?? NADA, filtros),
    queryFn: ({ signal }) =>
      leer("/predictivo/scorings/{scoring_run_id}/predicciones", {
        ruta: { scoring_run_id: id ?? NADA },
        consulta: filtros,
        signal,
      }),
    enabled: id !== undefined,
    placeholderData: keepPreviousData,
  });
}

export function useExplicacionPredictiva(scoring: string | undefined, cuenta: string) {
  return useQuery({
    queryKey: claves.explicacionPredictiva(scoring ?? NADA, cuenta),
    queryFn: ({ signal }) =>
      leer("/predictivo/scorings/{scoring_run_id}/cuentas/{cuenta_id}/explicacion", {
        ruta: { scoring_run_id: scoring ?? NADA, cuenta_id: cuenta },
        signal,
      }),
    enabled: scoring !== undefined,
  });
}

// --- optimization/v1 ------------------------------------------------------------------------------

export function useOptimizacion(id: string | undefined) {
  return useQuery({
    queryKey: claves.optimizacion(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/optimizacion/ejecuciones/{optimizacion_run_id}", {
        ruta: { optimizacion_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined,
  });
}

export function useCapacidad(id: string | undefined, pagina: Pagina, activa = true) {
  return useQuery({
    queryKey: claves.capacidad(id ?? NADA, pagina),
    queryFn: ({ signal }) =>
      leer("/optimizacion/ejecuciones/{optimizacion_run_id}/capacidad", {
        ruta: { optimizacion_run_id: id ?? NADA },
        consulta: pagina,
        signal,
      }),
    enabled: id !== undefined && activa,
    placeholderData: keepPreviousData,
  });
}

export function useRestricciones(
  id: string | undefined,
  filtros: FiltrosDeRestricciones,
  activa = true,
) {
  return useQuery({
    queryKey: claves.restricciones(id ?? NADA, filtros),
    queryFn: ({ signal }) =>
      leer("/optimizacion/ejecuciones/{optimizacion_run_id}/restricciones", {
        ruta: { optimizacion_run_id: id ?? NADA },
        consulta: filtros,
        signal,
      }),
    enabled: id !== undefined && activa,
    placeholderData: keepPreviousData,
  });
}

export function useResultadosOptimizacion(
  id: string | undefined,
  filtros: FiltrosDeResultados,
  activa = true,
) {
  return useQuery({
    queryKey: claves.resultados(id ?? NADA, filtros),
    queryFn: ({ signal }) =>
      leer("/optimizacion/ejecuciones/{optimizacion_run_id}/resultados", {
        ruta: { optimizacion_run_id: id ?? NADA },
        consulta: filtros,
        signal,
      }),
    enabled: id !== undefined && activa,
    placeholderData: keepPreviousData,
  });
}

export function useDiagnostico(id: string | undefined, activa = true) {
  return useQuery({
    queryKey: claves.diagnostico(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/optimizacion/ejecuciones/{optimizacion_run_id}/diagnostico", {
        ruta: { optimizacion_run_id: id ?? NADA },
        signal,
      }),
    enabled: id !== undefined && activa,
  });
}

export function useExplicacionOptimizacion(id: string | undefined, cuenta: string) {
  return useQuery({
    queryKey: claves.explicacionOptimizacion(id ?? NADA, cuenta),
    queryFn: ({ signal }) =>
      leer("/optimizacion/ejecuciones/{optimizacion_run_id}/cuentas/{cuenta_id}", {
        ruta: { optimizacion_run_id: id ?? NADA, cuenta_id: cuenta },
        signal,
      }),
    enabled: id !== undefined,
  });
}

// --- campo/v2 y ruteo/v2 --------------------------------------------------------------------------

export function usePlanCampo(id: string | undefined) {
  return useQuery({
    queryKey: claves.planCampo(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/campo-v2/planes/{campo_run_id}", { ruta: { campo_run_id: id ?? NADA }, signal }),
    enabled: id !== undefined,
  });
}

export function useCargas(id: string | undefined, pagina: Pagina) {
  return useQuery({
    queryKey: claves.cargas(id ?? NADA, pagina),
    queryFn: ({ signal }) =>
      leer("/campo-v2/planes/{campo_run_id}/cargas", {
        ruta: { campo_run_id: id ?? NADA },
        consulta: pagina,
        signal,
      }),
    enabled: id !== undefined,
    placeholderData: keepPreviousData,
  });
}

export function useRuteo(id: string | undefined) {
  return useQuery({
    queryKey: claves.ruteo(id ?? NADA),
    queryFn: ({ signal }) =>
      leer("/ruteo-v2/ejecuciones/{ruteo_run_id}", { ruta: { ruteo_run_id: id ?? NADA }, signal }),
    enabled: id !== undefined,
  });
}

export function useRutas(id: string | undefined, pagina: Pagina) {
  return useQuery({
    queryKey: claves.rutas(id ?? NADA, pagina),
    queryFn: ({ signal }) =>
      leer("/ruteo-v2/ejecuciones/{ruteo_run_id}/rutas", {
        ruta: { ruteo_run_id: id ?? NADA },
        consulta: pagina,
        signal,
      }),
    enabled: id !== undefined,
    placeholderData: keepPreviousData,
  });
}

export function useRuta(id: string, activa = true) {
  return useQuery({
    queryKey: claves.ruta(id),
    queryFn: ({ signal }) => leer("/ruteo-v2/rutas/{ruta_id}", { ruta: { ruta_id: id }, signal }),
    enabled: activa,
  });
}
