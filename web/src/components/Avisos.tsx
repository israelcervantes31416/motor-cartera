/**
 * Los avisos que la app nunca esconde en la documentacion: lo que un numero no dice. Cada pantalla
 * que muestra una prediccion, un plan optimo, una atribucion o un drift lleva el suyo, visible.
 */
import type { ReactNode } from "react";

export const AVISO_PREDICCION = "Predicción, no efecto causal ni recomendación de acción.";
export const AVISO_OPTIMO =
  "Óptimo respecto al modelo declarado; no estrategia causalmente óptima.";
export const AVISO_ATRIBUCION = "Asociación operacional; no implica causalidad.";
export const AVISO_DRIFT = "Drift no implica automáticamente degradación del modelo.";
export const AVISO_ANALYTICS =
  "Analytics describe lo observado y lo planificado, agregado: no prueba causalidad, no predice y no recomienda.";
export const AVISO_DEMO =
  "Superficie local/demostrativa sin autenticación; no desplegar con datos reales.";
export const AVISO_SOLO_LECTURA =
  "Solo lectura: esta app no ejecuta gestiones, no envía, no llama, no visita, no optimiza ni reentrena nada.";
export const AVISO_PARADA = "Una parada programada es un plan, no una visita realizada.";

export function Aviso({
  children,
  tono = "info",
}: {
  readonly children: ReactNode;
  readonly tono?: "info" | "aviso";
}) {
  return (
    <p className={`aviso aviso--${tono}`}>
      <span className="aviso__icono" aria-hidden="true">
        {tono === "aviso" ? "⚠" : "ⓘ"}
      </span>
      <span>{children}</span>
    </p>
  );
}
