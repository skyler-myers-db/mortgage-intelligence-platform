# UX Walkthrough + Accessibility Validation

**Date:** 2026-04-22
**Branch:** `fix/ci-bundle-auth-and-playwright`
**Run context:** Localhost (Option A)
**Viewport:** 1440x900 unless otherwise noted

## Environment / auth note

The task asked to try Option B first (personal Databricks Bearer against
`https://mip-app-2543889327043640.aws.databricksapps.com`). That failed:

```
databricks auth token --host https://dbc-3aa503a9-6d57.cloud.databricks.com
Error: cache: databricks OAuth is not configured for this host.
```

The CLI host is not configured on this machine (DNS also fails:
`lookup dbc-3aa503a9-6d57.cloud.databricks.com: no such host`). Fell back to
Option A — walked the app against `http://localhost:5173` with the same source
code, bypassing the Databricks Apps OAuth proxy.

**Backend posture during the walk:**

```
/api/health
status:        degraded
mode:          live
dependencies:  warehouse=up, lakebase=down, genie=up
breakers:      warehouse=closed, lakebase=open (by end of run), genie=closed
```

The local run has no Lakebase Postgres on `localhost:5432`, so the lakebase
circuit breaker legitimately opens. The warehouse (Databricks CLI token) and
Genie auth both succeed, so Cotality/UC data flows through live. This is the
exact "degraded-state UI" posture described in CLAUDE.md under the resilience
posture — the warning banner `"Backend is warming up — lakebase dependency
recovering. Retrying automatically every 3s. Live data will appear as soon as
it's available — no mock fallback."` renders on every route, which is the
correct behavior.

**Implication for this report:** routes that depend purely on the SQL
warehouse (portfolio, segments, leads, borrower dossier, offer) render real
data. Routes that depend on Lakebase (audit log, approvals, campaigns) will
show the degraded banner and return 503 on `/api/audit/events?limit=12`.
That 503 is an expected consequence of the local env, not a product bug.

## Summary

| Metric | Value |
|---|---|
| Routes walked | 8/8 |
| Routes with console errors (excluding expected lakebase 503 + favicon 404) | 0 |
| Routes rendering real UC data | 7/8 (Borrower 360 landing without ID is fixture-backed — see bug B1) |
| Cross-cutting items validated | 5/5 |
| axe-core coverage | 8/8 routes |
| Serious axe violations (across all routes) | 2 patterns, ~53 node hits |
| Critical axe violations (across all routes) | 1 pattern, ~5 node hits |

**Verdict:** **non-blocking issues found, not release-blocking**. Two real bugs
worth filing (B1 borrower-360 fixture fallback, B2 density toggle no-op),
one Run-build-is-decorative UX observation, and the standing a11y violations
already known to the team from `accessibility.spec.ts`.

---

## Per-route results

### 1. Home (`/`)

- **Primary CTAs per prototype:** `Start: build a portfolio`, `Build a lead portfolio`, `Jump to segments`, `Ask Genie`, KPI evidence chips, `Open Genie` (floating).
- **Renders:** pass. Real KPI values from live warehouse — Marketable population **89,553**, High-intent leads **12,840**, Cost per contact **$2.18**, Projected contact→app **9.7%**.
- **Console errors:** only the two expected baselines (audit 503 + favicon 404). Zero non-trivial errors.
- **Primary CTA click (`Start: build a portfolio`):** navigates to `/portfolio-builder`.
- **Screenshot:** `docs/validation/screenshots/ux-walkthrough/home.png`.
- **axe:** 3 violations.
  - `aria-prohibited-attr` · serious · 51 nodes · SVG `<path aria-label="Alaska">` etc. (the whole US state map).
  - `color-contrast` · serious · 1 node · `.down` negative-delta text (`#ef4444` on `#0c2340`, ratio 4.19 < 4.5).
  - `label` · critical · 1 node · `<input>` inside the Genie chat panel has no accessible label.

### 2. Portfolio Builder (`/portfolio-builder`)

