/**
 * Auditoria y linaje: de donde sale cada resultado del contexto (GET /app/v1/linaje). Cada nodo es
 * una ejecucion, una version o el corte, con su estado, su version, su politica, sus relojes y sus
 * hashes (abreviados a la vista, completos al copiar); cada flecha, una relacion que la ejecucion
 * que lee declaro al publicarse. Ninguna dependencia se infiere.
 */
import { useId, type ReactNode } from "react";

import { useLinaje } from "../../api/consultas";
import type { Linaje, NodoLinaje } from "../../api/tipos";
import { ProblemasDeContexto, useContextoDeLaPantalla } from "../../app/contexto";
import { Cargando, ErrorDeCarga, Resultado } from "../../components/Carga";
import { Copiable } from "../../components/Copiar";
import { InsigniaEstado } from "../../components/Estado";
import { Ficha } from "../../components/Kpi";
import type { Contexto } from "../../lib/contexto";
import { etiquetaDeCodigo, formatearFecha, formatearInstante } from "../../lib/formato";

/** El orden de lectura de la cadena: del corte a lo que se calcula sobre el. */
const ORDEN = [
  "CORTE",
  "DECISION_V2",
  "GEOGRAFIA",
  "TERRITORIAL_V2",
  "CONFIGURACION_CAMPO",
  "CAMPO_V2",
  "RED_VIAL",
  "RUTEO_V2",
  "MOTOR_PAGOS",
  "ATRIBUCION",
  "EVALUACION_PROMESAS",
  "ANALYTICS",
  "ENTRENAMIENTO",
  "MODELO",
  "SCORING",
  "CONFIGURACION_OPTIMIZACION",
  "OPTIMIZACION",
];

const NOMBRES: Readonly<Record<string, string>> = {
  CORTE: "Corte canónico",
  DECISION_V2: "Decisión (decision/v2)",
  GEOGRAFIA: "Geografía (geografia/v1)",
  TERRITORIAL_V2: "Territorial (territorial/v2)",
  CONFIGURACION_CAMPO: "Configuración de campo",
  CAMPO_V2: "Campo (campo/v2)",
  RED_VIAL: "Red vial",
  RUTEO_V2: "Ruteo (ruteo/v2)",
  MOTOR_PAGOS: "Interpretación de pagos (motor-pagos/v1)",
  ATRIBUCION: "Atribución (atribucion/v1)",
  EVALUACION_PROMESAS: "Evaluación de promesas",
  ANALYTICS: "Analytics (analytics/v1)",
  ENTRENAMIENTO: "Entrenamiento predictivo",
  MODELO: "Versión del modelo",
  SCORING: "Scoring",
  CONFIGURACION_OPTIMIZACION: "Configuración de optimización",
  OPTIMIZACION: "Optimización (optimization/v1)",
};

export function PaginaAuditoria() {
  const { contexto, validado } = useContextoDeLaPantalla();
  if (contexto === null) {
    return (
      <section className="tarjeta">
        <h1>Auditoría y linaje</h1>
        <p>El linaje es el de un contexto: elige uno en el selector.</p>
      </section>
    );
  }
  return (
    <>
      <h1>Auditoría y linaje</h1>
      <p className="entrada">
        ¿De dónde salió este resultado? Cada ejecución del contexto con sus versiones y sus hashes,
        y solo las relaciones que cada una declaró al publicarse.
      </p>
      {validado.isPending ? (
        <Cargando que="el contexto" />
      ) : validado.isError ? (
        <ErrorDeCarga error={validado.error} />
      ) : !validado.data.compatible ? (
        <ProblemasDeContexto validado={validado.data} />
      ) : null}
      <Cadena contexto={contexto} />
    </>
  );
}

function Cadena({ contexto }: { readonly contexto: Contexto }) {
  const linaje = useLinaje(contexto);
  return (
    <Resultado consulta={linaje} que="el linaje">
      {(datos) => <Grafo linaje={datos} />}
    </Resultado>
  );
}

