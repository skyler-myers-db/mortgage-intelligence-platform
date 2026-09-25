/// <reference types="vitest" />
import path from "node:path";
import { defineConfig, type Plugin } from "vite";
import babel from "@rolldown/plugin-babel";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import { bootModulePlugin } from "./src/lib/bootModulePlugin.ts";
import { shellSkeleton } from "./src/lib/shellSkeleton.ts";

/** Written beside the build manifest; postbuild moves both to build-meta/. */
const CHUNK_MODULES_FILE = "build-modules.json";

/**
 * Records which modules each emitted chunk renders, and every module the
 * entry reaches through STATIC import edges, as `build-modules.json` (audit
 * bundle-03). The manifest names chunks, not modules, so this is what
 * tools/check_frontend_budgets.mjs reads to prove no vendor chunk holds a
 * module on its LAZY_ONLY_VENDOR_MODULES list (a lazy-only module, such as
 * the infinite query observer only the lazy Console uses, pulled into a
 * vendor chunk would make every first paint download it), and that the list
 * still matches what the lazy chunks render. The static reach
 * (`entryStaticModules`) over-approximates what the first paint needs: it
 * follows barrel re-exports that tree-shaking later drops (the @tanstack
 * index.js files re-export every hook), so it names useInfiniteQuery.js too
 * and is only a backstop for packages the entry never imports. Ids are
 * relative to the frontend root, and a node_modules id is keyed from its last
 * `node_modules/` segment: the build resolves symlinks first, so a worktree
 * whose frontend/node_modules is a symlink to another tree would otherwise
 * record `../../<other-tree>/frontend/node_modules/...` (the budget tool keys
 * ids the same way, so it reads either shape).
 */
function chunkModulesManifest(): Plugin {
  let root = "";
  const relative = (id: string) => {
    const bare = id.replace(/^\0/, "").split("?")[0];
    const rel = path.isAbsolute(bare) ? path.relative(root, bare).split(path.sep).join("/") : bare;
    const vendored = rel.lastIndexOf("node_modules/");
    return vendored === -1 ? rel : rel.slice(vendored);
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

/** Geist + Geist Mono, one variable woff2 each (src/design-system/tokens.css). */
const WEBFONT_FILES = 2;

/**
 * Preloads the two webfonts from the built index.html (audit bundle-05).
 * Without it a font is requested only after the stylesheet is parsed and
 * text is laid out, so every cold load painted the fallback first. The
 * hashed file names exist only in the bundle, so the links are injected here
 * (build only) and index.html's source stays unchanged; no inline script, and
 * CSP `font-src 'self'` already covers the fetch. `crossorigin` is required:
 * fonts are fetched in CORS mode, and a preload without it is not reused
 * (the font would be fetched twice). Throws unless exactly two woff2 files
 * were emitted, so an unresolved url() in tokens.css (which Vite only warns
 * about) or a stray format fails the build.
 */
function preloadWebfonts(): Plugin {
  let base = "/";
  return {
    name: "mip:preload-webfonts",
    apply: "build",
    configResolved(config) {
      base = config.base;
    },
    transformIndexHtml: {
      order: "post",
      handler(_html, ctx) {
        const fonts = Object.values(ctx.bundle ?? {})
          .map((output) => output.fileName)
          .filter((fileName) => /\.(?:woff2?|ttf|otf)$/.test(fileName));
        if (fonts.length !== WEBFONT_FILES || !fonts.every((fileName) => fileName.endsWith(".woff2"))) {
          throw new Error(`expected exactly ${WEBFONT_FILES} woff2 webfonts in the bundle, found: ${fonts.join(", ") || "none"}`);
        }
        return fonts.sort().map((fileName) => ({
          tag: "link",
          attrs: { rel: "preload", as: "font", type: "font/woff2", href: `${base}${fileName}`, crossorigin: "" },
          injectTo: "head" as const,
        }));
      },
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
    preloadWebfonts(),
    bootModulePlugin(),
    shellSkeleton(),
  ],
  build: {
    // The chunk graph tools/check_frontend_budgets.mjs measures (initial and
    // per-route closures). A non-dot name, so it is never mistaken for Vite's
    // default `.vite/manifest.json`; tools/postbuild_artifacts.mjs moves it
    // out of dist into frontend/build-meta/ before anything is served or
    // uploaded, because the SPA fallback would serve any real file in dist.
    manifest: "build-manifest.json",
    // Source maps for production debugging (audit stack-01), emitted WITHOUT
    // the `//# sourceMappingURL=` comment so no browser ever asks for one.
    // tools/postbuild_artifacts.mjs moves every .map out of dist into
    // frontend/sourcemaps/ (the CI artifact) and fails the build if a map or
    // a sourceMappingURL comment is left in dist, because the SPA fallback
    // would serve a map from dist and the bundle would deploy it.
    sourcemap: "hidden",
    rolldownOptions: {
      output: {
        // Vendor chunks (audit bundle-03): the framework code that changes
        // only with a lockfile bump gets its own long-cached chunk, so an app
        // edit no longer re-hashes it. Exactly two groups, both limited by
        // `$initial` to modules the initial chunks need: a lazy-only module
        // (useInfiniteQuery / infiniteQueryObserver, used only by the lazy
        // Console) stays in its lazy chunk instead of joining every first
        // paint. tools/check_frontend_budgets.mjs fails if a vendor chunk
        // leaves the initial closure, imports an app chunk, or holds a module
        // on its LAZY_ONLY_VENDOR_MODULES list (what a groups-free build
        // renders only in lazy chunks, kept exact against each build);
        // dropping `$initial` from vendor-data fails it on those two modules.
        // Separator-agnostic tests.
        codeSplitting: {
          groups: [
            {
              name: "vendor-react",
              test: /[\\/]node_modules[\\/](?:react|react-dom|scheduler|react-router)[\\/]/,
              tags: ["$initial"],
              priority: 20,
            },
            {
              name: "vendor-data",
              test: /[\\/]node_modules[\\/]@tanstack[\\/](?:react-query|query-core)[\\/]/,
              tags: ["$initial"],
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
