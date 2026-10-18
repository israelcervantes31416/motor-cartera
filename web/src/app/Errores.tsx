/**
 * Lo que la app muestra cuando algo falla fuera de una consulta: una ruta que no existe (404), un
 * error de una ruta (el errorElement del router) y un error de render (el limite de errores de
 * React). Nunca un traceback: un mensaje util, el camino en que paso y un boton para reintentar.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { isRouteErrorResponse, Link, useLocation, useRouteError } from "react-router";

import { mensajeDeError } from "../components/Carga";

export function NoEncontrada() {
  const { pathname } = useLocation();
  return (
    <section className="tarjeta" aria-labelledby="no-encontrada">
      <h1 id="no-encontrada">Página no encontrada</h1>
      <p>
        La app no tiene la ruta <code>{pathname}</code>.
      </p>
      <Link className="boton boton--primario" to="/">
        Ir al selector de contexto
      </Link>
    </section>
  );
}

function Falla({ titulo, detalle }: { readonly titulo: string; readonly detalle: string }) {
  const { pathname } = useLocation();
  return (
    <section className="tarjeta alerta alerta--error" role="alert" aria-labelledby="falla">
      <h1 id="falla">{titulo}</h1>
      <p>{detalle}</p>
      <p>
        En <code>{pathname}</code>.
      </p>
      <button
        type="button"
        className="boton"
        onClick={() => {
          window.location.reload();
        }}
      >
        Reintentar
      </button>{" "}
      <Link className="boton" to="/">
        Ir al selector de contexto
      </Link>
    </section>
  );
}

/** El errorElement de las rutas: un error al cargar una pagina diferida o al resolver la ruta. */
export function ErrorDeRuta() {
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NoEncontrada />;
  const { titulo, detalle } = mensajeDeError(error);
  return <Falla titulo={titulo} detalle={detalle} />;
}

interface Estado {
  readonly fallo: boolean;
}

/** El limite de errores de toda la app: un error de render no deja la pantalla en blanco. */
export class LimiteDeErrores extends Component<{ readonly children: ReactNode }, Estado> {
  override state: Estado = { fallo: false };

  static getDerivedStateFromError(): Estado {
    return { fallo: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // A la consola del navegador de quien lo ve, no a un servicio externo: no hay telemetria.
    console.error("Error de render", error, info.componentStack);
  }

  override render(): ReactNode {
    if (!this.state.fallo) return this.props.children;
    return (
      <main className="contenido" id="contenido">
        <section className="tarjeta alerta alerta--error" role="alert">
          <h1>La pantalla no se pudo mostrar</h1>
          <p>
            Ocurrió un error en la app. Ningún dato se modificó: esta superficie es de solo lectura.
          </p>
          <button
            type="button"
            className="boton"
            onClick={() => {
              window.location.reload();
            }}
          >
            Reintentar
          </button>
        </section>
      </main>
    );
  }
}
