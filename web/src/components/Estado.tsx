/**
 * Como se muestra un estado, en un solo lugar. Un valor que no esta disponible, que se suprimio,
 * que no aplica o que esta censurado nunca se muestra como cero: se muestra su estado, con texto y
 * con un icono, no solo con un color.
 */
import type { ReactNode } from "react";

import { etiquetaDeCodigo } from "../lib/formato";

export type Tono = "ok" | "info" | "aviso" | "error" | "neutro";

interface Definicion {
  readonly etiqueta: string;
  readonly icono: string;
  readonly tono: Tono;
  readonly descripcion: string;
}

export const ESTADOS: Readonly<Record<string, Definicion>> = {
  DISPONIBLE: {
    etiqueta: "Disponible",
    icono: "✓",
    tono: "ok",
    descripcion: "Publicado por su ejecución.",
  },
  NO_DISPONIBLE: {
    etiqueta: "No disponible",
    icono: "⊘",
    tono: "neutro",
    descripcion: "Su fuente no se declaró o no tiene cobertura: no es cero.",
  },
  SUPRIMIDO: {
    etiqueta: "Suprimido",
    icono: "▨",
    tono: "neutro",
    descripcion:
      "La celda tiene menos cuentas que el mínimo de la política: su valor no se publica ni se reconstruye.",
  },
  NO_APLICA: {
    etiqueta: "No aplica",
    icono: "–",
    tono: "neutro",
    descripcion: "La métrica no aplica a esta celda.",
  },
  CENSURADO: {
    etiqueta: "Censurado",
    icono: "◔",
    tono: "aviso",
    descripcion: "Todavía no es observable: no es cero ni negativo.",
  },
  SIN_VALOR: {
    etiqueta: "Sin valor",
    icono: "∅",
    tono: "neutro",
    descripcion: "Publicado sin valor (por ejemplo, una razón con denominador cero): no es cero.",
  },
  EXITOSA: { etiqueta: "Exitosa", icono: "✓", tono: "ok", descripcion: "Terminó y publicó." },
  EN_PROCESO: {
    etiqueta: "En proceso",
    icono: "⟳",
    tono: "info",
    descripcion: "Un worker todavía no la termina.",
  },
  FALLIDA: {
    etiqueta: "Fallida",
    icono: "✕",
    tono: "error",
    descripcion: "Terminó sin publicar: su resultado dice por qué.",
  },
  NO_DECLARADA: {
    etiqueta: "No declarada",
    icono: "○",
    tono: "neutro",
    descripcion: "El contexto no declara esta ejecución: no se busca otra.",
  },
  NO_PUBLICADA: {
    etiqueta: "No publicada",
    icono: "⊘",
    tono: "aviso",
    descripcion: "La ejecución declarada no está EXITOSA: no publicó resultados.",
  },
  SIN_RESULTADO: {
    etiqueta: "Sin resultado",
    icono: "∅",
    tono: "neutro",
    descripcion: "La ejecución publicó, y esta cuenta no está en ella.",
  },
  COMPATIBLE: {
    etiqueta: "Compatible",
    icono: "✓",
    tono: "ok",
    descripcion: "Cada ejecución existe, es del corte y encadena con las demás.",
  },
  INCOMPATIBLE: {
    etiqueta: "Incompatible",
    icono: "✕",
    tono: "error",
    descripcion: "Las ejecuciones del contexto no encadenan: no se combinan.",
  },
  EJECUCION_NO_ENCONTRADA: {
    etiqueta: "No existe",
    icono: "✕",
    tono: "error",
    descripcion: "La ejecución declarada no existe.",
  },
  CORTE_DISTINTO: {
    etiqueta: "Otro corte",
    icono: "✕",
    tono: "error",
    descripcion: "La ejecución es de otro corte que el del contexto.",
  },
  ENCADENAMIENTO_DISTINTO: {
    etiqueta: "No encadena",
    icono: "✕",
    tono: "error",
    descripcion: "Dos ejecuciones leyeron cosas distintas de lo mismo.",
  },
  OK: { etiqueta: "OK", icono: "✓", tono: "ok", descripcion: "El control cuadró, exacto." },
  FALLO: { etiqueta: "Falló", icono: "✕", tono: "error", descripcion: "El control no cuadró." },
  PUBLICADA: { etiqueta: "Publicada", icono: "✓", tono: "ok", descripcion: "Versión publicada." },
  RECHAZADA: {
    etiqueta: "Rechazada",
    icono: "✕",
    tono: "error",
    descripcion: "No pasó sus compuertas.",
  },
  VALIDADO: { etiqueta: "Validado", icono: "✓", tono: "ok", descripcion: "Pasó sus compuertas." },
  RECHAZADO: {
    etiqueta: "Rechazado",
    icono: "✕",
    tono: "error",
    descripcion: "No pasó sus compuertas en TEST.",
  },
  OPTIMAL: {
    etiqueta: "Óptimo demostrado",
    icono: "✓",
    tono: "ok",
    descripcion: "El solver demostró el óptimo: cota igual al objetivo.",
  },
  TIME_LIMIT: {
    etiqueta: "Tiempo agotado",
    icono: "⏱",
    tono: "aviso",
    descripcion: "El solver agotó su tiempo sin demostrar el óptimo: no se publicó un plan.",
  },
  INFEASIBLE: {
    etiqueta: "Infactible",
    icono: "✕",
    tono: "error",
    descripcion: "No existe un plan que cumpla las restricciones declaradas.",
  },
  ASIGNADA: { etiqueta: "Asignada", icono: "●", tono: "ok", descripcion: "Quedó en el plan." },
  NO_SELECCIONADA: {
    etiqueta: "No seleccionada",
    icono: "○",
    tono: "neutro",
    descripcion: "Elegible, fuera de la solución óptima del modelo declarado.",
  },
  NO_ELEGIBLE: {
    etiqueta: "No elegible",
    icono: "⊘",
    tono: "neutro",
    descripcion: "No cumplía las condiciones para entrar al modelo.",
  },
  COMPLETA: {
    etiqueta: "Completa",
    icono: "✓",
    tono: "ok",
    descripcion: "Todas sus features se conocían.",
  },
  CON_IMPUTACION: {
    etiqueta: "Con imputación",
    icono: "◐",
    tono: "aviso",
    descripcion: "Alguna feature se imputó con el valor de TRAIN: lo dicen sus motivos.",
  },
  PROGRAMADA: {
    etiqueta: "Programada",
    icono: "●",
    tono: "ok",
    descripcion: "Parada planificada en la jornada: no es una visita realizada.",
  },
  SIN_TIEMPO: {
    etiqueta: "Sin tiempo",
    icono: "⏱",
    tono: "aviso",
    descripcion: "No cabe en la jornada del gestor.",
  },
  NO_ENRUTABLE: {
    etiqueta: "No enrutable",
    icono: "⊘",
    tono: "aviso",
    descripcion: "No tiene un camino en la red vial.",
  },
  NO_ENRUTABLE_BASE: {
    etiqueta: "Base no enrutable",
    icono: "⊘",
    tono: "aviso",
    descripcion: "La base del gestor no tiene un punto válido en la red.",
  },
  BAJO: {
    etiqueta: "Bajo",
    icono: "▁",
    tono: "ok",
    descripcion: "PSI bajo: heurística operacional.",
  },
  MODERADO: {
    etiqueta: "Moderado",
    icono: "▄",
    tono: "aviso",
    descripcion: "PSI moderado: heurística operacional, no una prueba de deterioro.",
  },
  ALTO: {
    etiqueta: "Alto",
    icono: "█",
    tono: "error",
    descripcion: "PSI alto: heurística operacional, no una prueba de deterioro.",
  },
  ACTIVA: {
    etiqueta: "Activa (binding)",
    icono: "■",
    tono: "aviso",
    descripcion: "Holgura cero: la restricción limita el plan.",
  },
  HOLGADA: {
    etiqueta: "Con holgura",
    icono: "□",
    tono: "neutro",
    descripcion: "La restricción no limita el plan.",
  },
};

