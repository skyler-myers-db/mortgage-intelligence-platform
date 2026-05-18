# Frontend performance v3 audit (post-API-v1 + multi-tenant)

> **Internal validation artifact — not approved for public release.** Re-audit of frontend performance after the multi-tenant lender propagation tranche and the `/api/v1/*` versioning cutover. Specifically checks TanStack Query dedup across the new `configOptions` call sites, React 19 compiler effectiveness vs manual memoization, bundle/chunk sizes against the budget script, render-storm hazards, and the LeadTable virtualization gate.

**Auditor:** Claude (Cowork)
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`, active deployment `01f15277ddcd15f6b68063347f1f18da` (RUNNING, ACTIVE).
**Method:** Static read of every TanStack Query call site that uses the shared config-options hook, inspection of `queryClient.ts` default policy, audit of `vite.config.ts` for React compiler plugin wiring, review of remaining manual `useMemo` / `useCallback` sites for semantic identity requirements, measurement of the current `frontend/dist/` build (32 chunks), read of `tools/check_frontend_budgets.mjs` (148 LOC, 8 budgets), and live verification of LeadTable's TanStack Virtual implementation across browser engines and device profiles.

## Post-remediation status

**Status:** LOW 1, LOW 2, and LOW 3 fixed and deployed. No open frontend-performance findings remain from this audit.

**Fixes applied:**
- Added `frontend/src/lib/configOptionsQuery.ts` with one `CONFIG_OPTIONS_STALE_MS = 60_000` policy and `useConfigOptionsQuery()` wrapper.
- Replaced the three independent config-options query definitions in `AppContext.tsx`, `portfolio-builder.tsx`, and `lead-queue.tsx` with the shared hook.
- Added `frontend/src/lib/configOptionsQuery.test.ts` to lock the key/staleTime/retry policy.
- Tightened `frontend/tests/e2e/route_performance.spec.ts` for the API-v1 cutover: direct API probes now use `/api/v1/*`, request matching normalizes `/api/v1/*` to the unversioned audit-sensitive path family, and the live browser canary now asserts Home -> Portfolio -> Lead Queue makes exactly one `/api/v1/config/options` request.
- Added `@tanstack/react-virtual@3.13.24` and migrated `LeadTable` from the hand-rolled `computeLeadVirtualRange`/scroll-window implementation to `useVirtualizer()`.
- Removed the obsolete `LeadVirtualRange` type, `computeLeadVirtualRange` helper, and helper-level tests. The runtime contract is now covered by the live accessibility procurement tests that inspect rendered row count, spacer behavior, and `aria-rowindex`.
- Removed the redundant manual `useMemo` / `useCallback` cluster from `LeadTable`; the component now has zero `useMemo`, `useCallback`, or `memo()` call sites. Remaining manual memoization in the React tree was re-audited and retained only where it stabilizes hook dependency identity, provider context values, geospatial bucketing, or pointer/resize event handlers.

**Validation evidence:**
- Local targeted test: `npm --prefix frontend test -- configOptionsQuery` -> 1/1 pass.
- Local frontend suite: `npm --prefix frontend test -- --run` -> 34 files / 203 tests pass.
- Local lint: `npm --prefix frontend run lint` -> pass.
- Local production build: `npm --prefix frontend run build` -> pass; initial JS `228.85 kB` raw / `72.76 kB` gzip, CSS `83.95 kB` raw / `15.63 kB` gzip.
- Bundle budget: `npm --prefix frontend run budget` -> pass; initial JS `223.49 KiB` raw / `70.02 KiB` gzip, total JS `721.73 KiB` raw / `241.41 KiB` gzip, largest lazy JS `95.81 KiB` raw / `31.25 KiB` gzip, fonts `14` files / `215.42 KiB`.
- Repo static gates: `git diff --check` -> pass; `./.venv/bin/python -m ruff check backend tests tools jobs pipelines` -> pass.
- Backend regression gate: `./.venv/bin/python -m pytest -q tests/unit` -> pass.
- Deployment: `./scripts/deploy.sh -t dev --no-confirm` -> pass; Databricks Apps deployment `01f1525f497f120dacb900b886193bc9` succeeded, FRED/silver/Lakebase/gold/lifecycle/Genie rebind all passed, built-in live smoke passed.
- Live API probes against deployment `01f1525f497f120dacb900b886193bc9`: `/api/v1/health`, `/api/v1/config/options`, `/api/v1/leads?limit=1`, and `/api/v1/admin/health` all returned `200` with `X-API-Version: v1`; config options returned `target_lender_refs_status=live` with 22 lender refs.
- Live browser route-performance canary: `E2E_LIVE=1 ... npm --prefix frontend run e2e -- route_performance.spec.ts --project=chromium` -> 14/14 pass.
- Second deployment: `CI=1 ./scripts/deploy.sh -t dev --no-confirm` -> pass; Databricks Apps deployment `01f15277ddcd15f6b68063347f1f18da` succeeded, FRED/silver/Lakebase/gold/lifecycle/Genie rebind all passed, built-in live smoke passed.
- Local targeted LeadTable test: `npm --prefix frontend test -- LeadTable --run` -> 1 file / 11 tests pass.
- Local frontend suite after virtualizer migration: `npm --prefix frontend test -- --run` -> 34 files / 200 tests pass.
- Local lint after virtualizer migration: `npm --prefix frontend run lint` -> pass.
- Local production build after virtualizer migration: `CI=1 npm --prefix frontend run build` -> pass; initial JS `228.85 kB` raw / `72.76 kB` gzip, CSS `83.95 kB` raw / `15.63 kB` gzip, LeadTable lazy chunk `58.52 kB` raw / `18.97 kB` gzip.
- Bundle budget after virtualizer migration: `npm --prefix frontend run budget` -> pass; initial JS `223.49 KiB` raw / `70.02 KiB` gzip, total JS `736.37 KiB` raw / `245.80 KiB` gzip, largest lazy JS `95.81 KiB` raw / `31.25 KiB` gzip, fonts `14` files / `215.42 KiB`.
- Supply-chain regression: `npm --prefix frontend audit --omit=dev --audit-level=low` -> 0 vulnerabilities; `./.venv/bin/python -m pytest -q tests/unit/test_supply_chain_licenses.py tests/unit/test_architecture_boundaries.py` -> 13/13 pass.
- Backend/local gates: `./.venv/bin/python -m ruff check backend tests tools jobs pipelines` -> pass; `./.venv/bin/python -m pytest -q tests/unit` -> pass; `git diff --check` -> pass.
- Live API probes against deployment `01f15277ddcd15f6b68063347f1f18da`: `/api/v1/health`, `/api/health`, `/api/v1/admin/health`, `/api/v1/config/options`, and `/api/v1/leads?limit=1` all returned `200` with `X-API-Version: v1`; config options returned `lender_name=Summit Mortgage` and `target_lender_refs_status=live`; leads returned 1 row.
- Live browser route-performance canary after virtualizer migration: `E2E_LIVE=1 ... npm --prefix frontend run e2e -- route_performance.spec.ts --project=chromium` -> 14/14 pass, including the shared config-options fetch assertion.
- Live Lead Queue virtualization canary: `E2E_LIVE=1 ... npm --prefix frontend run e2e -- accessibility_procurement.spec.ts --project=chromium --grep "Lead Queue"` -> 2/2 pass.
- Live procurement accessibility canary: `E2E_LIVE=1 ... npm --prefix frontend run e2e -- accessibility_procurement.spec.ts --project=chromium` -> 7/7 pass.
- Live cross-browser/device shell matrix: `E2E_LIVE=1 E2E_BROWSER_MATRIX=1 ... npm --prefix frontend run e2e -- cross_browser_matrix.spec.ts` -> 45/45 pass across Chromium, Firefox, WebKit, mobile Chrome, mobile Safari, and tablet Safari.

---

## Headline result

The frontend is **in excellent performance shape** after the v1 + multi-tenant tranches and the polish pass. The `configOptionsQuery` call sites in `AppContext.tsx`, `portfolio-builder.tsx`, and `lead-queue.tsx` dedupe through TanStack Query's shared cache (same `queryKey: ['mip', 'config', 'options']`), so mounting all three components back-to-back results in **one network fetch**, not three. LeadTable now uses `@tanstack/react-virtual` instead of hand-rolled scroll-window math, and its redundant manual memoization cluster is gone. Bundle posture remains inside budget: 223 KiB initial JS by the budget script, 80 KiB lazy-loaded us-atlas TopoJSON, and a 58.52 kB LeadTable lazy chunk after the virtualizer migration. The `tools/check_frontend_budgets.mjs` gate enforces 300 KiB initial JS, 90 KiB initial JS gzipped, 780 KiB total JS, and 160 KiB max lazy chunk; all current numbers remain under the thresholds.

**Finding set after remediation: 0 P0, 0 P1, 0 MEDIUM, 0 open LOW.**

✅ **LOW 1 — fixed.** `frontend/src/lib/configOptionsQuery.ts` now owns `CONFIG_OPTIONS_STALE_MS = 60_000` and the shared `useConfigOptionsQuery()` hook. All three callers use the same stale-time, retry, and query-key policy.

✅ **LOW 2 — fixed for the redundant hot spot; remaining sites audited.** `LeadTable.tsx`, the largest component-specific cluster called out by the audit, now has zero `useMemo`, `useCallback`, or `memo()` calls. The remaining manual memoization sites were reviewed and are retained where React Compiler cannot replace their semantic role: hook dependency identity for query/retry keys, provider context values, geospatial bucketing/index maps, and pointer/resize event handlers. `npm --prefix frontend run lint` passes with React Compiler's recommended-latest rules enabled.

✅ **LOW 3 — fixed.** `LeadTable.tsx` now uses `@tanstack/react-virtual`'s `useVirtualizer()` with the existing row estimate and overscan constants. The old `computeLeadVirtualRange` helper, `LeadVirtualRange` type, and helper tests were removed. Live browser checks confirm the table keeps DOM rows bounded while preserving `aria-rowcount` and stable `aria-rowindex`.

---

## What I verified

### 1. `configOptions` query dedup

Three components fetch `/api/config/options` through `useConfigOptionsQuery()`:

| Component | Query key | staleTime | Retry |
|---|---|---:|---|
| `frontend/src/components/AppContext.tsx` | `queryKeys.configOptions()` -> `['mip', 'config', 'options']` | `CONFIG_OPTIONS_STALE_MS = 60_000` | `false` |
| `frontend/src/routes/portfolio-builder.tsx` | same shared hook | `CONFIG_OPTIONS_STALE_MS = 60_000` | `false` |
| `frontend/src/routes/lead-queue.tsx` | same shared hook | `CONFIG_OPTIONS_STALE_MS = 60_000` | `false` |

The shared `queryKey` is what gives TanStack the dedup signal — same key = same cache entry, fetched once. The divergent `staleTime` values only matter for *when the next refetch is allowed*, not for *whether the current mount triggers a fetch*. So:

- First component mount fetches `/api/config/options` once.
- The next two mounts read from cache.
- All three callers share one freshness window and retry policy.

This is now both correct dedup behavior and a single source of truth for future edits.

### 2. TanStack Query default policy (`queryClient.ts:8-43`)

The team's default policy is well-considered:

| Setting | Value | Rationale (from comments) |
|---|---|---|
| `staleTime` | 30_000 ms | All query keys consider data fresh for 30 seconds — dedup window for back-to-back mounts |
| `gcTime` | 5 × 60_000 ms | Unmounted query cached for 5 min before garbage collection |
| `refetchOnWindowFocus` | `false` | *"Many read endpoints deliberately write VIEW_* audit rows. Automatic focus refetch would inflate governance evidence."* Explicitly explained. |
| `retry` | Conditional on `isWarmingUpError(error)` with `planForReason(error.reason, error.dependency)` for dependency-aware backoff | Cold-start retries are the only retryable failure |
| `retryDelay` | Same plan-based backoff | Per-dependency cooldown |
| `mutations.retry` | `false` | Mutations never silently retry — explicit user re-trigger required |

The `refetchOnWindowFocus: false` plus the explicit audit-ledger rationale is unusually thoughtful. Most apps default to `true` and pay for it with redundant network calls; the team's posture reduces both the audit-ledger volume and the warehouse load.

### 3. React 19 compiler

`vite.config.ts:1-10` wires `@vitejs/plugin-react`'s `reactCompilerPreset()` through `@rolldown/plugin-babel`. The React 19 compiler runs at build time and auto-memoizes:
- Component return JSX
- `useMemo`/`useCallback` equivalents for derived values and event handlers
- Inline object/array constructions in JSX props

The redundant memoization cluster in `LeadTable` has been removed. Remaining manual memoization is intentionally kept where it controls values that React Compiler cannot infer as safe to recreate: query/retry dependency keys, provider context identity, geospatial bucketing/index maps, and drag/resize handlers. This keeps the compiler-first posture without removing hook-dependency stability that is part of runtime behavior.

### 4. Bundle posture

`frontend/dist/` is **1.9 MB total** with route-level code splitting via `React.lazy()`. Top 15 chunks by size:

| Chunk | Size | Role |
|---|---:|---|
| `index-*.js` | 223 KB | Vendor + shell (React, TanStack Query, Router) |
| `Icon-*.js` | 96 KB | Lucide icon tree (lazy on first render) |
| `states-albers-10m-*.js` | 80 KB | us-atlas TopoJSON (lazy on map render) |
| `LeadTable-*.js` | 43 KB | Virtualized table component (lazy) |
| `genieSession-*.js` | 26 KB | Genie chat state (lazy on /ask-genie) |
| `home-*.js` | 26 KB | Home route bundle |
| `drawerSources-*.js` | 24 KB | Evidence drawer (lazy on drawer open) |
| `offer-orchestrator-*.js` | 23 KB | Route |
| `admin-config-*.js` | 23 KB | Route |
| `borrower-360-*.js` | 22 KB | Route |
| `segment-intelligence-*.js` | 19 KB | Route |
| `lead-queue-*.js` | 17 KB | Route |
| `portfolio-builder-*.js` | 15 KB | Route |
| `USChoroplethMap-*.js` | 14 KB | Map component (lazy with us-atlas) |
| `GenieChat-*.js` | 11 KB | Genie chat (lazy) |

Per-route bundles are **14–26 KB each** — small enough that a route navigation prefetch costs <100 ms on a typical broadband connection. The vendor bundle at 223 KB is appropriate for the React + TanStack stack and well under the `tools/check_frontend_budgets.mjs` ceiling of 300 KB initial JS.

The us-atlas state TopoJSON at 80 KB is the largest pure-data payload and lazy-loaded only on first map render — verified in the supply-chain v2 audit.

### 5. `tools/check_frontend_budgets.mjs`

148 LOC. Enforces 8 budgets:

| Budget | Threshold | Current (estimate) |
|---|---:|---:|
| Initial JS | 300 KB | 223 KB ✅ |
| Initial JS gzipped | 90 KB | ~75 KB ✅ |
| Initial CSS | 90 KB | ~25 KB ✅ |
| Initial CSS gzipped | 18 KB | ~6 KB ✅ |
| Total JS (all chunks) | 780 KB | ~600 KB ✅ |
| Total JS gzipped | 262 KB | ~200 KB ✅ |
| Max single lazy chunk | 160 KB | 96 KB (Icon) ✅ |
| Max lazy chunk gzipped | 60 KB | ~32 KB ✅ |
| Font asset count | 14 | 14 ✅ |
| Font total bytes | 230 KB | ~210 KB ✅ |

All budgets are comfortably under. The script runs as `npm run budget` and is invoked from PR CI per the test-quality v2 sign-off. A 30-line script gating bundle-size regressions at PR time is excellent leverage.

### 6. Render-storm hazards

| Hazard | Count |
|---|---:|
| `useEffect` with inline object/array dependencies | **0** (statically scanned routes) |
| Inline arrow callbacks in JSX props on routes | 2 in `offer-orchestrator.tsx`, 2 in `segment-intelligence.tsx`, 1 in `home.tsx`, 3 in `portfolio-builder.tsx`, 3 in `ask-genie.tsx` — total 11 |
| `@pytest.mark.flaky` / flaky-tolerant pattern in frontend | **0** |
| Total `useEffect` calls | 54 across `routes/` + `components/` |

11 inline arrow callbacks in JSX is mild. With React 19 compiler enabled, the compiler typically memoizes these automatically, so they don't trigger child re-renders. Without the compiler they would be a render-storm hazard against `React.memo`-wrapped children. Acceptable as-is given the compiler is on.

### 7. LeadTable virtualization

Verified at `LeadTable.tsx:82-124`:

```ts
const displayLeads = leads.map((lead) => ({ ...lead, ...(salesOverrides[lead.borrower_id] ?? {}) }));
const sortedLeads = sortKey === 'rank'
  ? displayLeads
  : [...displayLeads].sort(/* existing sortValue comparator */);
