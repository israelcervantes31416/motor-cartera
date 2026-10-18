/**
 * Renderiza la app entera en una URL, con un router en memoria y un cliente de consultas nuevo por
 * prueba: ninguna prueba hereda la cache de otra.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { rutas } from "../app/router";

export function crearClienteDePrueba(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
}

export function renderizar(url: string) {
  const router = createMemoryRouter(rutas, { initialEntries: [url] });
  const cliente = crearClienteDePrueba();
  const resultado = render(
    <QueryClientProvider client={cliente}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...resultado, router, cliente };
}

/** La URL actual del router, como la veria el navegador. */
export function urlActual(router: ReturnType<typeof createMemoryRouter>): URL {
  const { pathname, search } = router.state.location;
  return new URL(`${pathname}${search}`, "http://localhost");
}
