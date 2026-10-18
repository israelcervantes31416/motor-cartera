// ESLint de la superficie web: TypeScript con tipos (strict), las reglas de los hooks de React y
// las prohibiciones del proyecto. La accesibilidad se prueba con axe en los componentes y en el
// navegador (src/test/axe.ts, e2e/accesibilidad.spec.ts).
import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig(
  {
    ignores: [
      "dist",
      "coverage",
      "playwright-report",
      "test-results",
      "blob-report",
      "src/api/esquema.ts",
    ],
  },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
      globals: globals.browser,
    },
  },
  reactHooks.configs.flat["recommended-latest"],
  {
    rules: {
      // No hay HTML crudo en esta app: nada del backend se interpreta como marcado.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Sin dangerouslySetInnerHTML: el texto del backend se muestra como texto.",
        },
        {
          selector: "MemberExpression[property.name='innerHTML']",
          message: "Sin innerHTML: el texto del backend se muestra como texto.",
        },
      ],
      "no-restricted-imports": [
        "error",
        {
          paths: [
            { name: "axios", message: "fetch con src/api/cliente.ts." },
            { name: "lodash", message: "Lo que hace falta ya esta en el lenguaje." },
            { name: "moment", message: "Intl, en src/lib/formato.ts." },
            { name: "date-fns", message: "Intl, en src/lib/formato.ts." },
            { name: "redux", message: "Estado del servidor en TanStack Query; local en React." },
          ],
        },
      ],
      "no-eval": "error",
      "no-implied-eval": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      eqeqeq: ["error", "always"],
    },
  },
  {
    files: ["**/*.js", "**/*.mjs", "scripts/**"],
    extends: [tseslint.configs.disableTypeChecked],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["vite.config.ts", "vitest.config.ts", "playwright.config.ts", "proxy-api.ts", "e2e/**"],
    languageOptions: { globals: globals.node },
  },
);
