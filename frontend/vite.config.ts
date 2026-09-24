/// <reference types="vitest" />
import path from "node:path";
import { defineConfig, type Plugin } from "vite";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";

/** Written beside the build manifest; postbuild moves both to build-meta/. */
const CHUNK_MODULES_FILE = "build-modules.json";

/**
 * Records which modules each emitted chunk holds, and every module the entry
 * reaches through STATIC imports alone, as `build-modules.json` (audit
 * bundle-03). The manifest names chunks, not modules, so this is what lets
 * tools/check_frontend_budgets.mjs prove a vendor chunk holds only modules the
 * first paint needs anyway: a lazy-only module (for example a query observer
 * only the lazy Console uses) pulled into a vendor chunk would make every
 * first paint download it. Ids are relative to the frontend root.
 */
function chunkModulesManifest(): Plugin {
  let root = "";
  const relative = (id: string) => {
    const bare = id.replace(/^\0/, "").split("?")[0];
    return path.isAbsolute(bare) ? path.relative(root, bare).split(path.sep).join("/") : bare;
  };
  return {
    name: "mip:chunk-modules-manifest",
    apply: "build",
    configResolved(config) {
      root = config.root;
    },
    generateBundle(_options, bundle) {
      const chunks: Record<string, string[]> = {};
      const queue: string[] = [];
      for (const output of Object.values(bundle)) {
        if (output.type !== "chunk") continue;
        chunks[output.fileName] = output.moduleIds.map(relative).sort();
        if (output.isEntry && output.facadeModuleId) queue.push(output.facadeModuleId);
      }
      const reached = new Set<string>();
      while (queue.length > 0) {
        const id = queue.shift() as string;
        if (reached.has(id)) continue;
        reached.add(id);
        queue.push(...(this.getModuleInfo(id)?.importedIds ?? []));
      }
      this.emitFile({
        type: "asset",
        fileName: CHUNK_MODULES_FILE,
        source: `${JSON.stringify({ chunks, entryStaticModules: [...reached].map(relative).sort() }, null, 1)}\n`,
      });
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    babel({
      presets: [reactCompilerPreset()],
    }),
    chunkModulesManifest(),
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
    rolldownOptions: {
      output: {
        // Vendor chunks (audit bundle-03): the framework code that changes
        // only with a lockfile bump gets its own long-cached chunk, so an app
        // edit no longer re-hashes it. Exactly two groups, both limited to
        // modules the entry reaches statically (`$initial`): a lazy-only
        // module (useInfiniteQuery / infiniteQueryObserver, used only by the
        // lazy Console; @tanstack/react-virtual, used only by LeadTable)
        // stays in its lazy chunk instead of joining every first paint.
        // tools/check_frontend_budgets.mjs fails if a vendor chunk leaves the
        // initial closure, imports an app chunk, or holds a module the entry
        // does not reach statically. Separator-agnostic tests.
        codeSplitting: {
          groups: [
            {
              name: 'vendor-react',
              test: /[\\/]node_modules[\\/](?:react|react-dom|scheduler|react-router)[\\/]/,
              tags: ['$initial'],
              priority: 20,
            },
            {
              name: 'vendor-data',
              test: /[\\/]node_modules[\\/]@tanstack[\\/](?:react-query|query-core)[\\/]/,
              tags: ['$initial'],
              priority: 10,
            },
          ],
        },
      },
    },
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