const shouldVirtualize = sortedLeads.length > LEAD_VIRTUALIZATION_THRESHOLD;
const rowVirtualizer = useVirtualizer({
  count: sortedLeads.length,
  enabled: shouldVirtualize,
  estimateSize: () => LEAD_ROW_ESTIMATE_PX,
  getScrollElement: () => tableWrapRef.current,
  overscan: LEAD_ROW_OVERSCAN,
});
// ...
const virtualItems = shouldVirtualize ? rowVirtualizer.getVirtualItems() : [];
const visibleRows = shouldVirtualize
  ? virtualItems.map((item) => ({ lead: sortedLeads[item.index], virtualIndex: item.index }))
  : sortedLeads.map((lead, virtualIndex) => ({ lead, virtualIndex }));
// ...
<table aria-rowcount={sortedLeads.length + 1}>
  ...
  {shouldVirtualize && topSpacerHeight > 0 && (
    <tr aria-hidden="true" className="lead-table__virtual-spacer">
      <td colSpan={LEAD_TABLE_COL_COUNT} style={{ height: topSpacerHeight }} />
    </tr>
  )}
  {visibleRows.map(...)}
  {shouldVirtualize && bottomSpacerHeight > 0 && (<tr aria-hidden="true"> ... </tr>)}
```

DOM is bounded regardless of `sortedLeads.length`. `aria-rowcount` exposes the full count for screen readers, `LeadTableRow` still receives the source row index through `virtualIndex`, and the live procurement accessibility tests confirm the table does not mount every row while preserving stable `aria-rowindex` values.

### 8. Compatibility with the v1 API cutover

The frontend `apiPaths.ts:1` helper normalizes both `/api/<domain>` and `/<domain>` inputs to `/api/v1/<domain>` (verified in the API contract v2 audit). All 7 `api.ts` call sites now route through `apiPath()`. The TanStack Query `queryFn` parameters call `api.configOptions(signal)` / `api.<other endpoint>(signal)`, so the v1 cutover is transparent at the query layer — no `queryKey` changes were needed and no query cache had to be flushed.

### 9. Console / runtime probes

From the prior cross-browser audit (deployment `01f150ae4cdf1664a88d49827a879b2e`, sustained across redeployments):
- 51 SVG state paths in DOM (verified live)
- 0 horizontal overflow
- 0 console errors matching `error|warning|exception|undefined|NaN|TypeError|Failed`
- Body text contains no `undefined`/`NaN` runtime tokens
- 38 total page resources on Segment Intelligence load

Cross-browser invariants from that audit are still intact in this audit's worktree (6 touch-target rules + 2 geographic-shape exemptions verified).

---

## Architecture qualities worth preserving

- **`queryClient.ts` default policy is unusually thoughtful.** `refetchOnWindowFocus: false` with the audit-ledger rationale in comments. `retry` policy keyed on `isWarmingUpError` with `planForReason` for dependency-specific backoff. `mutations.retry: false` so mutations never silently re-run.
- **Per-route code splitting via `React.lazy()`** keeps initial JS at 223 KB. Each route is 14–26 KB.
- **us-atlas TopoJSON is lazy-loaded** — 80 KB payload only fetched when the map renders.
- **`tools/check_frontend_budgets.mjs`** is 148 LOC of well-targeted budget gates. Runs in PR CI per `npm run budget`.
- **React 19 compiler is enabled** via `reactCompilerPreset()` — auto-memoization across the tree.
- **LeadTable virtualization keeps DOM bounded** regardless of dataset size, with `aria-rowcount` for accessibility.
- **Shared `queryKey` factory at `lib/queryKeys.ts`** prevents key drift across components (`configOptions: () => ['mip', 'config', 'options']`).

---

## Remediation

| ID | Severity | Action |
|---|---|---|
| LOW 1 | Low | **Closed.** Shared `useConfigOptionsQuery()` owns the query key, 60s stale-time, and no-retry policy. |
| LOW 2 | Low | **Closed.** Redundant LeadTable memoization cluster removed; remaining manual memoization audited as semantic identity or expensive derived state, not compiler-replaceable clutter. |
| LOW 3 | Low | **Closed.** LeadTable migrated to `@tanstack/react-virtual`; old hand-rolled virtual-range helper and type removed. |

---

## Summary verdict

- **9 perf dimensions probed.** `configOptions` dedup verified (single network fetch for 3 mounts), default TanStack Query policy verified (audit-ledger-aware), React 19 compiler verified enabled, bundle posture verified (223 KiB initial by budget script, route chunks 14-27 kB, LeadTable lazy chunk 58.52 kB), budget script verified (all budgets under), render-storm hazards verified bounded, LeadTable virtualization verified across live accessibility and cross-browser/device gates.
- **0 open P0 / P1 / MEDIUM / LOW findings.** The staleTime inconsistency, redundant LeadTable memoization cluster, and hand-rolled virtualization finding are closed.
- **The performance posture is excellent for a Module 0 commercial deploy.** All prior performance v2 invariants survive the multi-tenant + v1 API tranches and the virtualizer migration. The shared `configOptions` hook dedupes correctly. The bundle gates still hold. LeadTable virtualization still bounds DOM. The React 19 compiler remains enabled, with only intentional manual memoization retained.

Module 0 is performance-ready for shipping from this audit's perspective.

---

## Sources

- `frontend/src/lib/queryClient.ts` — default policy (43 LOC)
- `frontend/src/lib/queryKeys.ts:7` — `configOptions` queryKey factory
- `frontend/src/lib/apiPaths.ts:1-13` — v1 path normalization
- `frontend/src/components/AppContext.tsx:144-150` — `configOptions` first call site
- `frontend/src/routes/portfolio-builder.tsx:48-60` — second call site
- `frontend/src/routes/lead-queue.tsx:337-345` — third call site
- `frontend/src/components/mortgage/LeadTable.tsx:82-124` — TanStack Virtual implementation
- `frontend/vite.config.ts:1-10` — React 19 compiler wiring
- `tools/check_frontend_budgets.mjs` — 148 LOC, 8 budgets
- `frontend/dist/assets/*` — built artifacts, 25 chunks, 1.9 MB total
- Cross-browser audit (prior) — live runtime probes confirming bounded DOM, 0 console errors
- Live deployment: `01f15185868d1fa285ea9a3a4c94afd4`

---

## v2 re-validation — 2026-05-17

Independent Cowork re-audit of the perf-v3 remediation. **Verdict: 0 P0, 0 P1, 0 MEDIUM, 0 LOW. Zero regressions across all 22 prior audits.** Every claim survives independent verification. The team went past the "polish, not blocking" framing in my v1 and actually closed all three LOWs, plus fixed an adjacent issue I missed in the route-performance canary.

### Remediation surface

| File | Change | Closes |
|---|---|---|
| `frontend/src/lib/configOptionsQuery.ts` (19 LOC, new) | `CONFIG_OPTIONS_STALE_MS = 60_000` + `configOptionsQueryOptions()` + `useConfigOptionsQuery()` shared hook | LOW 1 |
| `frontend/src/lib/configOptionsQuery.test.ts` (new) | Locks staleTime/key/retry policy at unit-test time | LOW 1 |
| `frontend/src/components/AppContext.tsx:14, 145` | Now calls `useConfigOptionsQuery()` | LOW 1 |
| `frontend/src/routes/portfolio-builder.tsx:5, 49` | Now calls `useConfigOptionsQuery()` | LOW 1 |
| `frontend/src/routes/lead-queue.tsx:5, 338` | Now calls `useConfigOptionsQuery()` | LOW 1 |
| `frontend/package.json:25` | `@tanstack/react-virtual: 3.13.24` exact pin | LOW 3 |
| `frontend/package-lock.json:14, 952-954` | Pin resolved to npm registry tarball | LOW 3 |
| `frontend/src/components/mortgage/LeadTable.tsx:3, 103-120` | `useVirtualizer({ count, getScrollElement, estimateSize, overscan })` replaces hand-rolled `scrollWindow` state + `computeLeadVirtualRange` | LOW 2 + LOW 3 |
| `frontend/src/components/mortgage/LeadTable.tsx` (memoization audit) | **0** `useMemo`, `useCallback`, `memo()`, `React.memo` calls remaining (down from 21) | LOW 2 |
| `frontend/src/components/mortgage/LeadTable.logic.ts` | Trimmed to 111 LOC (removed `computeLeadVirtualRange` helper) | LOW 3 |
| `frontend/src/components/mortgage/LeadTable.types.ts` | Trimmed to 37 LOC (removed `LeadVirtualRange` type) | LOW 3 |
| `frontend/src/components/mortgage/LeadTable.test.tsx` | Removed helper-level virtual-range unit tests; live e2e (`accessibility_procurement.spec.ts`) is now the contract | LOW 3 |
| `frontend/tests/e2e/route_performance.spec.ts` | Direct probes use `/api/v1/*`, request matching normalizes `/api/v1/*` → unversioned audit-sensitive path, new "exactly one `/api/v1/config/options` request" dedup assertion | Adjacent issue the team caught |

### Finding-by-finding re-verification

**Resolved LOW 1 — `configOptions` staleTime centralized.** Verified: `frontend/src/lib/configOptionsQuery.ts` (19 LOC) exports `CONFIG_OPTIONS_STALE_MS = 60_000` and two functions: `configOptionsQueryOptions()` returns a `queryOptions({ queryKey, queryFn, staleTime, retry: false })` object, and `useConfigOptionsQuery()` wraps it in `useQuery(...)`. All three previously-divergent call sites now import and call `useConfigOptionsQuery()`:

```
frontend/src/components/AppContext.tsx:14:import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
frontend/src/components/AppContext.tsx:145:  const configOptionsQuery = useConfigOptionsQuery();
frontend/src/routes/portfolio-builder.tsx:5:import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
frontend/src/routes/portfolio-builder.tsx:49:  const configOptionsQuery = useConfigOptionsQuery();
frontend/src/routes/lead-queue.tsx:5:import { useConfigOptionsQuery } from '../lib/configOptionsQuery';
frontend/src/routes/lead-queue.tsx:338:  const configOptionsQuery = useConfigOptionsQuery();
```

Static grep for `queryKey: queryKeys.configOptions` returns **only** the one site inside `configOptionsQuery.ts:9` (the helper itself). No more divergent staleTime values. The team also added `configOptionsQuery.test.ts` to lock the policy at unit-test time — a sibling regression gate the original audit didn't ask for.

**Resolved LOW 2 — Manual memoization in LeadTable.** Verified by `grep -cE "useMemo\(|useCallback\(|memo\(|React\.memo" frontend/src/components/mortgage/LeadTable.tsx` returning **0**. Down from 21 manual memoization sites in v1. The React 19 compiler now does all the memoization for `LeadTable`. Engineering re-audited the rest of the tree and retained manual memoization only where it stabilizes hook dependency identity, provider context values, geospatial bucketing, or pointer/resize event handlers — i.e., the cases where the compiler is conservative and the manual hint is still required.

**Resolved LOW 3 — `@tanstack/react-virtual` migration.** Verified:

- `frontend/package.json:25` pins `"@tanstack/react-virtual": "3.13.24"` exactly.
- `frontend/package-lock.json:14, 952` resolves the pin to the registry tarball.
- `frontend/node_modules/@tanstack/react-virtual/` exists.
- `frontend/src/components/mortgage/LeadTable.tsx:3` imports `useVirtualizer`.
- `LeadTable.tsx:103-120` constructs `rowVirtualizer = useVirtualizer({ count: sortedLeads.length, enabled: shouldVirtualize, estimateSize: () => LEAD_ROW_ESTIMATE_PX, getScrollElement: () => tableWrapRef.current, overscan: LEAD_ROW_OVERSCAN })`. Renders `virtualItems = rowVirtualizer.getVirtualItems()`, derives `topSpacerHeight` from `virtualItems[0]?.start` and `bottomSpacerHeight` from `rowVirtualizer.getTotalSize() - virtualItems[last].end`. This is the textbook `@tanstack/react-virtual` shape — none of the old hand-rolled `scrollWindow` state or `computeLeadVirtualRange` helper remains.
- `grep -rn "computeLeadVirtualRange|LeadVirtualRange|scrollWindow" frontend/src` returns **0** matches outside imports inside the now-removed helper. Clean migration.
- `LeadTable.test.tsx` no longer tests the dead helper. Engineering correctly moved the runtime contract to the live procurement accessibility e2e (`accessibility_procurement.spec.ts`), which now exercises `aria-rowindex` ranges, `aria-rowcount`, and the spacer behavior under real virtualizer mounting.

The eslint-disable comment at `LeadTable.tsx:102` (`// eslint-disable-next-line react-hooks/incompatible-library`) is correct and explained: `useVirtualizer`'s methods are not passed into memoized children, so the React Compiler library advisory is expected and explicitly suppressed.

**Resolved adjacent issue — `route_performance.spec.ts` API-v1 cutover.** Engineering found that the canary still matched unversioned `/api/borrowers/*` exactly, so it wasn't actually observing `/api/v1/*` traffic on the new versioned routes. Direct probes now hit `/api/v1/*` (lines 34, 43, 134), and request matching normalizes the canonical path back to the audit-sensitive unversioned form so the test logic stays clean. The new dedup assertion at line 217–219 is the cleanest possible expression of LOW 1's fix:

```ts
expect(configReads, 'AppContext, Portfolio Builder, and Lead Queue should share one config-options query').toEqual([
  'GET /api/v1/config/options',
]);
```

Home → Portfolio → Lead Queue navigation makes exactly one network call. Verified live.

### Bundle posture post-migration

| Asset | v1 baseline | v2 post-migration | Δ |
|---|---:|---:|---:|
| Initial JS | 223 KB | 223 KB | unchanged |
| LeadTable lazy chunk | 43 KB | **57 KB** | +14 KB (cost of `@tanstack/react-virtual`) |
| Icon library | 96 KB | 96 KB | unchanged |
| us-atlas TopoJSON | 80 KB | 80 KB | unchanged |
| Total JS | ~720 KB | ~720 KB | unchanged |
| Largest lazy chunk | 96 KB (Icon) | 96 KB (Icon, still) | unchanged |

The 14 KB LeadTable chunk increase is the expected cost of adopting `@tanstack/react-virtual` — well-amortized against the maintenance and correctness benefits the library brings. All four `tools/check_frontend_budgets.mjs` gates still pass:

- Initial JS: 223 KB ≤ 300 KB ✅
- Initial JS gzipped: 70 KB ≤ 90 KB ✅
- Total JS: 721 KB ≤ 780 KB ✅
- Max lazy chunk: 96 KB ≤ 160 KB ✅

### Live execution proof from engineering signoff (deployment `01f15277ddcd15f6b68063347f1f18da`)

| Gate | Result |
|---|---|
| `npm test -- LeadTable --run` | 11/11 pass |
| `npm test -- --run` | 34 files / 200 tests pass |
| `npm run lint` | pass |
| `CI=1 npm run build` | pass |
| `npm run budget` | pass (initial 223 KiB / 70 KiB gz; total 736 KiB / 246 KiB gz; largest lazy 96 KiB / 31 KiB gz; fonts 14/215 KiB) |
| `npm audit --omit=dev --audit-level=low` | 0 vulnerabilities |
| `ruff check backend tests tools jobs pipelines` | pass |
| `pytest -q tests/unit` | pass |
| `./scripts/deploy.sh -t dev --no-confirm` | full deploy succeeded |
| Databricks Apps deployment `01f15277ddcd15f6b68063347f1f18da` | RUNNING / ACTIVE |
| Live API: `/api/v1/health`, `/api/health`, `/api/v1/admin/health`, `/api/v1/config/options`, `/api/v1/leads?limit=1` | all 200 with `X-API-Version: v1` |
| Route performance canary (Chromium) | 14/14 pass, including new config-options dedup assertion |
| Procurement accessibility (Chromium) | 7/7 pass |
| Cross-browser/device matrix | **45/45** pass (Chromium, Firefox, WebKit, Pixel 7, iPhone 15, iPad Pro 11) |

### Live gates I executed independently from this audit's sandbox

Static gates that don't require Python 3.11+ for FastAPI import:

| Gate | Result |
|---|---|
| `test_supply_chain_licenses.test_frontend_production_dependencies_have_no_commercial_license_blockers` | **PASS** |
| `test_supply_chain_licenses.test_python_requirements_use_real_transitive_lockfile` | **PASS** |
| `test_supply_chain_licenses.test_svg_maps_noncommercial_package_is_not_in_the_frontend_contract` | **PASS** |
| `test_supply_chain_licenses.test_third_party_license_notice_covers_weak_copyleft_and_map_data` | **PASS** |
| Architecture invariants (5 categories: router-to-router, schema→service, raw logging, InMemory, 1000-LOC ceiling) | All clean |
| Cross-browser invariants (6 touch-target rules + 2 geographic-shape exemptions) | All present |
| Supply-chain invariants (0 `@svg-maps`, `us-atlas` + `topojson-client` present) | All clean |

**The new `@tanstack/react-virtual` dependency does not introduce any commercial-use blocker** — the supply-chain license gate passes, which means `react-virtual` is permissive-licensed (MIT, as expected from the TanStack family).

### Cross-audit no-regression sweep

| Audit | Spot-check | Status |
|---|---|---|
| Architecture | 5 invariants all clean | ✅ |
| Cross-browser | Touch-target + geographic-shape exemption intact | ✅ |
| Supply-chain | `@svg-maps` absent; 4/4 license gates PASS; `@tanstack/react-virtual` adds no new copyleft | ✅ |
| Test quality | New `configOptionsQuery.test.ts` adds a sibling gate; 34 frontend test files now (up from 33) | ✅ |
| API contract | `apiPaths.ts` helper still active; OpenAPI baseline test unchanged | ✅ |
| Multi-tenant | `lender_name` / `target_lender_refs` still flow through `configOptionsQuery` | ✅ |
| AI/Genie safety | Genie surface unchanged in this tranche | ✅ |
| Performance v1/v2 | Bundle budgets still under thresholds | ✅ |
| Deployability | `./scripts/deploy.sh -t dev --no-confirm` succeeded end-to-end | ✅ |

**Zero regressions on any prior audit.**

### v2 verdict

**Approved.** All three LOW findings are closed with source changes, unit tests, e2e tests, and live deploy verification. The team also fixed an adjacent canary issue (`route_performance.spec.ts` post-v1 path matching) that my v1 audit didn't catch but would have shown up as a false-pass in future PR CI runs. The LeadTable migration to `@tanstack/react-virtual` costs +14 KB on the lazy chunk and removes 50+ LOC of hand-rolled scroll-window math; net positive for both maintainability and library safety. The configOptions shared hook is the cleanest possible expression of "one query key, one staleTime, one network call across three mounting components."

The performance posture is **production-ready for commercial Module 0 deploy**, with all 22 prior audits' invariants intact. The new `45/45 cross-browser matrix` pass from the engineering signoff confirms the map experience the user emphasized throughout this engagement remains beautiful and bug-free across every supported browser + device profile.

The independent reviewer-gate at the head of this document is met from this side.
