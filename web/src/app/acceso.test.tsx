import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { AVISO_DEMO, AVISO_SESION } from "../components/Avisos";
import { rutaConContexto } from "../lib/contexto";
import { renderizar } from "../test/renderizar";
import { sinAcciones, sinViolacionesGraves } from "../test/revisar";
import { CONTEXTO, errorDeApi, servidor } from "../test/servidor";

import "../features/resumen/PaginaResumen";

function sesion(datos: { tipo: "USUARIO" | "SERVICIO"; permisos: string[] }) {
  return http.get("/api/app/v1/sesion", () =>
    HttpResponse.json({ ...datos, actor: datos.tipo === "USUARIO" ? "usr:0f" : "svc:desarrollo" }),
  );
}

describe("la sesion", () => {
  it("sin sesion, explica como iniciarla y vuelve a la misma pantalla", async () => {
    servidor.use(
      http.get("/api/app/v1/sesion", () =>
        errorDeApi(401, "SESION_REQUERIDA", "No hay una sesion valida."),
      ),
    );
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    expect(await screen.findByRole("heading", { name: "Inicia sesión" })).toBeInTheDocument();
    const enlace = screen.getByRole("link", { name: "Iniciar sesión" });
    const destino = new URL(enlace.getAttribute("href") ?? "", "http://localhost");
    expect(destino.pathname).toBe("/oauth2/start");
    expect(destino.searchParams.get("rd")).toBe(rutaConContexto("/resumen", CONTEXTO));
    // Sin sesion no hay navegacion ni datos: solo la marca y como entrar.
    expect(screen.queryByRole("navigation", { name: "Secciones" })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "contenido");
    sinAcciones();
    await sinViolacionesGraves();
  });

  it("con sesion pero sin mc.read, dice por que y ofrece cerrar sesion", async () => {
    servidor.use(
      http.get("/api/app/v1/sesion", () =>
        errorDeApi(403, "PERMISO_INSUFICIENTE", "La ruta exige el permiso mc.read."),
      ),
    );
    renderizar("/");

    expect(
      await screen.findByRole("heading", { name: "Sin permiso de lectura" }),
    ).toBeInTheDocument();
    const salir = screen.getByRole("button", { name: "Cerrar sesión" });
    const formulario = salir.closest("form");
    expect(formulario).toHaveAttribute("method", "post");
    expect(formulario).toHaveAttribute("action", "/oauth2/sign_out");
    await sinViolacionesGraves();
  });

  it("con sesion de lectura y auditoria, muestra sus permisos, la auditoria y como salir", async () => {
    renderizar("/");

    // Mientras llega la sesion, el marco de acceso tiene su propia cabecera: se espera la de la app.
    const permisos = await screen.findByText(/Sesión iniciada · auditoría, lectura/);
    const cabecera = screen.getByRole("banner");
    expect(cabecera).toContainElement(permisos);
    expect(within(cabecera).getByRole("button", { name: "Cerrar sesión" })).toBeInTheDocument();
    const navegacion = screen.getByRole("navigation", { name: "Secciones" });
    expect(within(navegacion).getByRole("link", { name: "Auditoría" })).toBeInTheDocument();
    expect(screen.getByText(AVISO_SESION)).toBeInTheDocument();
  });

  it("sin mc.audit, la auditoria no se ofrece", async () => {
    servidor.use(sesion({ tipo: "USUARIO", permisos: ["mc.read"] }));
    renderizar("/");

    const navegacion = await screen.findByRole("navigation", { name: "Secciones" });
    expect(within(navegacion).getByRole("link", { name: "Resumen" })).toBeInTheDocument();
    expect(within(navegacion).queryByRole("link", { name: "Auditoría" })).not.toBeInTheDocument();
  });

  it("en desarrollo (la clave del proxy), no hay sesion que cerrar y lo dice", async () => {
    servidor.use(sesion({ tipo: "SERVICIO", permisos: ["mc.audit", "mc.read"] }));
    renderizar("/");

    expect(await screen.findByText("Desarrollo local · solo lectura")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cerrar sesión" })).not.toBeInTheDocument();
    expect(screen.getByText(AVISO_DEMO)).toBeInTheDocument();
  });

  it("si una consulta responde 401 a mitad del camino, la sesion vencio", async () => {
    servidor.use(
      http.get("/api/app/v1/resumen", () =>
        errorDeApi(401, "SESION_REQUERIDA", "No hay una sesion valida."),
      ),
    );
    renderizar(rutaConContexto("/resumen", CONTEXTO));

    expect(await screen.findByRole("heading", { name: "Tu sesión expiró" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("link", { name: "Iniciar sesión" })).toHaveAttribute(
        "href",
        expect.stringContaining("/oauth2/start?rd=%2Fresumen"),
      );
    });
    await sinViolacionesGraves();
  });

  it("una pantalla que exige mc.audit sin ese permiso dice que la sesion no alcanza", async () => {
    servidor.use(
      sesion({ tipo: "USUARIO", permisos: ["mc.read"] }),
      http.get("/api/app/v1/linaje", () =>
        errorDeApi(403, "PERMISO_INSUFICIENTE", "La ruta exige el permiso mc.audit."),
      ),
    );
    renderizar(rutaConContexto("/auditoria", CONTEXTO));

    expect(await screen.findByText("Tu sesión no tiene permiso para ver esto")).toBeInTheDocument();
  });
});
