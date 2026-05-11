> **Internal implementation artifact. Not approved for public release.**

# Lakeview dashboards — validation report

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
them is a horizontal bar chart of the six funnel stages (Addressable →
In-the-Money → High Opportunity ≥ 75 → Offer Recommended → Approved →
Actioned) sourced from `mip.gold.borrower_360`, and an opportunity-score
distribution line chart at 5-point buckets. **Screenshot for a demo:**
stand on the Funnel tab at 1440×900; the narrative is "5M addressable
becomes 12K in-the-money becomes 1.2K approved — this is why targeting
matters."

### Page 2 — Geography

Top row: a symbol-map keyed by `state` coloring each current coverage
state by mean opportunity score, sized by borrower count, next to a bar
chart of total AVM value by state. Bottom:
a 20-row table of the densest in-the-money ZIPs with city, borrower count,
ITM count, mean score, and mean rate spread. **Screenshot:** wait for the
map to paint all current coverage states before capturing — the story is "Chicago is
the anchor, Denver is the runner-up" (`docs/data-contract-module0.md §10`).

### Page 3 — Economic incentive

Rate-spread histogram (25-bp bins, -100 to 400 bps) over all borrowers,
then a scatter of equity vs rate-spread colored by segment (sampled to
5,000 rows for responsive rendering), and a top-10 borrower table from
`mip.gold.lead_population` with synthetic `display_name` only.
**Screenshot:** point to the upper-right quadrant of the scatter — that
is the ITM cohort, upper-right = high equity AND high rate spread.

## Segment dashboard

### Page 1 — Segment performance

A joined overview table combining `mip.semantics.segment_performance_metric_view`
(counts, mean score, QoQ delta label) with per-segment aggregates from
`mip.semantics.borrower_opportunity_metric_view` (mean rate spread, mean
equity, ITM count). Below: two bar charts — segment size and mean
opportunity score by segment. The table explicitly notes approval rate
and outreach rate are Lakebase-authoritative and not joined here (see
Follow-up section). **Screenshot:** show the overview table first — the
six segment rows map 1:1 to the segment cards in the app's Segment
Intelligence route.

### Page 2 — Segment × geography

A pivot (rows = state, cols = segment, cell = count) and a stacked bar
of the top 3 segments per state. The pivot answers "which markets over-
or under-index on which segment" without the user writing SQL.
**Screenshot:** zoom the pivot so current coverage states and all segment
columns are visible in one frame.

### Page 3 — Triggers

A 30-day line chart of evidence events per day colored by `signal_type`
over `mip.gold.evidence_events`, and a bar chart of evidence counts by
`signal_type` colored by `source_product`. Demonstrates operational
freshness and signal mix. **Screenshot:** the bar chart makes the
"permit and listing are blocked until Cotality licenses MLS and
Permits" story visible — those bars are absent from real data.

## Metric-view column gaps (follow-up)

Flagged for a future slice:

- **`approval_rate` and `outreach_rate` per segment** — not on
  `mip.semantics.segment_performance_metric_view`. These are authoritative
  in Lakebase (`mip_app.approvals`, `mip_app.outreach`). The segment
  overview table includes placeholder text noting this; a federated
  Lakebase join or a nightly sync table into `mip.gold.segment_approvals`
  would let the dashboard show those rates inline.
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
  2 for counter / 1 for table/pivot / 3 for cartesian charts, which
  matches the versions Databricks currently renders from a bundle. If a
  future CLI release rejects them, the error surfaces at
  `databricks bundle validate -t ci` and the test harness here will
  still pass (we only guard shape, not semantic version).
- `warehouse_id` is injected at the **dashboard resource** level in
  `resources/dashboards.yml`, not in the `.lvdash.json`. The validator
  test enforces this — any `warehouseId` / `warehouse_id` key in the
  JSON or any 32-hex literal fails the suite.
- `timestamp` is a STRING column on `mip.gold.evidence_events` (Pydantic
  `EvidenceEvent.timestamp: str`), so the 30-day trigger query uses
  `SUBSTRING(`timestamp`, 1, 10)` and `DATE_FORMAT(CURRENT_DATE() -
  INTERVAL 30 DAYS, 'yyyy-MM-dd')`. Do not cast to DATE without updating
  the gold DDL first.
- The source footprint is derived from refreshed gold coverage. No dashboard
  hardcodes a fixed state list; scoping to a state, county, ZIP, or metro is a
  user action on the rendered widget.

## Validation

- `pytest tests/unit/test_lakeview_dashboards.py -q` — shape, references,
  emoji, warehouse-id guards.
- `databricks bundle validate -t ci` — bundle-level schema check.
- Visual: open each dashboard in the Databricks workspace after
  `databricks bundle deploy -t dev`; the bundle resource takes a minute
  to materialize because the Lakeview warehouse warm-starts on first
  query.
