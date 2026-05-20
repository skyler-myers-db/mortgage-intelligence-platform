> **Internal implementation artifact. Not approved for public release.**

# Lakeview dashboards — validation report

The app-owned `/analytics` route is the primary analytics experience for
loan officers, growth leaders, and executives. It renders native React
charts over `/api/v1/analytics/*` so users stay inside the Mortgage
Intelligence Platform and can drill into Lead Queue, Borrower 360,
Segment Intelligence, and Ask Genie without opening Lakeview, SQL
Warehouse, Data Explorer, or other Databricks workspace UI surfaces. App
access itself still follows Databricks Apps sharing/SSO until an external
auth front door exists.

The Lakeview dashboards documented below are retained for Databricks
workspace operators, customer SEs, and admin-side validation. Their
contracts still matter, but they are no longer the required path for an
app user to understand the funnel, geography, economics, segment, or
signal-mix views.

Two dashboard specs replace the prior stubs:
- `dashboards/executive_dashboard.lvdash.json` — CEO / VP Mortgage Lending view.
- `dashboards/segment_dashboard.lvdash.json` — Head of Growth view.

Both are wired into `resources/dashboards.yml` at the bundle level, which
supplies `warehouse_id` via `${var.sql_warehouse_id}`. The JSON files never
pin a warehouse id, never reach below `mip.gold.*` / `mip.semantics.*`, and
contain no emojis (enforced by `tests/unit/test_lakeview_dashboards.py`).

## Executive dashboard

### Page 1 — Funnel

Four counter KPIs across the top (Addressable Borrowers, In-the-Money
Borrowers, Offers Recommended, Outreach Actioned) feed off a single
`ds_funnel_totals` dataset so a refresh updates all four atomically. Below
them, a 12-column layout pairs the opportunity-score distribution with a
horizontal funnel bar (Addressable → In-the-Money → High Opportunity ≥ 75
→ Offer Recommended → Approved → Actioned). **Screenshot for a demo:**
stand on the Funnel tab at 1440×900; the narrative is "5M addressable
becomes 135K in-the-money, then human approval governs the final send."

### Page 2 — Geography

Top row: a renderer-safe horizontal state bar chart colored by mean
opportunity score, next to a bar chart of total AVM value by state. Bottom:
a v2 Lakeview table of the densest in-the-money ZIPs with city, borrower
count, ITM count, mean score, and mean rate spread. **Screenshot:** the
state bar and ZIP table should both be populated; there should be no
"Visualization has no fields selected" panels.

### Page 3 — Economic incentive

Rate-spread histogram (25-bp bins, -100 to 400 bps) over all borrowers,
then a scatter of equity vs rate-spread colored by segment (sampled to
5,000 rows for responsive rendering), and a top-10 borrower table from
`mip.gold.lead_population` with synthetic `display_name` only.
**Screenshot:** point to the upper-right quadrant of the scatter — that
is the ITM cohort, upper-right = high equity AND high rate spread.

## Segment dashboard

### Page 1 — Segment performance

A joined v2 overview table combining
`mip.semantics.segment_performance_metric_view` (counts, mean score, QoQ
delta label, approval rate, outreach rate) with per-segment aggregates
from `mip.semantics.borrower_opportunity_metric_view` (mean rate spread,
mean equity, ITM count). Below: two horizontal bars — segment size and
mean opportunity score by segment. **Screenshot:** show the overview
table first — the six segment rows map 1:1 to the segment cards in the
app's Segment Intelligence route.

### Page 2 — Segment × geography

A v3 Lakeview pivot (rows = state, cols = segment, cell = count) and a
stacked bar of the top 3 segments per state. The pivot answers "which
markets over- or under-index on which segment" without the user writing
SQL. **Screenshot:** the pivot must show fields and totals; if it renders
as no fields selected, the widget spec regressed from the exported v3
multi-cell shape.

### Page 3 — Triggers

A 30-day refresh-date line chart of evidence events by `signal_type` over
`mip.gold.evidence_events`, and a horizontal bar of evidence counts by
`signal_type` colored by `source_product`. Demonstrates operational
freshness and signal mix. **Screenshot:** the bar chart makes the
"permit and listing are blocked until Cotality licenses MLS and Permits"
story visible — those bars are absent from real data.

## Metric-view column gaps (follow-up)

Flagged for a future slice:

- **Year-over-year / quarter-over-quarter deltas on executive KPIs** —
  `delta_vs_prior` exists on `gold.segment_population`, but not on the
  funnel totals. Building a daily snapshot table
  (`mip.gold.funnel_totals_snapshot`) would let the four KPI counters
  render a true YoY chip.
- **`mean_rate_spread_bps` / `mean_equity_pct` measures on
  `segment_performance_metric_view`** — the dashboard joins those in at
  query time against `borrower_opportunity_metric_view`. Promoting them
  to pre-aggregated columns on the segment metric view would simplify
  the SQL and avoid the secondary aggregation at dashboard load.

## Lakeview schema gotchas

- The Lakeview JSON schema moves — widget `spec.version` is intentionally
  2 for counter/table, 3 for pivot/cartesian charts, matching the current
  exported Databricks AI/BI shapes. The unit tests fail if table widgets
  fall back to v1 or pivots lose their v3 multi-cell encoding, because
  both states have rendered as "Visualization has no fields selected" in
  the hosted UI.
- `warehouse_id` is injected at the **dashboard resource** level in
  `resources/dashboards.yml`, not in the `.lvdash.json`. The validator
  test enforces this — any `warehouseId` / `warehouse_id` key in the
  JSON or any 32-hex literal fails the suite.
- `timestamp` is a STRING column on `mip.gold.evidence_events` (Pydantic
  `EvidenceEvent.timestamp: str`), but the trigger chart casts it with
  `TO_DATE(`timestamp`)` so AI/BI receives a temporal axis rather than a
  string axis.
- The source footprint is derived from refreshed gold coverage. No dashboard
  hardcodes a fixed state list; scoping to a state, county, ZIP, or metro is a
  user action on the rendered widget.

## Validation

- `pytest tests/unit/test_lakeview_dashboards.py -q` — shape, references,
  emoji, warehouse-id guards.
- `databricks bundle validate -t ci` — bundle-level schema check.
- Visual: open each dashboard in the Databricks workspace after
  `./scripts/deploy.sh -t dev`; the dashboard bundle resource can take a
  minute to materialize because the Lakeview warehouse warm-starts on first
  query.
