/**
 * Lo que el codigo de la app nunca dice ni carga, revisado en su texto: ninguna promesa causal,
 * ningun recurso externo (CDN, fuentes, telemetria) y ningun HTML crudo.
 */
import { describe, expect, it } from "vitest";

const FUENTES = import.meta.glob<string>(["../**/*.{ts,tsx,css}", "!../**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const INDEX = import.meta.glob<string>("/index.html", {
  query: "?raw",
  import: "default",
  eager: true,
});

// Generados: el contrato de la API y las capturas de las pruebas.
const GENERADOS = ["/api/esquema.ts", "/test/capturas.ts"];
const propias = Object.entries(FUENTES).filter(
  ([ruta]) => !GENERADOS.some((generado) => ruta.endsWith(generado)),
);

/** Lo que una pantalla no puede afirmar: que una accion o un canal causa un resultado. */
const CAUSALES = [
  /esta acci[oó]n causar[aá]/i,
  /este canal incrementar[aá]/i,
  /\bROI esperado\b/i,
  /\buplift\b/i,
  /\bcausar[aá]\b/i,
  /\bincrementar[aá] la recuperaci[oó]n\b/i,
];

describe("los textos de la app", () => {
  it("revisa todas las fuentes", () => {
    expect(propias.length).toBeGreaterThan(30);
  });

  it("no promete causalidad, salvo para negarla", () => {
    const hallazgos: string[] = [];
    for (const [ruta, texto] of propias) {
      for (const patron of CAUSALES) {
        for (const m of texto.matchAll(new RegExp(patron.source, "gi"))) {
          const antes = texto.slice(Math.max(0, m.index - 60), m.index);
          if (!/\b(no|ni|sin|nunca)\b[^.]*$/i.test(antes)) hallazgos.push(`${ruta}: ${m[0]}`);
        }
      }
    }
    expect(hallazgos).toEqual([]);
  });

  it("no carga nada de fuera: ni CDN, ni fuentes, ni telemetria", () => {
    const externos: string[] = [];
    for (const [ruta, texto] of [...propias, ...Object.entries(INDEX)]) {
      for (const m of texto.matchAll(/https?:\/\/[^\s"'`)]+/g)) {
        if (!/^https?:\/\/(localhost|127\.0\.0\.1)([:/]|$)/.test(m[0])) {
          externos.push(`${ruta}: ${m[0]}`);
        }
      }
    }
    expect(externos).toEqual([]);
    for (const [ruta, texto] of propias.filter(([r]) => r.endsWith(".css"))) {
      expect(texto, ruta).not.toMatch(/@import|url\(\s*["']?(https?:)?\/\//);
    }
  });

  it("no inserta HTML crudo", () => {
    for (const [ruta, texto] of propias) {
      expect(texto, ruta).not.toMatch(/dangerouslySetInnerHTML|\.innerHTML\s*=/);
    }
  });
});
