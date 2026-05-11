> **Internal implementation artifact. Not approved for public release.**

# Interactive-Element Audit — Segment Intelligence & Lead Queue

Date: 2026-04-23
Branch: slice13-accuracy-validation
Scope: [frontend/src/routes/segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx), [frontend/src/routes/lead-queue.tsx](../../frontend/src/routes/lead-queue.tsx) and their rendered components: `SegmentCard`, `LeadTable`, `USChoroplethMap`, `FilterSelect`, `ScoreBadge`, `ConfidenceMeter`, `EvidenceChip`, `Sparkline`.

The user's complaint was that "a lot of numbers still look like fake demo placeholders to me and tons of buttons and filters don't even work." This audit categorizes every interactive element and flags every hardcoded literal that masquerades as data.

---

## 1. Element × wiring table

| Route | Element | Action | Wired? | Evidence |
|---|---|---|---|---|
| segment-intelligence | `Clear filters` button | Reset `activeSegs` + `chipFilters` | LIVE | [segment-intelligence.tsx:168](../../frontend/src/routes/segment-intelligence.tsx) — `onClick={clearAll}` resets local state |
| segment-intelligence | `SegmentCard` (6 cards) | Toggle segment in `activeSegs`, filters `LeadTable` | LIVE | [segment-intelligence.tsx:200](../../frontend/src/routes/segment-intelligence.tsx) — `toggleSeg(s.code)`; LeadTable filtering at line 128 via `segment_codes.some` |
| segment-intelligence | `SegmentCard` count / delta / avg | Display segment KPIs | LIVE | Values come from `/api/segments` → `mip.gold.segment_population.count/delta_vs_prior/avg_score` — see [databricks_repo.py:333–363](../../backend/services/repositories/databricks_repo.py) |
| segment-intelligence | `FilterSelect` LOCATION | Filter table by state | **LIVE** | Uses refreshed footprint options and narrows leads by current coverage state. |
| segment-intelligence | `FilterSelect` DEMOGRAPHICS | Filter by occupancy | **STUB (display-only)** | [segment-intelligence.tsx:140–142](../../frontend/src/routes/segment-intelligence.tsx) — TODO comment; filter change only dims table opacity via `hasSoftFilter`, does not narrow rows. |
| segment-intelligence | `FilterSelect` LIEN | Filter by lien status | **STUB** | Same — dims only, no predicate on `LeadSummary` (no lien field). |
| segment-intelligence | `FilterSelect` OWNER LINK | Filter by owner link | **STUB** | Same — dims only. |
| segment-intelligence | `FilterSelect` PURCHASE INTENT | Filter by purchase signal | **STUB** | Same — dims only. |
| segment-intelligence | `FilterSelect` CASH-OUT | Filter by equity floor | LIVE (client-side) | [segment-intelligence.tsx:136–139](../../frontend/src/routes/segment-intelligence.tsx) — real predicate via `EQUITY_FLOOR_USD`. |
| segment-intelligence | `Deep-dive lead queue` link | Navigate to `/lead-queue` | NAV | [segment-intelligence.tsx:260](../../frontend/src/routes/segment-intelligence.tsx) — `<Link to="/lead-queue">` (no segment param) |
| segment-intelligence + lead-queue | `LeadTable` header "select all" checkbox | Select/deselect all eligible rows | LIVE | [LeadTable.tsx:211–228](../../frontend/src/components/mortgage/LeadTable.tsx) — `toggleSelectAll` targets `eligibleIds` (skips already-approved) |
| segment-intelligence + lead-queue | `LeadTable` row checkbox | Toggle row in `selectedIds` | LIVE | [LeadTable.tsx:187–194](../../frontend/src/components/mortgage/LeadTable.tsx) — `toggleSelect`; disabled when `!isEligible` |
| segment-intelligence + lead-queue | `LeadTable` row body | Expand/collapse RowPreview | LIVE | [LeadTable.tsx:375](../../frontend/src/components/mortgage/LeadTable.tsx) — `onClick={() => setExpanded(...)}` |
| segment-intelligence + lead-queue | `LeadTable` inline `Approve` button | `POST /api/outreach/approve` | LIVE | [LeadTable.tsx:145–172](../../frontend/src/components/mortgage/LeadTable.tsx) — `api.approve(borrowerId)` → real backend |
| segment-intelligence + lead-queue | `LeadTable` inline reject button | Set approval=rejected (local) | **STUB (local-only)** | [LeadTable.tsx:174–180](../../frontend/src/components/mortgage/LeadTable.tsx) — `rejectLead` only calls `setApproval(id, 'rejected')`. No `/api/outreach/reject` call → no audit row. |
| segment-intelligence + lead-queue | `Approve N leads` bulk button | Loop `api.approve` per row | LIVE | [LeadTable.tsx:238–262](../../frontend/src/components/mortgage/LeadTable.tsx) — real endpoint, chunks of 3 |
| segment-intelligence + lead-queue | `Clear selection` bulk button | Clear `selectedIds` | LIVE | [LeadTable.tsx:196–198](../../frontend/src/components/mortgage/LeadTable.tsx) |
| segment-intelligence + lead-queue | Bulk toast | Show ok/fail count | LIVE | [LeadTable.tsx:519–531](../../frontend/src/components/mortgage/LeadTable.tsx) |
| segment-intelligence + lead-queue | `Export list` button | Download CSV | **LIVE (fixed inline)** | Previously had no `onClick`. Now calls `exportCsv` producing a client-side CSV of the current `leads` prop. |
| segment-intelligence + lead-queue | `PII suppressed` chip | Display-only | DEAD | [LeadTable.tsx:331](../../frontend/src/components/mortgage/LeadTable.tsx) — intentionally static status indicator |
| segment-intelligence + lead-queue | RowPreview `Open Borrower 360` link | Navigate | NAV | [LeadTable.tsx:104](../../frontend/src/components/mortgage/LeadTable.tsx) — real route |
| segment-intelligence + lead-queue | RowPreview segment chips | Display segment codes | LIVE | From `lead.segment_codes` |
| segment-intelligence + lead-queue | RowPreview `EvidenceChip` × 2 (Why-now) | Open EvidenceDrawer | LIVE | [LeadTable.tsx:88–89](../../frontend/src/components/mortgage/LeadTable.tsx) — `DRAWER_SOURCES.itm/nbo` |
| segment-intelligence + lead-queue | LeadTable `nbo_v3` chip | Open EvidenceDrawer | LIVE | [LeadTable.tsx:437](../../frontend/src/components/mortgage/LeadTable.tsx) |
| segment-intelligence | `USChoroplethMap` state path | Drill to county | **LIVE (superseded finding)** | Current map coverage is derived from backend geography rollups and available county geometry. |
| segment-intelligence | Map state hover tooltip (count/avgScore/topSegment) | Display | **LIVE (superseded finding)** | Current tooltip values come from backend rollups, not local geography fixtures. |
| segment-intelligence | Map county path | Drill to ZIP | **LIVE (superseded finding)** | Current county and ZIP levels are driven by backend rollups for the selected geography. |
| segment-intelligence | Map county hover tooltip | Display | **LIVE (superseded finding)** | Current county tooltip values come from backend rollups, not local geography fixtures. |
| segment-intelligence | Map ZIP path | Navigate to `/borrower-360/<id>` when a sample borrower exists | **LIVE** | ZIP tiles use backend rollup context and only expose borrower navigation when the API returns a sample borrower id. |
| segment-intelligence | Map breadcrumb "US" | Reset to state level | LIVE | [USChoroplethMap.tsx:718–729](../../frontend/src/components/mortgage/USChoroplethMap.tsx) |
| segment-intelligence | Map breadcrumb state name | Reset to county level | LIVE | [USChoroplethMap.tsx:734–749](../../frontend/src/components/mortgage/USChoroplethMap.tsx) |
| segment-intelligence | Map legend | Display | DEAD | [USChoroplethMap.tsx:781–805](../../frontend/src/components/mortgage/USChoroplethMap.tsx) — legend is a color-ramp reference |
| segment-intelligence | Map `segmentFilter` dim | Dim non-matching geographies | LIVE (decorative) | Respects `activeSegs` using backend rollup top-segment metadata. |
| **segment-intelligence** | **Map click filters LeadTable?** | — | **BROKEN** | No callback from map to parent. Map is decorative — clicking a state does NOT narrow the LeadTable below. LOCATION dropdown is the only way to filter geographically. |
| lead-queue | `segment = foo` URL chip | Display-only | LIVE | [lead-queue.tsx:53](../../frontend/src/routes/lead-queue.tsx) — `segment` URL param is passed to `api.leads(segment)` which hits `_LIST_BY_SEGMENT_SQL` WHERE `array_contains(segment_codes, :segment)` — real predicate. |