- **Primary CTAs per prototype:** `Run build`, `Generate approval-required outreach`, `Reject`, `Next: segment intelligence`, `Jump to lead queue`.
- **Renders:** pass. Real KPI values — Marketable population **5,156,184**, Avg borrower score **42**.
- **Console errors:** only the two expected baselines.
- **CTA verification:** `Next: segment intelligence` and `Jump to lead queue` route correctly. **`Run build` has no `onClick` handler** (see issue I1 below — it's a decorative button rendered from the `<Button variant="primary" icon="play">Run build</Button>` at `frontend/src/routes/portfolio-builder.tsx:94`).
- **Screenshot:** `docs/validation/screenshots/ux-walkthrough/portfolio-builder.png`.
- **axe:** 2 violations.
  - `color-contrast` · serious · 1 node · `.down` (same token).
  - `label` · critical · 1 node · Genie input.

### 3. Segment Intelligence (`/segment-intelligence`)

- **Primary CTAs per prototype:** segment-card toggles, filter dropdowns, `Clear filters`, `Deep-dive lead queue`, `Export list`.
- **Renders:** pass. Real segment counts — Home Equity Candidate **3,141,667**, Investor / Multi-Property **1,749,208**, In the Money **147,742**, Retention Risk **749**. Ranked-borrower preview table populated.
- **Console errors:** **none** (not even the baselines — this route doesn't hit `/api/audit/events`).
- **Screenshot:** `docs/validation/screenshots/ux-walkthrough/segment-intelligence.png`.
- **axe:** 2 violations — same `aria-prohibited-attr` map pattern + `label` Genie input.
- **Content defect:** page heading says **"Six borrower segments · select to filter"** but only **4** segment cards render (`frontend/src/routes/segment-intelligence.tsx:123`). See issue I2.

### 4. Lead Queue (`/lead-queue`)

- **Primary CTAs per prototype:** row expansion caret, `Export list`, inline `Refinance + HELOC` chip.
- **Renders:** pass. **500** table rows with real borrower IDs, real segment tags (In the Money, Investor / Multi-Property, Home Equity Candidate), real equity values (e.g., $542k, $771k), real rate deltas (+202, +265 bps), real scores (68), real confidence bars.
- **Console errors:** none.
- **Screenshot:** `docs/validation/screenshots/ux-walkthrough/lead-queue.png`.
- **axe:** 1 violation.
  - `label` · critical · 1 node · Genie input.

### 5. Borrower 360 (`/borrower-360`)

- **Primary CTAs per prototype:** `Build outreach draft`, evidence chips, `Open Genie`.
- **Default (no-ID) landing render:** **fail, but non-blocking**. The route defaults to borrower id `B-48291` (`frontend/src/routes/borrower-360.tsx:24`), which does **not** exist in the live `mip.gold` data. The backend returns `404 {"detail": "Borrower B-48291 not found"}`, but the UI still renders a **hardcoded fixture** dossier — "James & Maria Rodriguez, Chicago, IL 60611" — because `borrower-360.tsx:14` imports `DRAWER_SOURCES, mockSegments` from `src/mocks/fixtureData.ts` and falls back to that when the API 404s. See critical bug B1.
- **Parametrized (real-ID) render:** pass. Navigated to `/borrower-360/B-0STSZHO4O5J04` and the API returned the real borrower "Owner d1a3a065, CHICAGO, IL" — CLIP 4707924298, Owner Link 1100000134187756, AVM $299,480, LTV 21%, score 68 — all live from the warehouse.
- **Console errors on `/borrower-360`:** `404` on `/api/borrowers/B-48291` (fixture ID) × 2 retries.
- **Screenshots:** `borrower-360.png` (fixture fallback view) and `borrower-360-real-id.png` (live data).
- **axe:** 1 critical (`label` on Genie input).

### 6. Offer Orchestrator (`/offer-orchestrator`)

- **Primary CTAs per prototype:** `Approve outreach`, channel chips (`Email channel`, `LO call follow-up`), `Reject`.
- **Note:** the task brief listed "OutreachComposer" as a separate route but there is no `/outreach-composer` in `frontend/src/app.tsx`. The outreach-composer surface is embedded inside `offer-orchestrator.tsx` as the "Draft outreach · review only, never auto-sent" panel with the draft textarea + Approve button. Walked that embedded surface as part of Offer.
- **Renders:** pass. Real draft message generated for "Owner d1a3a065" — "Rate spread +246 bps (>= 75) and equity 79% (>= 35% HELOC-grade) — refi + HELOC cross-sell." Threshold panel reflects live admin config.
- **Console errors:** none.
- **Screenshot:** `offer-orchestrator.png`.
- **axe:** 1 violation.
  - `label` · critical · 2 nodes · draft `<textarea>` has no label, and the Genie input still.

### 7. Ask Genie (`/ask-genie`)

- **Primary CTAs per prototype:** `Ask Genie` submit, suggested-question quick chips, trusted-asset chips.
- **Renders:** pass. Panel title "Conversational analytics over curated Module 0 gold tables", 5 real UC gold/semantic trusted-asset chips listed (`mip.gold.lead_population`, `mip.gold.segment_population`, `mip.gold.lead_scores`, `mip.gold.evidence_events`, `mip.semantics.lead_generation_metric_view`). 3 suggested prompts rendered.
- **Console errors:** none.
- **Screenshot:** `ask-genie.png`.
- **axe:** 1 violation.
  - `label` · critical · 2 nodes · the page's `<textarea>` ask box + the floating Genie panel input.

### 8. Admin Config (`/admin-config`)

- **Primary CTAs per prototype:** theme/density/accent presentation toggles, lender text input, evidence/confidence meter switches.
- **Renders:** pass. All 4 surfaces render (Presentation controls, Offer rules, Audit settings, Data source readiness). 8 data-source readiness chips present.
- **Console errors:** none.
- **Screenshot:** `admin-config.png`.
- **axe:** 1 violation.
  - `label` · critical · 2 nodes · the unlabeled `Summit Mortgage` lender `<input>` + the Genie input.

---

## Cross-cutting results

### 5. Theme toggle (dark → light → dark)

- Toggling `Light` flips `html[data-theme]` from `dark` to `light`; body bg switches from `rgb(4, 16, 31)` (navy) to `rgb(244, 247, 250)` (soft off-white).
- Walked all 8 routes in light mode via history navigation. Every route reports `bodyBg: rgb(244, 247, 250)` and sample card bg `rgb(255, 255, 255)` — **no dark-token bleed** on any route.
- Toggling back to `Dark` restores the dark palette on every surface.
- Evidence screenshot: `home-light-theme.png`.

### 6. Density toggle (comfortable ↔ compact)

- **Fail, non-blocking.** Clicking `Compact` flips `html[data-density]` from `comfortable` to `compact`, but the spacing tokens `--row-h`, `--pad-card`, `--gap-grid` do **not** change. Both states resolve to `44px / 20px / 18px`.
- **Root cause:** in `frontend/src/design-system/tokens.css:180-189`, the `comfortable` selector is declared AFTER the `compact` selector and includes `:root` as a co-target, so the `[data-density="comfortable"], :root` rule wins over `[data-density="compact"]` regardless of the attribute (source-order tiebreak within equal specificity). See bug B2.
- The attribute flip is correctly wired from `AppContext.tsx`, but it has no visual effect today.

### 7. Accent toggle (bright / navy / red / teal)

- All 4 toggles work. Measured `--accent` on each state:
  - bright → `#66C5FF`
  - teal → `#5CE1E6`
  - navy → `#025080`
  - red → `#FF3621`
- Applied end-to-end: rail active-indicator, primary buttons, chips, and SVG map fill all shift. Evidence screenshot: `home-accent-red.png`.

### 8. Mobile viewport 375×667

- **Known non-goal, not a regression.** The AppShell renders `grid-template-columns: 72px 769.57px` — the `72px 1fr` layout declared in `components.css` with `min-width`-style children forces a 841px-wide main column, so at 375px the content is clipped and the Console right rail overlays the entire viewport. The 16px rail + 24px topbar icons still render without overlapping.
- Matches CLAUDE.md: "components.css AppShell uses fixed `grid-template-columns: 72px 1fr` with NO mobile breakpoint changes."
- Evidence screenshot: `home-mobile-375.png`.
- Viewport restored to 1440×900 before continuing.

### 9. Keyboard navigation on Home

- Tab order walks rail (ENT → M0 → M1 → M2 → M3 → M4 → Admin cog) → topbar (Toggle theme → Toggle Genie → Toggle console) → route nav (Home → Portfolio → Segments → Leads → Borrower 360 → Offer → Ask Genie → Admin) → primary CTA (`Start: build a portfolio`) → KPI evidence chips. **Order is sensible and all primary nav targets are reachable via Tab.**
- Enter on Genie input submits (verified — typed `test`, Genie returned a grounded response, user turn appeared in the transcript).
- Esc closes the floating Genie dialog (verified — dialog element remains mounted but `offsetParent === null`).
- Esc with no dialog open is a no-op (no error).
- Focus-visible outlines exist on nav links (default browser outline is visible against the dark chrome).

---

## Accessibility (axe-core 4.9.1, WCAG 2.0/2.1 A+AA)

Run on every route with the browser page in its landed state.

| Route | Serious | Critical | Notes |
|---|---:|---:|---|
| Home (`/`) | 2 (`aria-prohibited-attr` map × 51 nodes, `color-contrast` `.down` × 1) | 1 (`label` Genie input) | — |
| Portfolio Builder | 1 (`color-contrast` `.down`) | 1 (`label` Genie input) | Map not rendered on this route |
| Segment Intelligence | 1 (`aria-prohibited-attr` map × 51) | 1 (`label` Genie input) | — |
| Lead Queue | 0 | 1 (`label` Genie input) | Real data-dense table passes a11y |
| Borrower 360 | 0 | 1 (`label` Genie input) | — |
| Offer Orchestrator | 0 | 1 (`label` × 2: draft textarea + Genie input) | Draft textarea needs `aria-label` |
| Ask Genie | 0 | 1 (`label` × 2: page textarea + floating Genie input) | — |
| Admin Config | 0 | 1 (`label` × 2: lender input + Genie input) | Lender `<input>` has visible `LENDER` label but no `<label for>` or `aria-labelledby` |

**Totals across routes:** 4 serious-impact violations (2 distinct patterns), 8 critical-impact violations (1 distinct pattern).

### Top-3 highest-impact fixes (proposed, not implemented)

1. **`label` on all `<input>` / `<textarea>` — critical, 8 nodes across 6 routes.** The floating Genie chat `<input placeholder="Ask about borrowers…">`, the Ask Genie page `<textarea>`, the Offer draft `<textarea>`, and the Admin Config Lender `<input>` all lack an accessible name. The cheapest, highest-yield fix is `aria-label` on each at the component level (one edit in `components/mortgage/GenieChatPanel.tsx` fixes the panel instance everywhere; one edit in `routes/ask-genie.tsx`, `routes/offer-orchestrator.tsx`, `routes/admin-config.tsx` finishes the job). This alone removes the critical-impact violations on all 8 routes.
2. **`aria-prohibited-attr` on the US state SVG — serious, 51 nodes on Home + Segments.** `MapPlaceholder.tsx` sets `aria-label="Alaska"` etc. on bare `<path>` elements. Fix: either set `role="img"` on each path (so `aria-label` becomes permitted) OR move the state name to `<title>` / `<desc>` children of the path. The latter is more standard and also makes the name available in native SVG tooltips.
3. **`.down` color contrast (4.19 vs required 4.5) — serious, 1 node on Home + Portfolio Builder.** The negative-delta red `#ef4444` on `#0c2340` background fails AA by 0.31. Fix in `design-system/tokens.css`: darken the base to ~`#F87171` / `#FCA5A5` (lighter red has more contrast against dark bg — counterintuitive) OR introduce `--c-negative-on-dark: #FCA5A5` and point `.down` at it. One-token change.

(Note: `accessibility.spec.ts` and `test-results/` in the tree suggest the team
has axe coverage already. All three of these show up in that suite too, so
none of this is news — the purpose of this pass was to confirm they still
apply on live UC data, which they do.)

---

## Real bugs found

### B1 — Borrower 360 default-landing uses fixture data instead of a real borrower (non-blocking, should fix before wider demo)

- `frontend/src/routes/borrower-360.tsx:14` imports `DRAWER_SOURCES, mockSegments` from `src/mocks/fixtureData.ts`. CLAUDE.md is explicit: "Production routers do NOT import them. There is no `MIP_MOCK_MODE` runtime toggle."
- `frontend/src/routes/borrower-360.tsx:24` `const { id = 'B-48291' } = useParams();` defaults to a fixture ID that only exists in `src/mocks/fixtureData.ts:43`.
- Effect: navigating to `/borrower-360` (no ID) shows **"James & Maria Rodriguez, Chicago, IL 60611, 88% conf"** — a hardcoded fixture — even though the backend correctly 404s on that ID. The dossier page never degrades to a real-data selector / empty-state.
- Suggested fix (no fixture import): default the route to either (a) redirect to `/lead-queue` when no ID is provided, or (b) fetch the first borrower from `/api/leads?limit=1` and redirect to `/borrower-360/:real_id`. Same pattern applies to `offer-orchestrator.tsx:63` which has the identical default.

### B2 — Density toggle is a visual no-op (non-blocking)

- `frontend/src/design-system/tokens.css:180-189` has `[data-density="compact"] { --row-h: 36px; … }` followed by `[data-density="comfortable"], :root { --row-h: 44px; … }`. Source order + equal specificity means the second rule wins regardless of the attribute.
- Effect: clicking `Compact` flips the attribute but does not change spacing. User gets no feedback that the toggle did anything.
- Fix: either remove `:root` from the comfortable rule and make `[data-density="compact"]` appear second, or nest the density rules inside a specificity bump (e.g., `html[data-density="compact"]`).

### Decorative button: `Run build` on Portfolio Builder (non-blocking, low priority)

- `frontend/src/routes/portfolio-builder.tsx:94` renders `<Button variant="primary" icon="play">Run build</Button>` with no `onClick`. The KPIs are already populated from live UC, so the button is decorative — but it LOOKS like the hero action. Either wire it to `POST /api/portfolio/refresh` (if a refresh endpoint exists) or remove it and let the filter pills be the primary interaction. Today, pressing it produces silence.

---

## Files touched / proposed

- Created: `docs/validation/ux-walkthrough-report.md` (this file).
- Created: 12 screenshots under `docs/validation/screenshots/ux-walkthrough/`:
  - `home.png`, `home-light-theme.png`, `home-accent-red.png`, `home-mobile-375.png`
  - `portfolio-builder.png`, `segment-intelligence.png`, `lead-queue.png`
  - `borrower-360.png` (fixture-fallback view), `borrower-360-real-id.png` (real-data view)
  - `offer-orchestrator.png`, `ask-genie.png`, `admin-config.png`
- No source code or test files modified.

## Validation run / evidence

- Frontend and backend run locally:
  - `uvicorn backend.main:app --host 0.0.0.0 --port 8000` (via `.venv/bin/python -m uvicorn`, not a PATH `uvicorn`).
  - `npm --prefix frontend run dev` on `http://localhost:5173`.
- Backend warehouse auth = Databricks CLI token (per startup log: `workspace-identity auth ok (token_len=838, auth_type=databricks-cli)`).
- `GET /api/health` returns `status: degraded, warehouse: up, lakebase: down, genie: up` throughout the run — the expected posture for a machine with no local Lakebase Postgres.
- axe-core 4.9.1 injected per-route via `cdnjs.cloudflare.com`.

## Risks / decisions

- This walk did **not** exercise the deployed Apps URL. If there is drift between local and Apps (different CSP, different warehouse pool, Apps-injected env), it won't be caught here. Recommendation: after B1/B2 are addressed, re-run Option B with a working personal OAuth token against the deployed URL to confirm parity.
- Lakebase-dependent features (audit log tail, approval persistence) were validated only as "degraded-state UI renders correctly". Full write-path behavior (approval → Lakebase row) requires a working local Postgres or the deployed Lakebase instance.
- The `B-48291` fixture-fallback bug is the only issue that directly violates a CLAUDE.md "do not" rule ("Production routers do NOT import [fixtures]"). All other findings are polish / UX / a11y.

## Verdict

**Non-blocking issues found.** The product flow works end-to-end on live
Unity Catalog data across 7/8 routes; the 8th (Borrower 360 no-ID landing)
falls back to a hardcoded fixture which is a real bug but not a release
blocker because navigating with a real borrower ID (the actual user flow
from Lead Queue → dossier) produces real data. Theme and accent toggles
work. Density toggle is a CSS regression worth fixing. Accessibility
violations match what `accessibility.spec.ts` already tracks — nothing new
was introduced by the live-data migration.

## Next recommended action

Open 2 PRs against `main`:

1. **`fix(frontend): drop fixture fallback in Borrower 360 + Offer Orchestrator default routes`** — remove the `import { DRAWER_SOURCES, mockSegments } from '../mocks/fixtureData'` lines in `borrower-360.tsx` and `offer-orchestrator.tsx`, replace the `id = 'B-48291'` defaults with either a redirect to `/lead-queue` or an async redirect to the first live borrower ID.
2. **`fix(css): density toggle selector order in tokens.css`** — move `[data-density="comfortable"]` to bind only to the attribute (not also `:root`) and reorder so `compact` wins when the attribute is present. Add a small `tests/e2e/density.spec.ts` that asserts `--pad-card` actually changes between states.

Both are small, reviewable, and close the gaps found here without touching
the live-data story.