export function definicionDe(estado: string): Definicion {
  return (
    ESTADOS[estado] ?? {
      etiqueta: etiquetaDeCodigo(estado),
      icono: "•",
      tono: "neutro",
      descripcion: estado,
    }
  );
}

export function InsigniaEstado({ estado }: { readonly estado: string }) {
  const d = definicionDe(estado);
  return (
    <span className={`insignia insignia--${d.tono}`} title={d.descripcion} data-estado={estado}>
      <span className="insignia__icono" aria-hidden="true">
        {d.icono}
      </span>
      {d.etiqueta}
    </span>
  );
}

/** Un valor con su estado: solo un DISPONIBLE con valor se formatea; todo lo demas es su estado. */
export function ValorConEstado<T>({
  estado,
  valor,
  formatear,
}: {
  readonly estado: string | null | undefined;
  readonly valor: T | null | undefined;
  readonly formatear: (valor: T) => ReactNode;
}) {
  const efectivo = estado ?? "NO_DISPONIBLE";
  if (efectivo !== "DISPONIBLE") return <InsigniaEstado estado={efectivo} />;
  if (valor === null || valor === undefined) return <InsigniaEstado estado="SIN_VALOR" />;
  return <span className="valor">{formatear(valor)}</span>;
}

/** Un valor que puede faltar sin estado propio: null es "Sin valor", nunca cero. */
export function ValorOpcional<T>({
  valor,
  formatear,
}: {
  readonly valor: T | null | undefined;
  readonly formatear: (valor: T) => ReactNode;
}) {
  if (valor === null || valor === undefined) return <InsigniaEstado estado="SIN_VALOR" />;
  return <span className="valor">{formatear(valor)}</span>;
}
