/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
  test: {
    environment: "node",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    // Vitest's default globs would pick up `tests/e2e/*.spec.ts` — the
    // Playwright suite — and try to run those through Vitest's runner.
    // Those files import `@playwright/test` and call `test.describe` /
    // `test.skip` which only work under the Playwright runner. Exclude
    // them explicitly; `playwright.config.ts` is the source of truth
    // for e2e discovery. Also drop the Vitest defaults we still need
    // (node_modules, dist, .idea, .git, .cache).
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.{idea,git,cache,output,temp}/**",
      "tests/e2e/**",
    ],
  },
});
