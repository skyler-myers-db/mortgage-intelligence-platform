/// <reference types="vitest" />
import { defineConfig } from "vite";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [
    react(),
    babel({
      presets: [reactCompilerPreset()],
    }),
  ],
  build: {
    // The chunk graph tools/check_frontend_budgets.mjs measures (initial and
    // per-route closures). A non-dot name, so it is never mistaken for Vite's
    // default `.vite/manifest.json`; tools/postbuild_artifacts.mjs moves it
    // out of dist into frontend/build-meta/ before anything is served or
    // uploaded, because the SPA fallback would serve any real file in dist.
    manifest: 'build-manifest.json',
    // Source maps for production debugging (audit stack-01), emitted WITHOUT
    // the `//# sourceMappingURL=` comment so no browser ever asks for one.
    // tools/postbuild_artifacts.mjs moves every .map out of dist into
    // frontend/sourcemaps/ (the CI artifact) and fails the build if a map or
    // a sourceMappingURL comment is left in dist, because the SPA fallback
    // would serve a map from dist and the bundle would deploy it.
    sourcemap: 'hidden',
  },
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
