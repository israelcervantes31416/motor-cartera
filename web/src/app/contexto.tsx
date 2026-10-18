/**
 * El contexto de la pantalla, leido de la URL y validado por la API, y el guardia de cada pagina
 * que lo necesita: sin contexto, con uno que no encadena o sin la ejecucion que la pagina lee, la
 * pagina no se combina ni busca otra ejecucion; dice por que y ofrece abrir el selector.
 */
import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link, useOutletContext } from "react-router";

import type { ContextoValidado } from "../api/tipos";
import { Cargando, ErrorDeCarga } from "../components/Carga";
import { InsigniaEstado } from "../components/Estado";
import {
  NOMBRE_DE_EJECUCION,
  rutaConContexto,
  TIPO_DE_EJECUCION,
  type ClaveDeContexto,
  type ClaveDeEjecucion,
  type Contexto,
} from "../lib/contexto";

export interface ContextoDeLaPantalla {
  readonly contexto: Contexto | null;
  readonly invalidos: readonly ClaveDeContexto[];
  readonly validado: UseQueryResult<ContextoValidado>;
}

export function useContextoDeLaPantalla(): ContextoDeLaPantalla {
  return useOutletContext<ContextoDeLaPantalla>();
}

export interface ContextoListo {
  readonly contexto: Contexto;
  readonly validado: ContextoValidado;
}

/** La ejecucion declarada de un tipo, como la resolvio la API. */
export function ejecucionDe(validado: ContextoValidado, clave: ClaveDeEjecucion) {
  return validado.ejecuciones.find((e) => e.tipo === TIPO_DE_EJECUCION[clave]);
}

export function ProblemasDeContexto({ validado }: { readonly validado: ContextoValidado }) {
  return (
    <section className="alerta alerta--error" aria-labelledby="problemas-contexto">
      <h2 id="problemas-contexto" className="alerta__titulo">
        <span aria-hidden="true">✕ </span>El contexto no encadena: no se combina
      </h2>
      <p>
        Estas ejecuciones no son compatibles entre sí. La app no elige otra ni corrige ninguna:
        cambia el contexto en el selector.
      </p>
      <ul className="problemas">
        {validado.problemas.map((p, i) => (
          <li key={i}>
            <InsigniaEstado estado={p.codigo} /> {p.mensaje}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ConContexto({
  requiere = [],
  titulo,
  children,
}: {
  readonly requiere?: readonly ClaveDeEjecucion[];
  readonly titulo: string;
  readonly children: (listo: ContextoListo) => ReactNode;
}) {
  const { contexto, invalidos, validado } = useContextoDeLaPantalla();
  if (contexto === null) {
    return (
      <section className="tarjeta" aria-labelledby="sin-contexto">
        <h1 id="sin-contexto">{titulo}</h1>
        <p>
          Esta pantalla lee un contexto operativo: un corte y las ejecuciones que declara. Nada se
          elige como “lo más reciente”.
        </p>
        {invalidos.length > 0 ? (
          <p className="alerta alerta--aviso">
            La URL trae identificadores sin forma de UUID, que no se usan: {invalidos.join(", ")}.
          </p>
        ) : null}
        <Link className="boton boton--primario" to="/">
          Elegir un contexto
        </Link>
      </section>
    );
  }
  if (validado.isPending) return <Cargando que="el contexto" />;
  if (validado.isError) {
    return (
      <ErrorDeCarga
        error={validado.error}
        reintentar={() => {
          void validado.refetch();
        }}
      />
    );
  }
  const datos = validado.data;
  if (!datos.compatible) {
    return (
      <>
        <h1>{titulo}</h1>
        <ProblemasDeContexto validado={datos} />
        <Link className="boton" to={rutaConContexto("/", contexto)}>
          Cambiar el contexto
        </Link>
      </>
    );
  }
  const faltan = requiere.filter((clave) => contexto[clave] === undefined);
  if (faltan.length > 0) {
    return (
      <section className="tarjeta" aria-labelledby="falta-ejecucion">
        <h1 id="falta-ejecucion">{titulo}</h1>
        <p>
          El contexto no declara {faltan.map((c) => NOMBRE_DE_EJECUCION[c]).join(" ni ")}. Sin
          {faltan.length > 1 ? " ellas" : " ella"}, esta pantalla no busca otra ejecución.
        </p>
        <Link className="boton boton--primario" to={rutaConContexto("/", contexto)}>
          Declararla en el selector
        </Link>
      </section>
    );
  }
  return <>{children({ contexto, validado: datos })}</>;
}