---

## 2. Fixed inline

Three changes applied in [frontend/src/components/mortgage/LeadTable.tsx](../../frontend/src/components/mortgage/LeadTable.tsx):

### 2.1 `RowPreview` CLIP cell — use real backend field

Before (line 58): `v={\`clip_${lead.borrower_id.toLowerCase().replace('-', '')}\`}`
After: `v={clipValue}` where `clipValue = lead.clip && lead.clip.length > 0 ? lead.clip : fallback`

`lead.clip` is the real Cotality CLIP projected by the backend per the 2026-04-22 schema addition ([backend/schemas/lead.py:34](../../backend/schemas/lead.py), [frontend/src/types.ts LeadSummary.clip](../../frontend/src/types.ts)). The old code synthesized a fake `clip_b12345` string from `borrower_id` even when the real CLIP was available.

### 2.2 Row body CLIP subtext — same fix

Line ~394 in the Borrower column subtext: now prefers `lead.clip` with the derived string only as fallback for pre-2026-04-22 cached payloads.

### 2.3 `Export list` button — wire `onClick`

Previously the button had no handler. Added `exportCsv` callback: downloads a CSV of the currently-visible `leads` (columns: `borrower_id, clip, city, state, zip, segments, equity_estimate, rate_spread_bps, opportunity_score, confidence, recommended_offer, approval_status`). Filename is `mip-leads-YYYY-MM-DD.csv`. Honors PII-suppression by construction since we only emit fields already on `LeadSummary`. Disabled when `leads.length === 0`.

