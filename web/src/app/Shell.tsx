/**
 * El marco de la app: el enlace para saltar al contenido, la cabecera con el contexto y la sesion,
 * la navegacion (que en movil se pliega) y el pie con lo que la app es y lo que no. Todo detras de
 * <Acceso>: sin una sesion que pueda leer, la app solo muestra como conseguirla.
 */
import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router";

import { useContextoValidado } from "../api/consultas";
import { AVISO_DEMO, AVISO_SESION, AVISO_SOLO_LECTURA } from "../components/Avisos";
import { leerContexto, rutaConContexto, type Contexto } from "../lib/contexto";
import { Acceso, FormularioDeSalida, tienePermiso, useSesionActiva, type Sesion } from "./acceso";
import { BarraDeContexto } from "./BarraDeContexto";
import type { ContextoDeLaPantalla } from "./contexto";

export const SECCIONES = [
  { ruta: "/resumen", etiqueta: "Resumen" },
  { ruta: "/cuentas", etiqueta: "Cuentas" },
  { ruta: "/analytics", etiqueta: "Analytics" },
  { ruta: "/predictivo/modelo", etiqueta: "Predictivo", activa: "/predictivo" },
  { ruta: "/optimizacion/resumen", etiqueta: "Optimización", activa: "/optimizacion" },
  { ruta: "/campo", etiqueta: "Campo" },
  { ruta: "/ruteo", etiqueta: "Ruteo" },
  { ruta: "/auditoria", etiqueta: "Auditoría", permiso: "mc.audit" },
] as const;

const NOMBRES_DE_PERMISOS: Readonly<Record<string, string>> = {
  "mc.read": "lectura",
  "mc.audit": "auditoría",
  "mc.execute": "ejecución",
  "mc.configure": "configuración",
};

function permisosEnPalabras(sesion: Sesion): string {
  return sesion.permisos.map((p) => NOMBRES_DE_PERMISOS[p] ?? p).join(", ");
}

function enlace(ruta: string, contexto: Contexto | null): string {
  return contexto === null ? ruta : rutaConContexto(ruta, contexto);
}

export function Shell() {
  return (
    <Acceso>
      <ShellConSesion />
    </Acceso>
  );
}

function ShellConSesion() {
  const sesion = useSesionActiva();
  const deUsuario = sesion?.tipo === "USUARIO";
  const [busqueda] = useSearchParams();
  const { contexto, invalidos } = leerContexto(busqueda);
  const validado = useContextoValidado(contexto);
  const ubicacion = useLocation();
  // El menu movil se abre en una ruta: al navegar a otra queda cerrado, sin un efecto.
  const [abiertoEn, setAbiertoEn] = useState<string | null>(null);
  const abierto = abiertoEn === ubicacion.pathname;

  const pantalla: ContextoDeLaPantalla = { contexto, invalidos, validado };
  return (
    <div className="app">
      <a className="saltar" href="#contenido">
        Saltar al contenido
      </a>
      <header className="cabecera">
        <div className="cabecera__fila">
          <Link className="cabecera__marca" to={enlace("/", contexto)}>
            <img src="/favicon.svg" alt="" width={24} height={24} />
            Motor Cartera
          </Link>
          <span className="cabecera__modo">
            {deUsuario ? "Solo lectura" : "Desarrollo local · solo lectura"}
          </span>
          {sesion?.tipo === "USUARIO" ? (
            <div className="cabecera__sesion">
              <span>Sesión iniciada · {permisosEnPalabras(sesion)}</span>
              <FormularioDeSalida clase="boton boton--discreto" />
            </div>
          ) : null}
          <button
            type="button"
            className="boton cabecera__menu"
            aria-expanded={abierto}
            aria-controls="navegacion"
            onClick={() => {
              setAbiertoEn(abierto ? null : ubicacion.pathname);
            }}
          >
            {abierto ? "Cerrar menú" : "Menú"}
          </button>
        </div>
        {contexto !== null ? <BarraDeContexto contexto={contexto} validado={validado} /> : null}
      </header>
      <div className="app__cuerpo">
        <nav
          id="navegacion"
          className={abierto ? "navegacion navegacion--abierta" : "navegacion"}
          aria-label="Secciones"
        >
          <ul>
            <li>
              <NavLink
                to={enlace("/", contexto)}
                end
                className={({ isActive }) =>
                  isActive ? "navegacion__enlace activo" : "navegacion__enlace"
                }
              >
                Contexto
              </NavLink>
            </li>
            {SECCIONES.filter((s) => !("permiso" in s) || tienePermiso(sesion, s.permiso)).map(
              (s) => {
                const activa = "activa" in s ? s.activa : s.ruta;
                const actual =
                  ubicacion.pathname === activa || ubicacion.pathname.startsWith(`${activa}/`);
                return (
                  <li key={s.ruta}>
                    <Link
                      to={enlace(s.ruta, contexto)}
                      className={actual ? "navegacion__enlace activo" : "navegacion__enlace"}
                      aria-current={actual ? "page" : undefined}
                    >
                      {s.etiqueta}
                    </Link>
                  </li>
                );
              },
            )}
          </ul>
        </nav>
        <main id="contenido" className="contenido" tabIndex={-1}>
          <Outlet context={pantalla} />
        </main>
      </div>
      <footer className="pie">
        <p>{deUsuario ? AVISO_SESION : AVISO_DEMO}</p>
        <p>{AVISO_SOLO_LECTURA}</p>
      </footer>
    </div>
  );
}
