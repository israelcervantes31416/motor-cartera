/**
 * Los tipos de la app, tomados del contrato que genera openapi-typescript: ninguno se define otra
 * vez a mano. Si la API cambia una respuesta, `npm run api:tipos` cambia estos tipos y el
 * compilador senala cada pantalla que la lee.
 */
import type { components } from "./esquema";

type E = components["schemas"];

export type ErrorRespuesta = E["ErrorRespuesta"];

export type ContextoValidado = E["ContextoValidadoRespuesta"];
export type EjecucionDelContexto = E["EjecucionDelContexto"];
export type ProblemaDeContexto = E["ProblemaDeContexto"];
export type CorteDelContexto = E["CorteDelContexto"];
export type Opciones = E["OpcionesRespuesta"];
export type SugerenciaDeContexto = E["SugerenciaDeContexto"];

export type ResumenApp = E["ResumenAppRespuesta"];
export type DistribucionPredicciones = E["DistribucionPrediccionesRespuesta"];
export type PaginaCuentas = E["PaginaCuentasApp"];
export type CuentaListada = E["CuentaListadaRespuesta"];
export type Cuenta360 = E["Cuenta360AppRespuesta"];
export type DecisionDeCuenta = E["DecisionV2DeCuentaRespuestaApp"];
export type PaginaHistoria = E["PaginaHistoriaApp"];
export type PaginaLineaDeTiempo = E["PaginaLifecycleApp"];
export type ElementoDeLinea = E["ElementoLifecycleRespuestaApp"];
export type PagosDeCuenta = E["PagosDeCuentaApp"];
export type GestionesDeCuenta = E["GestionesDeCuentaApp"];
export type PaginaParadas = E["PaginaParadasV2App"];
export type Linaje = E["LinajeRespuesta"];
export type NodoLinaje = E["NodoLinaje"];

export type CatalogoAnalytics = E["CatalogoAnalyticsRespuesta"];
export type MetricaAnalytics = E["MetricaAnalyticsRespuesta"];
export type EjecucionAnalytics = E["EjecucionAnalyticsRespuesta"];
export type PaginaAgregados = E["PaginaAgregadosAnalytics"];
export type AgregadoAnalytics = E["AgregadoAnalyticsRespuesta"];
export type ResumenAnalytics = E["ResumenAnalyticsRespuesta"];
export type IndicadorAnalytics = E["IndicadorAnalyticsRespuesta"];
export type EmbudosAnalytics = E["EmbudosAnalyticsRespuesta"];
export type EtapaDeEmbudo = E["EtapaEmbudoAnalytics"];
export type ControlesAnalytics = E["ControlesAnalyticsRespuesta"];

export type ModeloPredictivo = E["ModeloPredictivoRespuesta"];
export type ComponentePredictivo = E["ComponentePredictivoRespuesta"];
export type ScoringPredictivo = E["ScoringPredictivoRespuesta"];
export type DriftPredictivo = E["DriftPredictivoRespuesta"];
export type FilaDeDrift = E["DriftPredictivoFila"];
export type PaginaPredicciones = E["PaginaPrediccionesPredictivas"];
export type ExplicacionPredictiva = E["ExplicacionPredictivaRespuesta"];

export type EjecucionOptimizacion = E["EjecucionOptimizacionRespuesta"];
export type PaginaCapacidad = E["PaginaCapacidadOptimizacion"];
export type PaginaRestricciones = E["PaginaRestriccionesOptimizacion"];
export type PaginaResultadosOptimizacion = E["PaginaResultadosOptimizacion"];
export type ExplicacionOptimizacion = E["ExplicacionCuentaRespuesta"];
export type DiagnosticoOptimizacion = E["DiagnosticoRespuesta"];

export type PlanCampo = E["PlanCampoV2Respuesta"];
export type PaginaCargas = E["PaginaCargasCampo"];
export type EjecucionRuteo = E["EjecucionRuteoV2Respuesta"];
export type PaginaRutas = E["PaginaRutasV2"];
export type RutaDeRuteo = E["RutaRespuesta"];