Validation:
- `npm --prefix frontend run lint` — clean.
- `npm --prefix frontend run build` — green (tsc + vite).

---

## 3. Needs main-agent attention (flagged, not fixed)

### 3.1 BROKEN — Map does not filter LeadTable

The prototype's geography drill-down is supposed to narrow the ranked-borrower table. Currently `USChoroplethMap` is a self-contained drill (state → county → ZIP → borrower deep-link) but never reports its selection back to `SegmentIntelligence`. The LOCATION dropdown is the only path that narrows rows.

Fix shape: add `onGeographyChange?: (states: string[]) => void` prop to `USChoroplethMap`; in `segment-intelligence.tsx`, use it to set a `mapStates` filter merged into the `filtered` memo. Clicking "US" breadcrumb should clear.

### 3.2 STUB — 4 of 6 secondary filters are display-only

DEMOGRAPHICS, LIEN, OWNER LINK, PURCHASE INTENT narrow nothing. The page dims the table via `hasSoftFilter` to signal "intent" but this is theater. Options:
- (A) Extend `gold.lead_population` with `occupancy`, `lien_status`, `owner_link_type`, `purchase_intent_flag` columns; project via `LeadSummary`; add real predicates. This is backend work — out of scope for inline.
- (B) Remove the 4 dropdowns until backed. Shrinks product surface on the hero page.

Recommend (A) but note a migration is required.

### 3.3 STUB — `FilterSelect.options` hardcoded; `/api/config/options` exists

`LOCATION_TO_STATES`, demographics/lien/owner-link/purchase/cashout option lists are hardcoded in the route. [backend/api/config.py](../../backend/api/config.py) already ships `/api/config/options` with curated lists (geographies, occupancy, lien_status, lender_relationships, products, equity_thresholds). Either the frontend should fetch these, or the endpoint should be deprecated. Right now both exist and drift.

