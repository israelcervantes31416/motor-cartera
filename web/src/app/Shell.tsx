/**
 * El marco de la app: el enlace para saltar al contenido, la cabecera con el contexto, la
 * navegacion (que en movil se pliega) y el pie con lo que la app es y lo que no.
 */
import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router";

import { useContextoValidado } from "../api/consultas";
import { AVISO_DEMO, AVISO_SOLO_LECTURA } from "../components/Avisos";
import { leerContexto, rutaConContexto, type Contexto } from "../lib/contexto";
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
  { ruta: "/auditoria", etiqueta: "Auditoría" },
] as const;

function enlace(ruta: string, contexto: Contexto | null): string {
  return contexto === null ? ruta : rutaConContexto(ruta, contexto);
}

export function Shell() {
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
          <span className="cabecera__modo">Demo local · solo lectura</span>
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
            {SECCIONES.map((s) => {
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
            })}
          </ul>
        </nav>
        <main id="contenido" className="contenido" tabIndex={-1}>
          <Outlet context={pantalla} />
        </main>
      </div>
      <footer className="pie">
        <p>{AVISO_DEMO}</p>
        <p>{AVISO_SOLO_LECTURA}</p>
      </footer>
    </div>
  );
}
