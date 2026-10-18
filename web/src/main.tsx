import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/componentes.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";

import { crearClienteDeConsultas } from "./api/consultas";
import { LimiteDeErrores } from "./app/Errores";
import { crearRouter } from "./app/router";

const raiz = document.getElementById("raiz");
if (raiz === null) throw new Error("Falta el elemento #raiz en index.html.");

createRoot(raiz).render(
  <StrictMode>
    <LimiteDeErrores>
      <QueryClientProvider client={crearClienteDeConsultas()}>
        <RouterProvider router={crearRouter()} />
      </QueryClientProvider>
    </LimiteDeErrores>
  </StrictMode>,
);