### 3.4 SUPERSEDED — Map facts now come from backend rollups

Current state/county/ZIP facts come from backend geography rollups and current source coverage. The earlier local-map fixture finding is closed.

### 3.5 SUPERSEDED — ZIP drill uses backend rollup context

Current ZIP tiles are generated from backend rollups. Borrower navigation is
available only when the rollup payload includes a sample borrower id; otherwise
the tile remains an aggregate geography view.

### 3.6 STUB — Reject is local-only

`rejectLead` in [LeadTable.tsx:174–180](../../frontend/src/components/mortgage/LeadTable.tsx) updates `AppContext.approvals` but never hits the backend — no audit row is written for rejections. Governance §4 says approve AND reject should both land in the Lakebase audit table. Needs a `POST /api/outreach/reject` (or an `approve` body extension with `decision: 'reject'`).

### 3.7 SUPERSEDED — County drill follows available coverage

Current county drill behavior follows available backend rollups and geometry
for the selected state rather than a fixed local state allowlist.

---

## 4. Honest fake-data inventory

Every literal on these two routes that currently masquerades as data.

| File | Line | Literal | Masquerades as |
|---|---|---|---|
| [segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx) | 27–35 | `LOCATION_TO_STATES` metros | "Live" location filter taxonomy |
| [segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx) | 40–45 | `EQUITY_FLOOR_USD` thresholds | Equity-filter floors |
| [segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx) | 219 | `['All', 'Owner-occupied', 'Investor', 'Second home']` | Real occupancy taxonomy |
| [segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx) | 225 | `['Any', 'Open 1st lien', 'Open HELOC', 'Free & clear']` | Real lien taxonomy |
| [segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx) | 231 | `['All', 'Single property', 'Multi-property']` | Real owner-link taxonomy |
| [segment-intelligence.tsx](../../frontend/src/routes/segment-intelligence.tsx) | 237 | `['All', 'Listed for sale', 'Permit activity']` | Real purchase-intent taxonomy |
| [USChoroplethMap.tsx](../../frontend/src/components/mortgage/USChoroplethMap.tsx) | current | Backend geography rollup payload | Per-state/per-county/per-ZIP borrower density and top segment |
| [USChoroplethMap.tsx](../../frontend/src/components/mortgage/USChoroplethMap.tsx) | current | Segment label map | Map-highlight to segment-name join |
| [USChoroplethMap.tsx](../../frontend/src/components/mortgage/USChoroplethMap.tsx) | current | Current county geometry + backend rollups | County drill rendering |
| [USChoroplethMap.tsx](../../frontend/src/components/mortgage/USChoroplethMap.tsx) | current | Backend ZIP rollup payload | ZIP tiles and optional sample borrower link |
| [USChoroplethMap.tsx](../../frontend/src/components/mortgage/USChoroplethMap.tsx) | current | Rollup source metadata | Lineage claim on map-tip |
| [USChoroplethMap.tsx](../../frontend/src/components/mortgage/USChoroplethMap.tsx) | 862, 872 | `cx="833" cy="300"` Illinois beacon | Chicago pixel position (decoration, acceptable) |

Counts: 6 literal-taxonomy lists, 3 hardcoded geography-fact dicts (one per drill level), 1 fake source-claim chip. The two tables that ARE real — `segments` (6 rows from `mip.gold.segment_population`) and `leads` (up to 500 rows from `mip.gold.lead_population`) — drive the rest of the page correctly.

The RowPreview CLIP fallback that previously synthesized `clip_b12345` strings is fixed (see §2.1/2.2); real CLIPs from the `LeadSummary.clip` field are now used.

---

## 5. Validation run

```
npm --prefix frontend run lint   # clean
npm --prefix frontend run build  # green (tsc -b && vite build)
```

No commit created. Changes staged as working-tree edits in `frontend/src/components/mortgage/LeadTable.tsx`.
