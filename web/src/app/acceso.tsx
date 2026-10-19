/**
 * La sesion de quien ve la app. La app no maneja contrasenas ni tokens: la sesion vive del lado del
 * servidor (oauth2-proxy), en una cookie HttpOnly que el JavaScript de la pagina no puede leer. La
 * app solo pregunta quien es (GET /api/app/v1/sesion) y, segun la respuesta:
 *
 * - 200: muestra la app, con los permisos de la sesion (sin mc.audit no ofrece la auditoria);
 * - 401: no hay sesion o vencio: como iniciarla, volviendo a la misma URL;
 * - 403: hay sesion, pero sin mc.read: por que no puede leer, y como cerrar sesion.
 *
 * Si una consulta responde 401 a mitad de la navegacion, la sesion vencio: se avisa y se ofrece
 * volver a iniciarla. En desarrollo (sin usuarios) la sesion es la clave de servicio del proxy.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router";

import { ErrorDeApi, EVENTO_SIN_SESION, type Respuesta } from "../api/cliente";
import { useSesion } from "../api/consultas";
import { AVISO_SOLO_LECTURA } from "../components/Avisos";
import { Cargando, ErrorDeCarga } from "../components/Carga";

export type Sesion = Respuesta<"/app/v1/sesion">;

const ContextoDeSesion = createContext<Sesion | null>(null);

/** La sesion activa. Fuera de <Acceso>, null. */
export function useSesionActiva(): Sesion | null {
  return useContext(ContextoDeSesion);
}

export function tienePermiso(sesion: Sesion | null, permiso: string): boolean {
  return sesion?.permisos.includes(permiso) ?? false;
}

/** El destino al volver de iniciar sesion: la misma pagina, con su contexto. Siempre relativo. */
export function destinoDeRegreso(pathname: string, search: string): string {
  const ruta = pathname.startsWith("/") ? pathname : "/";
  return `${ruta}${search}`;
}

export function enlaceDeInicio(pathname: string, search: string): string {
  return `/oauth2/start?rd=${encodeURIComponent(destinoDeRegreso(pathname, search))}`;
}

/** Cerrar sesion: un POST del mismo origen (el edge revisa Origin y la cookie es SameSite=Lax). */
export function FormularioDeSalida({ clase = "boton" }: { readonly clase?: string }) {
  return (
    <form className="salida" method="post" action="/oauth2/sign_out">
      <button type="submit" className={clase}>
        Cerrar sesión
      </button>
    </form>
  );
}

export function PantallaDeAcceso({ expirada }: { readonly expirada: boolean }) {
  const { pathname, search } = useLocation();
  return (
    <section className="tarjeta acceso" aria-labelledby="acceso-titulo">
      <h1 id="acceso-titulo">{expirada ? "Tu sesión expiró" : "Inicia sesión"}</h1>
      <p>
        {expirada
          ? "La sesión venció o se cerró. Vuelve a iniciarla para seguir en esta misma pantalla."
          : "Motor Cartera necesita una sesión: entra con tu cuenta del proveedor de identidad de tu organización."}
      </p>
      <p>
        <a className="boton boton--primario" href={enlaceDeInicio(pathname, search)}>
          Iniciar sesión
        </a>
      </p>
      <p className="acceso__nota">
        La app no ve tu contraseña ni guarda ningún token: la sesión vive del lado del servidor, en
        una cookie que el navegador no deja leer a la página.
      </p>
    </section>
  );
}

export function PantallaSinPermiso() {
  return (
    <section className="tarjeta acceso alerta alerta--error" aria-labelledby="sin-permiso-titulo">
      <h1 id="sin-permiso-titulo">Sin permiso de lectura</h1>
      <p>
        Tu cuenta inició sesión, pero no tiene el permiso <code>mc.read</code> que exige esta
        superficie. Pide a quien administra el proveedor de identidad un rol de lectura.
      </p>
      <FormularioDeSalida />
    </section>
  );
}

/** El marco de lo que se ve sin una sesion que pueda leer: la marca y la pantalla, sin navegacion. */
function MarcoDeAcceso({ children }: { readonly children: ReactNode }) {
  return (
    <div className="app">
      <a className="saltar" href="#contenido">
        Saltar al contenido
      </a>
      <header className="cabecera">
        <div className="cabecera__fila">
          <span className="cabecera__marca">
            <img src="/favicon.svg" alt="" width={24} height={24} />
            Motor Cartera
          </span>
        </div>
      </header>
      <main id="contenido" className="contenido contenido--acceso" tabIndex={-1}>
        {children}
      </main>
      <footer className="pie">
        <p>{AVISO_SOLO_LECTURA}</p>
      </footer>
    </div>
  );
}

function esErrorDe(error: unknown, estado: number): boolean {
  return error instanceof ErrorDeApi && error.estado === estado;
}

/** Muestra a `children` solo con una sesion que puede leer; si no, lo que hace falta. */
export function Acceso({ children }: { readonly children: ReactNode }) {
  const sesion = useSesion();
  const [expirada, setExpirada] = useState(false);

  useEffect(() => {
    const alPerderla = () => {
      setExpirada(true);
    };
    window.addEventListener(EVENTO_SIN_SESION, alPerderla);
    return () => {
      window.removeEventListener(EVENTO_SIN_SESION, alPerderla);
    };
  }, []);

  let pantalla: ReactNode = null;
  if (sesion.isPending) pantalla = <Cargando que="la sesión" />;
  else if (sesion.isError && esErrorDe(sesion.error, 403)) pantalla = <PantallaSinPermiso />;
  else if (sesion.isError && esErrorDe(sesion.error, 401)) {
    pantalla = <PantallaDeAcceso expirada={false} />;
  } else if (sesion.isError) {
    pantalla = (
      <ErrorDeCarga
        error={sesion.error}
        reintentar={() => {
          void sesion.refetch();
        }}
      />
    );
  } else if (expirada) pantalla = <PantallaDeAcceso expirada />;
  if (pantalla !== null || sesion.data === undefined) {
    return <MarcoDeAcceso>{pantalla}</MarcoDeAcceso>;
  }
  return <ContextoDeSesion.Provider value={sesion.data}>{children}</ContextoDeSesion.Provider>;
}
