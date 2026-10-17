import { defineConfig, mergeConfig } from "vitest/config";

import configuracion from "./vite.config.ts";

export default mergeConfig(
  configuracion,
  defineConfig({
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/preparacion.ts"],
      include: ["src/**/*.test.{ts,tsx}"],
      restoreMocks: true,
      // Cada worker carga jsdom entero: pocos a la vez, para no pedirle de mas a la memoria.
      maxWorkers: 3,
      testTimeout: 20_000,
      coverage: {
        provider: "v8",
        include: ["src/**/*.{ts,tsx}"],
        exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/api/esquema.ts", "src/main.tsx"],
        reporter: ["text-summary", "text"],
        thresholds: {
          lines: 80,
          "src/lib/**": { lines: 90 },
          "src/api/cliente.ts": { lines: 90 },
          "src/components/Estado.tsx": { lines: 90 },
        },
      },
    },
  }),
);
