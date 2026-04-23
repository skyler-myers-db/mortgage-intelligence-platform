# Interactive-element audit — `/` and `/portfolio-builder`

Date: 2026-04-23
Branch: `slice13-accuracy-validation`
Scope: [frontend/src/routes/home.tsx](../../frontend/src/routes/home.tsx) and
[frontend/src/routes/portfolio-builder.tsx](../../frontend/src/routes/portfolio-builder.tsx) plus the components they render
(`KpiCard`, `USChoroplethMap`, `AgentActivityLog`, `ApprovalBanner`, `FilterSelect`, `Reveal`, `EntradaWordmark`, `EntradaMark`).

Legend:
- **LIVE** — hits a real backend endpoint and renders real data
- **STUB** — present but hardcoded value or placeholder response
- **NAV** — pure navigation (no data call)
- **BROKEN** — present but doesn't work
- **DEAD** — visible UI that intentionally does nothing

## 1. Full inventory

| Route | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| `/` | "Build a portfolio" CTA (hero right) | Link to `/portfolio-builder` | NAV | [home.tsx:70-74](../../frontend/src/routes/home.tsx#L70-L74) — `<Link to="/portfolio-builder">` |
| `/` | "Marketable population" KPI value | Render count from `/api/portfolio/preview` | LIVE | [home.tsx:94](../../frontend/src/routes/home.tsx#L94) → [databricks_repo.py:283](../../backend/services/repositories/databricks_repo.py#L283) `COUNT(*) FROM mip.gold.borrower_360` |
| `/` | "Marketable population" KPI sparkline/delta | Render 7-day trend | LIVE | [home.tsx:95-97](../../frontend/src/routes/home.tsx#L95-L97) → [databricks_repo.py:230-240](../../backend/services/repositories/databricks_repo.py#L230) `mip.gold.funnel_snapshot_daily` |
| `/` | "Marketable population" evidence chip | Opens drawer (`population` source) | STUB | [drawerSources.ts:17-36](../../frontend/src/lib/drawerSources.ts#L17-L36) — all lineage counts (`142M`, `98M`, `1.84M`, `1.72M`, `89,553`) and `updatedAt: '2026-04-20 06:12 UTC'` are hardcoded literals |
| `/` | "High-intent leads" KPI value | Render `in_the_money` count | LIVE | [home.tsx:102](../../frontend/src/routes/home.tsx#L102) → [databricks_repo.py:220](../../backend/services/repositories/databricks_repo.py#L220) `SUM(CASE WHEN in_the_money ...)` |
| `/` | "High-intent leads" sparkline/delta | Render 7-day trend | LIVE | [home.tsx:103-105](../../frontend/src/routes/home.tsx#L103-L105) → funnel_snapshot_daily |
| `/` | "High-intent leads" evidence chip | Opens drawer (`itm` source) | STUB | [drawerSources.ts:37-56](../../frontend/src/lib/drawerSources.ts#L37-L56) — all signal values hardcoded (`6.250%`, `7.125%`, `+87.5 bps`, `56%`) |
| `/` | "Cost per contact (est.)" KPI value | Render dollar amount | STUB (em-dash) | [home.tsx:110](../../frontend/src/routes/home.tsx#L110); backend returns `None` at [databricks_repo.py:289](../../backend/services/repositories/databricks_repo.py#L289); rendered as `—` by [KpiCard.tsx:56](../../frontend/src/components/mortgage/KpiCard.tsx#L56). **Deliberately not populated — flagged in scope doc as main-agent-owned.** |
| `/` | "Cost per contact" evidence chip | Opens drawer (`config` source) | STUB | [drawerSources.ts:91-97](../../frontend/src/lib/drawerSources.ts#L91-L97) — lineage chip is just `lender.campaign_config` with no real config object behind it |
| `/` | "Projected contact → app" KPI value | Render percent | STUB (em-dash) | [home.tsx:116](../../frontend/src/routes/home.tsx#L116); backend returns `None` at [databricks_repo.py:288](../../backend/services/repositories/databricks_repo.py#L288). **Main-agent-owned.** |
| `/` | "Projected contact → app" evidence chip | Opens drawer (`nbo` source) | STUB | [drawerSources.ts:57-73](../../frontend/src/lib/drawerSources.ts#L57-L73) — model name/AUROC/brier/SHAP features all hardcoded |
| `/` | Approval-queue banner | Display count from preview | LIVE | [home.tsx:123-138](../../frontend/src/routes/home.tsx#L123-L138) — reads `preview.high_intent_leads` |
| `/` | Choropleth state click (IL/CA/TX) | Drill to county level | LIVE (map) / STUB (counts) | [USChoroplethMap.tsx:466-500](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L466-L500) — real `@svg-maps/usa` polygons + real county TopoJSON. But `STATE_FACTS` (counts/scores/top-segment) at [USChoroplethMap.tsx:98-152](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L98-L152) is hardcoded; TODO at line 80 confirms. |
| `/` | Choropleth non-supported state click | Select state (no drill) | LIVE (selection) / STUB (count) | Selection updates; hover tooltip reads from hardcoded `STATE_FACTS` |
| `/` | Choropleth county click (IL/CA/TX) | Select county; on Cook, drill to ZIP | LIVE (polygons) / STUB (per-county facts) | [USChoroplethMap.tsx:581-611](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L581-L611); `COUNTY_FACTS` at [lines 31-57](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L31-L57) hardcoded |
| `/` | Choropleth ZIP click | Navigate to `/borrower-360/B-48291` (60611 only) | NAV (one working ZIP) / DEAD (other ZIPs) | [USChoroplethMap.tsx:678-683](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L678-L683) — only `60611`, `60647`, `60613` have `borrowerId`; `60614`, `60610`, `60657` set selection but don't navigate |
| `/` | Breadcrumb "US" button | Reset to state level | LIVE | [USChoroplethMap.tsx:718-729](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L718-L729) |
| `/` | Breadcrumb state button (county view) | Back to county level | LIVE | [USChoroplethMap.tsx:733-749](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L733-L749) |
| `/` | Breadcrumb "Chicago Metro" (ZIP view) | Static pill, not interactive | DEAD | [USChoroplethMap.tsx:755-758](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L755-L758) — `span`, no click handler |
| `/` | County-load retry text | Re-trigger TopoJSON fetch | LIVE | [USChoroplethMap.tsx:541-555](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L541-L555) |
| `/` | Map hover tooltip | Show name / count / score / top segment | STUB | Data source is `STATE_FACTS` / `COUNTY_FACTS` (hardcoded) |
| `/` | "Source: CLIP + MMA" footer row in tooltip | Label | STUB | [USChoroplethMap.tsx:836-838](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L836-L838) — hardcoded literal |
| `/` | Map legend "Borrowers in selection" count | Sum from level | STUB | [USChoroplethMap.tsx:413-428](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L413-L428) sums hardcoded fact tables |
| `/` | Agent-activity-log rows | List audit events | LIVE | [AgentActivityLog.tsx:105](../../frontend/src/components/mortgage/AgentActivityLog.tsx#L105) — `GET /api/audit/events?limit=12` |
| `/` | Warehouse/Genie/probe telemetry strip | Poll `/api/health` every 30s | LIVE | [AgentActivityLog.tsx:122-155](../../frontend/src/components/mortgage/AgentActivityLog.tsx#L122-L155) |
| `/` | "Exported nightly for compliance review" footer | Label | STUB (copy) | [AgentActivityLog.tsx:240](../../frontend/src/components/mortgage/AgentActivityLog.tsx#L240) — hardcoded string |
| `/` | M1 "Pipeline Optimization" card | None (planned module) | DEAD | [home.tsx:16-21](../../frontend/src/routes/home.tsx#L16-L21), rendered at [167-176](../../frontend/src/routes/home.tsx#L167-L176); no `onClick`, no `href` |
| `/` | M2 "LO Workbench" card | None | DEAD | same |
| `/` | M3 "Underwriting Copilot" card | None | DEAD | same |
| `/` | M4 "Risk & Retention" card | None | DEAD | same |
| `/` | "Build a lead portfolio" button (footer) | Link to `/portfolio-builder` | NAV | [home.tsx:180-182](../../frontend/src/routes/home.tsx#L180-L182) |
| `/` | "Jump to segments" button | Link to `/segment-intelligence` | NAV | [home.tsx:183-185](../../frontend/src/routes/home.tsx#L183-L185) |
| `/` | "Ask Genie" button | Navigate to `/ask-genie` via `window.location.href` | NAV | [home.tsx:186-188](../../frontend/src/routes/home.tsx#L186-L188) — works, but full-page reload rather than `<Link>` |
| `/` | EntradaWordmark signature | Decorative brand mark | DEAD (decorative) | [home.tsx:191-195](../../frontend/src/routes/home.tsx#L191-L195); `aria-hidden` |
| `/portfolio-builder` | GEO filter dropdown | Change geography criterion | BROKEN | [portfolio-builder.tsx:25](../../frontend/src/routes/portfolio-builder.tsx#L25) — options hardcoded; backend `_ = request` at [databricks_repo.py:276](../../backend/services/repositories/databricks_repo.py#L276) discards criteria; filter selection has no effect on KPI output |
| `/portfolio-builder` | OCCUPANCY filter dropdown | Change occupancy criterion | BROKEN | Same — criteria ignored server-side |
| `/portfolio-builder` | LIEN STATUS filter dropdown | Change lien criterion | BROKEN | Same |
| `/portfolio-builder` | RELATIONSHIP filter dropdown | Change customer-relationship criterion | BROKEN | Same |
| `/portfolio-builder` | PRODUCT filter dropdown | Change product criterion | BROKEN | Same |
| `/portfolio-builder` | EQUITY filter dropdown | Change min-equity criterion | BROKEN | Same |
| `/portfolio-builder` | "Run build" button | Re-fetch `/api/portfolio/preview` | LIVE (fetch) / BROKEN (effect) | [portfolio-builder.tsx:129-137](../../frontend/src/routes/portfolio-builder.tsx#L129-L137) — fires real POST; server returns identical payload each time because criteria are discarded |
| `/portfolio-builder` | "Marketable population" KPI | Render preview count | LIVE | Same as home |
| `/portfolio-builder` | "Avg. borrower score" KPI | Render avg score | LIVE | [portfolio-builder.tsx:167](../../frontend/src/routes/portfolio-builder.tsx#L167) → [databricks_repo.py:221](../../backend/services/repositories/databricks_repo.py#L221) `CAST(ROUND(AVG(opportunity_score)))` |
| `/portfolio-builder` | "Cost per contact" KPI | em-dash | STUB (em-dash) | Backend returns `None`; main-agent-owned |
| `/portfolio-builder` | "Projected contact → app" KPI | em-dash | STUB (em-dash) | Backend returns `None`; main-agent-owned |
| `/portfolio-builder` | All four KPI evidence chips | Open drawer | STUB | `DRAWER_SOURCES` values hardcoded (see `/` rows above) |
| `/portfolio-builder` | ApprovalBanner "Send to loan officers" button | Intended: send to loan officers | BROKEN | [portfolio-builder.tsx:190-198](../../frontend/src/routes/portfolio-builder.tsx#L190-L198) — no `onApprove` / `onReject` props passed; `ApprovalBanner` calls `undefined` on click ([ApprovalBanner.tsx:39-40](../../frontend/src/components/mortgage/ApprovalBanner.tsx#L39-L40)) |
| `/portfolio-builder` | ApprovalBanner "Reject" button | Intended: reject queue | BROKEN | Same — no handler |
| `/portfolio-builder` | "Next: segments" CTA | Link to `/segment-intelligence` | NAV | [portfolio-builder.tsx:201-204](../../frontend/src/routes/portfolio-builder.tsx#L201-L204) |
| `/portfolio-builder` | "Jump to lead queue" CTA | Link to `/lead-queue` | NAV | [portfolio-builder.tsx:205](../../frontend/src/routes/portfolio-builder.tsx#L205) |

Total rows: 48.

## 2. Fixed inline

No fixes were applied. Every candidate fix violated scope rules:

- **Filter dropdowns hardcoded options**: [portfolio-builder.tsx:25-31](../../frontend/src/routes/portfolio-builder.tsx#L25-L31) contains strings like `'≥ 15%'`, `'Chicago MSA'`, `'Open 1st lien'`. The backend's [`/api/config/options`](../../backend/api/config.py) does publish `geographies`, `occupancy`, `lien_status`, etc., but the vocabularies don't match the UI strings (e.g. frontend says `"Open 1st lien"`, backend says `"Open first lien"`; frontend geographies are MSA/region labels, backend options are `State / County / ZIP` triples). Binding the UI to `/api/config/options` today would change the visible copy and would still be decorative until criteria push-down lands server-side — it crosses into copy/schema changes and belongs to the main agent.
- **ApprovalBanner missing handlers** on `/portfolio-builder`: there is no wired target. `POST /api/outreach/approve` expects a `borrower_id`; the banner is summarizing an aggregate population count at this stage of the funnel, not a single borrower. Wiring it to a plausible endpoint would require a new bulk-approve route / Lakebase schema — flagged below for the main agent.
- **M1–M4 planned-module cards**: intentionally non-interactive per product posture. DEAD is the correct classification, not a fix target.
- **"Ask Genie" button uses `window.location.href`** instead of `<Link to="/ask-genie">` ([home.tsx:186](../../frontend/src/routes/home.tsx#L186)) — forces a full page reload and loses SPA state. Trivial one-line replacement, but it's a styling / navigation-pattern choice explicitly out of scope per audit rules ("No copy/styling changes").

## 3. Needs main-agent attention

- **Filter dropdowns have no server-side effect.** Selecting "Texas" or "≥ 40%" on `/portfolio-builder` and clicking "Run build" fires a real `POST /api/portfolio/preview` with the criteria in the body, but [`DatabricksPortfolioRepository.preview`](../../backend/services/repositories/databricks_repo.py) opens with `_ = request` and then executes a criteria-free `COUNT(*)` on `mip.gold.borrower_360`. Every filter combination returns identical numbers. This is the single largest source of the user's "filters don't work" complaint. Fix scope: push `PortfolioCriteria` into a parameterised `WHERE` clause against `mip.gold.borrower_360` (state, occupancy flag, lien status, customer-relationship flag, min-equity predicate, product focus). Likely needs a new covering index or Delta liquid-cluster spec on the gold table so the filtered read stays under the existing p95 budget.
- **Filter option vocabulary is not sourced from `/api/config/options`.** Even once criteria push-down lands, the UI will be out of sync with the backend dictionary. Wire `FILTER_GROUPS` to a `useEffect` that GETs `/api/config/options` on mount, and align the vocabularies (either change UI strings or widen the backend dictionary). Straightforward fetch work but crosses the copy-change line.
- **ApprovalBanner on `/portfolio-builder` has no handlers.** Today the "Send to loan officers" / "Reject" buttons are silent. Decide whether this surface should: (a) create a Lakebase `campaign` row from the current filter set and enqueue all `high_intent_leads` for LO review (new `POST /api/portfolio/enqueue`), (b) just deep-link to `/lead-queue` and become a NAV, or (c) be removed. Either (a) or (b) is valid; (a) is product-aligned but needs a new endpoint + schema.
- **`DRAWER_SOURCES` lineage/signal values are hardcoded literals**, not derived from UC or MLflow: `142M rows`, `98M rows`, `89,553`, `6.250%`, `7.125%`, `+87.5 bps`, `AUROC 0.81 · brier 0.09`, `Approved 2026-03-02`, `updatedAt: '2026-04-20 06:12 UTC'` all sit in [drawerSources.ts](../../frontend/src/lib/drawerSources.ts). Per the file's own docstring, this is "UI contract metadata, not fake borrower data" — but the row counts and model metrics are presented to a buyer as factual. A separate endpoint (e.g. `GET /api/evidence/sources`) that reports true table row counts + the registered MLflow model version + real refresh timestamps would remove the ambiguity. Larger change; new router + service.
- **Choropleth `STATE_FACTS` / `COUNTY_FACTS` are hardcoded synthetic rollups.** [USChoroplethMap.tsx:31-152](../../frontend/src/components/mortgage/USChoroplethMap.tsx#L31-L152) ships per-state counts (1.86M IL, 900 CA, ...) and per-county counts (620 Cook, 720 LA, ...) as Python-free TypeScript literals. The in-file TODO at line 30 / line 79 acknowledges this and pledges a backend rollup. Until it lands, hover tooltips and the "Borrowers in selection" legend read like live data but are a fixture. Fix scope: add `GET /api/geography/rollup?level=state|county&state=IL` backed by a new gold view `mip.gold.geography_rollup` aggregated from `borrower_360`.
- **ZIP drill-down only navigates for 3 of 6 ZIPs.** `60611`, `60647`, `60613` have `borrowerId` set; `60614`, `60610`, `60657` are visually identical but clicking them selects without navigating. Fix: either populate `borrowerId` for all six (probably means adding more sample borrowers to the fixture/dossier), or dim the three non-navigable ZIPs to signal DEAD state.
- **Choropleth "Chicago Metro" breadcrumb pill** at ZIP level is a static span. If the intent is "click to go back to the county level", it needs a `<button>` with an `onClick` that sets `level` back to `'county'`. Small scope but a navigation-semantics decision.
- **Agent-activity-log footer "Exported nightly for compliance review"** is a literal string. If there's a real nightly export job, the strip could read its most-recent run timestamp from an endpoint; if not, remove the line.

## 4. Honest fake-data inventory

Every hardcoded literal on these two routes that reads to a buyer as live data. File : line : literal.

- `frontend/src/lib/drawerSources.ts:24` — `meta: 'Delta Share · 142M rows'` (population source row count)
- `frontend/src/lib/drawerSources.ts:25` — `meta: 'Delta Share · 98M rows'` (voluntary_lien count)
- `frontend/src/lib/drawerSources.ts:31-34` — signal values `'1.84M'`, `'1.72M'`, `'89,553'` (after-lender-filter borrower counts)
- `frontend/src/lib/drawerSources.ts:35, 55, 72, 89` — `updatedAt: '2026-04-20 06:12 UTC'` (four separate `updatedAt` fields, all identical literal)
- `frontend/src/lib/drawerSources.ts:50-53` — rate / spread / equity signal values (`'6.250%'`, `'7.125%'`, `'+87.5 bps'`, `'56%'`)
- `frontend/src/lib/drawerSources.ts:64` — `meta: 'AUROC 0.81 · brier 0.09'` (NBO model performance)
- `frontend/src/lib/drawerSources.ts:65` — `meta: 'Approved 2026-03-02'` (governance approval date)
- `frontend/src/lib/drawerSources.ts:68-70` — SHAP features (`rate_spread_bps`, `avm_equity_pct`, `prior_heloc_flag`)
- `frontend/src/lib/drawerSources.ts:80` — `meta: '4.8M active records'` (permits)
- `frontend/src/lib/drawerSources.ts:85-87` — permit sample values (`'Kitchen remodel'`, `'$48,000'`, `'2026-03-17'`)
- `frontend/src/components/mortgage/USChoroplethMap.tsx:31-57` — `COUNTY_FACTS` map: 21 counties with literal `count` + `avgScore` + `lvl`
- `frontend/src/components/mortgage/USChoroplethMap.tsx:98-152` — `STATE_FACTS` map: 49 states with literal `count` + `avgScore` + `lvl` + `topSegment`
- `frontend/src/components/mortgage/USChoroplethMap.tsx:183-189` — `IL_COUNTIES` fallback polygons with literal counts (Cook 620, DuPage 310, etc.)
- `frontend/src/components/mortgage/USChoroplethMap.tsx:196-203` — `CHI_ZIPS` with literal counts (94/72/58/68/46/52) and avg scores, plus hardcoded `borrowerId: 'B-48291'` / `'B-48294'` / `'B-48295'`
- `frontend/src/components/mortgage/USChoroplethMap.tsx:836-838` — map-tip Source row literal `'CLIP + MMA'`
- `frontend/src/components/mortgage/AgentActivityLog.tsx:240` — footer literal `'Exported nightly for compliance review'`
- `frontend/src/routes/home.tsx:16-21` — `FUTURE_MODULES` array: M1–M4 titles and descriptions (acceptable as product roadmap copy; flagged for completeness)

Note on KPIs: `cost_per_contact` and `projected_contact_to_app` are correctly returned as `null` from the backend ([databricks_repo.py:288-289](../../backend/services/repositories/databricks_repo.py#L288-L289)), and the `KpiCard` renders `—` for `null`. These are **not** hardcoded in the frontend — the scope doc notes the main agent is handling the backend-side fix.

## 5. Build verification

Not run. The audit made no source changes, so there is nothing for `npm --prefix frontend run build` to verify. If a future pass applies any of the Section 3 fixes, re-run the build before landing.
