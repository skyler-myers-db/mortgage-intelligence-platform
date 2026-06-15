# Module 0 — Live App vs. Prototype Parity Audit

**Audit date:** 2026-05-04 (re-verified and corrected after first pass)
**Live app:** https://mip-app-2543889327043640.aws.databricksapps.com
**Design contract:** `design_files/Module 0 Prototype.html` + `design_files/index.html` + the Anthropic-hosted bundle at `https://api.anthropic.com/v1/design/h/_6tpGknmP6lCNVTY21WIZA`
**Method:** Walked all 8 nav routes in Chrome, diffed live DOM/BEM against the prototype, exercised evidence drawer + Genie + lead-queue expand + map drill, hit `/api/health`, `/api/leads`, `/api/borrowers/{id}`, `/api/segments` directly to separate code bugs from upstream-dependency degradation, and re-read each implicated source file before claiming a gap.

---

## TL;DR (corrected)

The live app is in much better shape than my first-pass audit claimed. After re-verifying every finding against running endpoints + source code, **most "gaps" turned out to be either false positives from the original audit or upstream-dependency degradation surfaced honestly via the prototype's resilience UI**. The genuinely-real remaining work was three items, all now landed in this PR:

1. Segment Intelligence rendered 4 of 6 segments because the gold CTAS aggregated only segments with non-zero borrowers. **Fixed** in `sql/transformations/gold_segment_population.sql` — the CTAS now drives off the canonical 6-segment registry so all 6 always appear. Listing activity now lights up from live MLS rows; `SegmentCard.tsx` keeps the Permit segment pending while the filed Building Permits share is still unavailable.
2. Lead Queue inline preview surfaced only 2 evidence chips. **Fixed** in `LeadTable.tsx` — RowPreview now renders 5–7 chips with conditional rendering for AVM, Voluntary lien, Permit, and MLS based on row data.
3. Keyboard shortcut hints used `<span class="mono">` instead of `<kbd>`. **Fixed** with new `kbd` styling in `frontend/src/design-system/components.css` and updated markup in `LeadTable.tsx`.

---

## What's already in parity (don't break these)

- **AppShell** — left `.rail` (icon nav: M0/M1/M2/M3/M4 + Admin), `.topbar` with breadcrumbs and status pills (`Summit Mortgage`, `sandbox`, warehouse / `offline`), main content, persistent right `.console` rail. Layout matches the prototype.
- **Typography** — `Geist` + `Geist Mono` loaded; `<body>` font-family matches.
- **Theming** — `<html data-theme="dark" data-density="comfortable" data-accent="bright">` matches prototype defaults; theme/density/accent toggles in the Console rail wire through. **Density persistence is already implemented** (`AppContext` lines 85, 103-110 — reads from `mip.density` localStorage and writes back).
- **BEM class vocabulary** for surfaces, KPI cards, segment cards, scores, confidence bars, filters, tables, drawer, Genie, approval, map — names match the prototype.
- **Floating Genie** — `.genie__fab` is rendered on every route; clicking opens the panel with `is-open`, FAB hides via `is-hidden`, prompt input and suggested questions render correctly. The dedicated `/ask-genie` route renders the deeper "Trusted assets / Suggested questions" surfaces.
- **Evidence Drawer** — clicking an evidence chip opens `.drawer.is-open` with real UC lineage:
  - Source: `cotality.public_records.deed_and_mortgage` (Delta Share)
  - Source: `cotality.liens.voluntary_lien` (Delta Share)
  - Entity: `entity.property_clip` (mastered via CLIP)
  - Entity: `entity.owner_link` (mastered via Owner Link)
  - Semantic: `metrics.borrower_universe` (UC metric view)
  - Raw signals with counts (Owner-occupied SFR 1.84M, Open first lien 1.72M, After lender filter 89,553)
  - Plus Fresh/Aging/Stale freshness chips — a nice addition that isn't in the prototype.
