/**
 * Las rutas de la app. El selector de contexto va en el paquete inicial; cada seccion se carga
 * cuando se abre (su propio paquete), para que el inicio pese poco.
 */
import { createBrowserRouter, type RouteObject } from "react-router";

import { Cargando } from "../components/Carga";
import { SelectorDeContexto } from "../features/contexto/SelectorDeContexto";
import { ErrorDeRuta, NoEncontrada } from "./Errores";
import { Shell } from "./Shell";

export const rutas: RouteObject[] = [
  {
    path: "/",
    element: <Shell />,
    errorElement: <ErrorDeRuta />,
    // Lo que se ve mientras llega el paquete de una pagina diferida abierta por URL directa.
    hydrateFallbackElement: <Cargando que="la app" />,
    children: [
      { index: true, element: <SelectorDeContexto /> },
      {
        path: "resumen",
        lazy: async () => ({
          Component: (await import("../features/resumen/PaginaResumen")).PaginaResumen,
        }),
      },
      {
        path: "cuentas",
        lazy: async () => ({
          Component: (await import("../features/cuentas/PaginaCuentas")).PaginaCuentas,
        }),
      },
      {
        path: "cuentas/:cuentaId/:pestana?",
        lazy: async () => ({
          Component: (await import("../features/cuentas/PaginaCuenta")).PaginaCuenta,
        }),
      },
      {
        path: "analytics",
        lazy: async () => ({
          Component: (await import("../features/analytics/PaginaAnalytics")).PaginaAnalytics,
        }),
      },
      {
        path: "predictivo/:vista?",
        lazy: async () => ({
          Component: (await import("../features/predictivo/PaginaPredictivo")).PaginaPredictivo,
        }),
      },
      {
        path: "optimizacion/:vista?",
        lazy: async () => ({
          Component: (await import("../features/optimizacion/PaginaOptimizacion"))
            .PaginaOptimizacion,
        }),
      },
      {
        path: "campo",
        lazy: async () => ({
          Component: (await import("../features/campo/PaginaCampo")).PaginaCampo,
        }),
      },
      {
        path: "ruteo",
        lazy: async () => ({
          Component: (await import("../features/ruteo/PaginaRuteo")).PaginaRuteo,
        }),
      },
      {
        path: "ruteo/rutas/:rutaId",
        lazy: async () => ({
          Component: (await import("../features/ruteo/PaginaRuta")).PaginaRuta,
        }),
      },
      {
        path: "auditoria",
        lazy: async () => ({
          Component: (await import("../features/auditoria/PaginaAuditoria")).PaginaAuditoria,
        }),
      },
      { path: "*", element: <NoEncontrada /> },
    ],
  },
];

export function crearRouter() {
  return createBrowserRouter(rutas);
}
