/**
 * The bundle treemap build (audit quality-08, W5e w5-deps-report). Run it with
 * `npm --prefix frontend run build:analyze`; CI's bundle-delta job uploads the
 * treemap as the `bundle-treemap-<sha>` PR artifact (docs/testing.md).
 *
 * It is the production config plus rollup-plugin-visualizer, built into
 * bundle-analyze/ (git-ignored, so neither the bundle sync nor
 * scripts/package_source.sh ever ships it), never into dist/. The production
 * vite.config.ts never imports the visualizer (pinned by
 * tests/unit/test_bundle_treemap_contract.py), and this file refuses to load
 * without ANALYZE=1 so it cannot stand in for the real build by accident.
 */
import { visualizer } from "rollup-plugin-visualizer";
import { mergeConfig } from "vite";
import productionConfig from "./vite.config.ts";

if (process.env.ANALYZE !== "1") {
  throw new Error("vite.analyze.config.ts builds only with ANALYZE=1 (npm run build:analyze)");
}

export default mergeConfig(productionConfig, {
  build: {
    outDir: "bundle-analyze/dist",
    emptyOutDir: true,
  },
  plugins: [
    visualizer({
      template: "treemap",
      filename: "bundle-analyze/treemap.html",
      gzipSize: true,
      brotliSize: true,
      sourcemap: false,
    }),
  ],
});