- **Lead Queue expand-in-place** — `.tbl__expand` / `.tbl__expand-inner` / `is-expanded` work; expanded preview shows Customer 360 mini-card, "Why Now" prose, and Next-Best-Offer panel with score badge and "Open Borrower 360" CTA.
- **Borrower 360 dossier** — `/api/borrowers/B-102FL7THC6Q3L` returns 200 with the full dossier; `/borrower-360/B-102FL7THC6Q3L` renders Customer 360, **Trigger timeline** (with full `.trig__when / __what / __why` BEM), Why We Recommend This, Next-best-offer, and **15 evidence chips** including Voluntary Lien, AVM, Market Rates, Owner Link, Property, Mortgage Domain.
- **Map drill-down** — 51 `.map-region` paths with `role="button"`, `tabindex="0"`, aria-labels; density bins `.lvl-1`..`.lvl-4` present and computed via quantile bucketer; `.map-tip` BEM (`__name`, `__kpis`, `__kpi`, `__kpi-label`, `__kpi-value`, `__seg`, `__seg-label`, `__seg-value`, `__row`) implemented in `USChoroplethMap.tsx` line 1462; `onMouseEnter`/`onMouseMove`/`onMouseLeave` handlers wired in `renderStateLevel` (line 806) and county/zip levels.
- **Audit log BEM** — `AgentActivityLog.tsx` lines 149-161 use `audit__time/__ico/__body/__what/__who` correctly; the row markup just isn't visible in the live DOM right now because Lakebase is down so the feed renders an honest error-state message instead of rows.
- **Resilience UI** — top yellow `degraded-banner` ("Reconnecting to operational database. Live data will resume automatically. This page refreshes every 3 seconds."), KPI shimmer placeholders, "Audit feed is briefly unavailable. This page will retry on the next refresh; live dependency state is shown below" microcopy. Exactly what `CLAUDE.md` mandates for degraded-state UX. The topbar `offline` pill switches to reflect real connectivity.
- **Console rail** — Theme (Dark/Light), Accent (4 swatches), Density (Comfortable/Compact, persisted), Lender field (Summit Mortgage), Show Evidence Chips toggle, Show Confidence Meters toggle, Open Genie link.
- **KPI evidence chips** route to real UC objects: `cotality.public_records`, `UC function · fn_in_the_money`, `UC function · fn_next_best_offer`.
- **Footer wordmark** — `EntradaWordmark` renders correctly across all routes (the original audit's "mis-render" was a false positive — it renders consistently on Borrower 360, Admin, Home).

---

## Real fixes landed in this PR

### Fix 1 (was P0-2): Segment Intelligence renders all 6 segments

**Symptom:** `/api/segments` returned 4 rows (`equity`, `investor`, `itm`, `retention`) and the page header read "4 borrower segments · select to filter". The prototype data (`design_files/Module 0 Prototype.html` lines 1546–1551) defines six segments: `itm`, `listed`, `permit`, `investor`, `equity`, `retention`. Listed for Sale and Permit Activity were missing.

**Root cause:** `sql/transformations/gold_segment_population.sql` aggregated segment rows from `gold.borrower_360.segment_codes` via `LATERAL VIEW EXPLODE`. At the time of this audit, the `listed` and `permit` predicates were intentionally blocked-FALSE in `gold.borrower_360` while upstream shares were pending, so no row in the source carried those codes. MLS/listing activity has since landed through `mip.silver.listing_activity`; filed Building Permits remain the pending predicate. The CTAS therefore correctly returned 4 rows at the time — the data was honest, but the segment registry contract was implicit instead of explicit.

**Fix:** Rewrote the gold CTAS to drive off the canonical 6-segment `meta` VALUES table and LEFT JOIN exploded counts onto it. Every refresh now emits one row per (segment_code, state) plus the `_ALL` national row for all 6 codes. Segments with zero matching borrowers come through as `count=0`, `avg_score=0`, `delta_vs_prior='+0%'`. The API contract becomes "you will always see 6 segments"; data availability is reflected in the count, not in row presence.

**FE companion:** `frontend/src/components/mortgage/SegmentCard.tsx` now detects zero-row upstream dependencies and renders a "Pending source" variant only for genuinely unavailable feeds. Listing activity now clears through live MLS rows; Permit Activity keeps "Awaiting Cotality Permits share" microcopy while the filed Building Permits feed remains unavailable. CSS `seg-card--pending` block added in `frontend/src/design-system/components.css`. Honest UX: the user sees all 6 segments and any remaining upstream data dependency is called out inline.

**Ordering:** `DatabricksSegmentRepository._LIST_SQL` still uses `ORDER BY count DESC` (existing behavior — leaves the SQL surface unchanged). The repository now applies a stable canonical re-sort after the fetch using `_CANONICAL_ORDER = (itm, listed, permit, investor, equity, retention)` so pending segments (count=0) don't get buried at the end of the list. Unknown future segment codes are appended after the canonical six. This was caught in independent code review (reviewer flagged the ORDER BY DESC interaction) and fixed before merge.

**Risk:** Low. The CTAS is rebuilt fresh on every refresh; the prior table format isn't mutated. Existing rows for the 4 active segments are unchanged. The two new rows have count=0 so they don't shift any aggregate KPI. The repository-side re-sort is a pure post-process on the in-memory list, no SQL change required.

### Fix 2 (was P1-5): Lead Queue inline preview shows 5–7 evidence chips

**Symptom:** Expanded row preview surfaced only `Rate + equity ruleset` and `Next-best-offer model`. Borrower 360 already renders 15 chips per dossier; the inline preview understated the platform's evidence depth.

**Fix:** `RowPreview` in `frontend/src/components/mortgage/LeadTable.tsx` now renders a fixed core set (Rate + equity ruleset, Next-best-offer model, CLIP · Owner Link) plus data-driven chips for AVM (when `equity_estimate > 0`), Voluntary lien (when `current_lien_balance > 0`), Recent permit (when `has_permit === true`), MLS listing (when `listed_for_sale === true`). Each routes into the existing `EvidenceDrawer` via `DRAWER_SOURCES`. The chips are conditional so a row with no permit doesn't show a fake "Permit" chip — honest by construction.

### Fix 3 (was P2-kbd): Keyboard hint chips

**Symptom:** Lead Queue subtitle reads "Keyboard: A approve, R reject the expanded row." with `<span class="mono">A</span>` markup — visually flat, reads as inline code.

**Fix:** Added a `kbd` element style block to `frontend/src/design-system/components.css` (subtle keycap chip with a 2px bottom border and tiny shadow); replaced the spans with `<kbd>A</kbd>` / `<kbd>R</kbd>` in `LeadTable.tsx`. Reads as a tappable key. 9-line CSS addition + 2 markup changes; safe.

---

## False positives from the original audit (corrected here)

The original audit overstated the gap. Each of the items below was claimed as a defect but turns out to be either already-implemented or an artifact of the upstream Lakebase outage that's currently in effect. Keeping the list explicit so the next reviewer doesn't re-flag them:

- **Borrower 360 returns 404.** I claimed `/borrower-360/B-1B2FL7THO9G2L` 404'd. Wrong — I misread `B-102FL7THC6Q3L` off a low-res screenshot. The endpoint works fine; verified with `/api/health` (warehouse: up) and `/api/borrowers/B-102FL7THC6Q3L` returning 200.
- **Borrower ID format drift.** Production IDs are 13-char alphanumeric (`B-102FL7THC6Q3L`); earlier five-digit spec copy is outdated. Not a contract bug. Recommend updating `CLAUDE.md` Naming Rules section in a doc-only PR.
- **Trigger Timeline missing.** Already implemented at `frontend/src/components/mortgage/TriggerTimeline.tsx`, rendered on Borrower 360 with all `.trig__when/__what/__why` BEM classes. Only invisible on the original audit pass because I never reached Borrower 360 successfully (see point 1).
- **Map drill-down read-only / no `.map-tip`.** `.map-tip` is implemented at `USChoroplethMap.tsx:1462` with the full prototype BEM; `.lvl-1..lvl-4` density bins computed via `buildQuantileBucketer`; `onMouseEnter` handlers wired at `renderStateLevel:806`, county level:1036, zip level:1206/1277. My JS-dispatched synthetic events didn't fire React's handlers, but real cursor hover does.
- **Audit log uses generic markup.** `AgentActivityLog.tsx:149-161` already emits `audit__time / __ico / __body / __what / __who`. The classes only fail to appear in the live DOM right now because Lakebase is down and the feed renders an honest error message instead of rows. As soon as Lakebase reconnects, those classes return.
- **Empty-state pages can't degrade.** `/borrower-360` and `/offer-orchestrator` empty-state CTAs ("Choose a borrower to inspect" → "Browse lead queue") work end-to-end. The original audit conflated this with the false 404.
- **Footer brand wordmark mis-rendering.** Renders correctly on every route checked. False positive.
- **Drawer/Console z-index overlap.** Re-checked — drawer renders above the Console at the right z-index. Visual perception in the first audit was that the Console was hiding the drawer's right edge, but the drawer scrim covers the full viewport including under the Console. Not a bug.
- **`score--low` and `conf--low` never rendered.** Fixture issue, not a code bug. The current Cotality population doesn't surface low-scoring borrowers in any meaningful volume (the in-the-money + equity filters skim the cream); the design tokens are defined and would render correctly given low-score data.
- **`chip--success` / `chip--warning` unused.** Defined in `components.css` lines 268–273 and used in `LeadTable.tsx` for approved/rejected status. Was scoped wrong in the audit search.
- **No FRED evidence chip on home KPI.** Lower-priority polish; not a bug. FRED ingestion exists; surfacing it as a chip is a nice-to-have, not a parity gap.
- **Density toggle persistence.** Already implemented (`AppContext.tsx:85` and `:103-110` write through to `localStorage.mip.density`). No change needed.

---

## Validation chain

After the three real fixes:

```bash
ruff check backend tests tools
npm --prefix frontend run lint
npm --prefix frontend run build         # tsc -b && vite build (catches type errors + builds prod bundle)
pytest -q
npm --prefix frontend run test           # vitest
```

This shell does not have the `databricks` CLI, so the bundle deploy (`databricks bundle deploy -t dev` then `databricks bundle run refresh_silver -t dev`) must be run from a workstation with credentials. Once deployed, the Segment Intelligence page should render 6 cards; Listed for Sale lights up from MLS/listing rows, while Permit Activity remains in the "Pending source" state until the filed Building Permits share lands in Unity Catalog.

## Recommended follow-up tickets (not in this PR)

- Update `CLAUDE.md` Naming Rules to reflect production borrower-ID format (`B-XXXXXXXXXXXXX`, 13-char alphanumeric).
- With the MLS Delta Share landed, keep `listed_for_sale` evidence-backed by `mip.silver.listing_activity`. When the filed Building Permits share lands, drop the remaining `has_permit = FALSE` block in `gold.borrower_360` and the Permit Activity pending state will auto-clear.
- Optional: page-aware Genie suggestions (currently the GeniePanel suggestions are constant across routes; passing `useLocation().pathname` into the suggestion array would let `/borrower-360/:id` pages suggest "What other properties does this owner own?").
- Optional: a `seed_borrower` admin page that lists 5 known-good borrower IDs so a presenter can fall back to a guaranteed dossier even during a snapshot-skew refresh window.
