---
name: perf-baselines
description: Bundle budget headroom policy (actuals +5%) in check_frontend_budgets.mjs; precompression stack; known-cosmetic build warnings; cache-semantics conventions per repo
metadata:
  type: project
---

Performance baselines and conventions established 2026-06-10/11:

- **Budget policy** lives in `tools/check_frontend_budgets.mjs`: every gate =
  measured actual + ~5% headroom; ratchet DOWN on wins; bumps must cite the
  feature + new actuals. Never bump-to-green. (The old style accreted until
  totalJs had 0.03 KiB slack and tripped on a 0.4 KiB a11y fix.)
- **Precompression**: `tools/precompress_assets.mjs` (build-time .br/.gz,
  wired into `npm run build`) + `backend/services/static_assets.py` +
  negotiated `/assets/{path}` route in `backend/main.py`. Live-proven −73%
  brotli on initial JS through the Databricks Apps edge. Runtime
  GZipMiddleware is `compresslevel=6`, dynamic JSON only.
- **Cache semantics convention** (deliberate, divergent by surface):
  portfolio preview + ALL geo rollups = `get_or_set(stale_if_error=True)`
  (read-only viz, serve last-good); segment list = fail-visible (pinned by
  `test_segment_list_expired_cache_failure_propagates`); cold-cache failures
  always propagate. Don't "unify" these without reading those tests.
- **Known-cosmetic build warnings**: rolldown `INEFFECTIVE_DYNAMIC_IMPORT`
  on `lib/drawerSources.ts` (idle preloader intentionally warms the shared
  lazy chunk — documented in AppShell.tsx; do not "fix"); `PLUGIN_TIMINGS`
  (React Compiler babel = ~90% of the ~5s build).
- **Largest lazy chunk** (~98 KiB "Icon-*.js") is the shared
  components+drawerSources chunk, NOT icon bloat — Icon.tsx is 87 lines.
- React Compiler opt-outs (`'use no memo'`) remain on LeadTable.tsx +
  analytics.{tsx,charts,sections} — revisit after query-layer state
  mirroring is gone.