function Grafo({ linaje }: { readonly linaje: Linaje }) {
  const nodos = [...linaje.nodos].sort(
    (a, b) => indice(a.tipo) - indice(b.tipo) || a.clave.localeCompare(b.clave),
  );
  const porClave = new Map(linaje.nodos.map((n) => [n.clave, n]));
  return (
    <>
      <ol className="linaje">
        {nodos.map((n) => {
          const lee = linaje.aristas.filter((a) => a.desde === n.clave);
          return (
            <li
              key={n.clave}
              className={n.declarado ? "linaje__nodo linaje__nodo--declarado" : "linaje__nodo"}
            >
              <Nodo nodo={n} />
              {lee.length > 0 ? (
                <ul
                  className="linaje__aristas"
                  aria-label={`Lo que lee ${NOMBRES[n.tipo] ?? n.tipo}`}
                >
                  {lee.map((a) => {
                    const destino = porClave.get(a.hacia);
                    return (
                      <li key={`${a.desde}-${a.hacia}`}>
                        <span aria-hidden="true">→ </span>
                        {a.relacion}:{" "}
                        <strong>
                          {destino ? (NOMBRES[destino.tipo] ?? destino.tipo) : a.hacia}
                        </strong>{" "}
                        {destino ? <code>{destino.run_id.slice(0, 8)}…</code> : null}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ol>
      <p className="nota">
        {linaje.nodos.length} nodos y {linaje.aristas.length} relaciones declaradas. Un nodo marcado
        “declarado” lo declara el contexto; los demás los leyeron sus ejecuciones.
      </p>
    </>
  );
}

function indice(tipo: string): number {
  const i = ORDEN.indexOf(tipo);
  return i < 0 ? ORDEN.length : i;
}

function Nodo({ nodo }: { readonly nodo: NodoLinaje }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <h2 id={id} className="linaje__titulo">
        {NOMBRES[nodo.tipo] ?? etiquetaDeCodigo(nodo.tipo)}{" "}
        {nodo.estado ? <InsigniaEstado estado={nodo.estado} /> : null}{" "}
        <span className="linaje__origen">
          {nodo.declarado ? "declarado en el contexto" : "leído por una declarada"}
        </span>
      </h2>
      <Ficha
        etiqueta={`Metadatos de ${nodo.tipo}`}
        filas={[
          [
            "Identificador",
            <Copiable key="id" valor={nodo.run_id} etiqueta={`el identificador de ${nodo.tipo}`} />,
          ],
          nodo.version ? ["Versión", nodo.version] : null,
          nodo.version_politica ? ["Política", nodo.version_politica] : null,
          nodo.resultado ? ["Resultado", etiquetaDeCodigo(nodo.resultado)] : null,
          nodo.as_of
            ? [
                nodo.tipo === "CORTE"
                  ? "Fecha de corte"
                  : nodo.tipo === "MOTOR_PAGOS" || nodo.tipo === "ATRIBUCION"
                    ? "Periodo desde"
                    : "as_of",
                formatearFecha(nodo.as_of),
              ]
            : null,
          nodo.known_at ? ["known_at", formatearInstante(nodo.known_at)] : null,
          nodo.fecha_operacion
            ? [
                nodo.tipo === "MOTOR_PAGOS" || nodo.tipo === "ATRIBUCION"
                  ? "Periodo hasta"
                  : "Fecha de operación",
                formatearFecha(nodo.fecha_operacion),
              ]
            : null,
          nodo.iniciada_en ? ["Iniciada", formatearInstante(nodo.iniciada_en)] : null,
          nodo.terminada_en ? ["Terminada", formatearInstante(nodo.terminada_en)] : null,
          ...Object.entries(nodo.hashes).map(([nombre, valor]): [string, ReactNode] => [
            etiquetaDeCodigo(nombre),
            <Copiable
              key={nombre}
              valor={valor}
              etiqueta={`${nombre} de ${nodo.tipo}`}
              largo={12}
            />,
          ]),
        ]}
      />
    </section>
  );
}
