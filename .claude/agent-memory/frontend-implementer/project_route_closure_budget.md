---
name: route-closure-budget-single-module-chunks
description: A new tiny shared component becomes its own one-module chunk and costs ~0.3 KiB br in EVERY route closure that imports it; glossary's closure has the least headroom.
metadata:
  type: project
---

`npm --prefix frontend run budget` gates each route's closure (route chunk +
static imports beyond the initial closure, brotli q11) in
`tools/check_frontend_budgets.mjs` `routes: {...}`. Rolldown puts a module in
its own chunk when no existing chunk has the same set of reaching entries, so a
3-line shared component (SurfaceTitle, like PageShell / Skeleton / Timestamp)
ships as a ~380 B raw / ~290 B br file: brotli barely compresses tiny files.

Measured 2026-09-25 (a11y-03 SurfaceTitle): +0.29 to +0.42 KiB br on every
route closure that imports it; glossary went 8.88 -> 9.17 against a 9.00 gate
(the only red one). asset (8 KiB gate) and borrower-360 were next tightest.

**Why:** a shared-component slice looks free in the diff and still reds the
budget on the route with the smallest headroom.

**How to apply:**
- Before adding a new shared component, expect +~0.3 KiB br per importing route
  and check the tightest gates (glossary, asset).
- Get a true baseline without stashing: `git archive HEAD frontend tools
  design_files tests/fixtures backend/resources | tar -x -C <scratch>/base`,
  symlink node_modules into `<scratch>/base/frontend`, then
  `npm --prefix <scratch>/base/frontend run build && ... run budget`
  (frontend tests import `tests/fixtures` and `backend/resources`, so tsc -b
  fails without them).
- `build-meta/build-manifest.json` + `build-modules.json` show which chunk a
  module landed in; `dist/` itself is deny-listed for ls/Read, but a node script
  can read and brotli a chunk.
- Never self-bump (see [[css-budget-headroom-is-thin]]): report the measured
  closure and the proposed gate; the budget owner re-baselines.

**Resolution that worked (2026-09-25, w4 pre-cut):** a shared primitive belongs IN components/Primitives.tsx (the shared chunk every route already loads), not its own module: moving SurfaceTitle there took glossary from 9.17 back to 8.88 KiB br with no gate raised. Try that before any budget re-baseline.
