> **Internal implementation artifact. Not approved for public release.**

# Metric-view contract — approval_rate, outreach_rate, delta_vs_prior

Slice13-accuracy follow-up to `docs/validation/dashboards.md` §"Metric-view
follow-ups flagged". Wave 1 shipped executive + segment dashboards whose
widgets reference `approval_rate`, `outreach_rate`, and `delta_vs_prior_*`
on the metric views. Those columns did not exist; the file parsed and the
shape test passed, but live SQL against the warehouse would have errored.

This slice closes the gap by adding two new gold tables, a sync job, a
daily snapshot MERGE, and updated metric views that JOIN them.

## Approach (Option A — UC-native mirror)

Approval and outreach state live in **Lakebase** (`mip_app.approvals`,
`mip_app.action_audit`). The metric views can't LEFT JOIN Lakebase
directly without a foreign UC catalog, which the bundle doesn't declare
today. Rather than build the federated-catalog layer, we mirror Lakebase
into a small, borrower-keyed gold table and JOIN that.

### New gold tables

- **`mip.gold.borrower_lifecycle_state`** (DDL: `sql/ddl/003_gold_tables.sql`
  §7). One row per `borrower_id`. Columns: `approval_status`
  (`pending` / `approved` / `rejected` / `hold`), `outreach_status`
  (`none` / `queued` / `actioned`), `offer_code`, `approved_at`,
  `outreach_at`, `synced_at`. Cluster BY `borrower_id`.

- **`mip.gold.funnel_snapshot_daily`** (DDL §8). One row per
  `(snapshot_date, state, segment_code)` incl. the `_ALL` rollups.
  Carries `addressable_borrowers`, `in_the_money_borrowers`,
  `high_opportunity_borrowers`, `offer_recommended_borrowers`,
  `approved_borrowers`, `actioned_borrowers`, `avg_opportunity_score`,
  `snapshot_at`.

### How they get written

1. **Seed default state** — `sql/transformations/gold_borrower_lifecycle_state.sql`
   does a `CREATE OR REPLACE TABLE ... AS SELECT` that walks every
   borrower in `gold.borrower_360` and writes `pending` / `none`. This
   lets the metric views resolve on a cold deploy before any approval
   has been made.

2. **Sync from Lakebase** — `jobs/sync_lifecycle_state.py` opens a
   psycopg3 connection to `mip-app-state` (same auth path as
   `jobs/lakebase_migrate.py` — workspace identity + short-lived
   credential), reads the latest approval per borrower + whether an
   `OUTREACH_*` event exists in `action_audit`, and `CREATE OR
   REPLACE TABLE`-rewrites `mip.gold.borrower_lifecycle_state` via
   Spark. The rewrite seeds every borrower in `borrower_360` to the
   `pending` / `none` default and then unions in the Lakebase rows —
   so borrowers that have never been reviewed still appear with the
   default, and approved borrowers carry the real timestamp.

3. **Snapshot the funnel** — `sql/transformations/gold_funnel_snapshot_daily.sql`
   does a `MERGE INTO` keyed by `(snapshot_date, state, segment_code)`,
   so re-running the same day is idempotent. Joins `gold.borrower_360`
   against `gold.borrower_lifecycle_state` and aggregates across both
   per-segment and `_ALL` rollups per state and per-`_ALL` state.

### Bundle wiring

A new job `mip_sync_lifecycle_state` chains these three steps
(`seed_default_state → sync_from_lakebase → record_funnel_snapshot`)
with an hourly quartz schedule (`0 15 * ? * * *` America/Chicago).
Canonical declaration in `databricks.yml`; mirror block in
`resources/jobs.yml` following the repo convention used for
`mip_fred_rates_ingest` and `mip_ref_seed`.

## Metric-view changes

### `mip.semantics.segment_performance_metric_view`

Still sourced from `mip.gold.segment_population`. Adds a LEFT JOIN
against `mip.gold.funnel_snapshot_daily` for today's snapshot and a
second LEFT JOIN for the 7-day-prior snapshot. Publishes:

- `approval_rate` — `ROUND(100.0 * approved_borrowers / NULLIF(count, 0), 2)`
- `outreach_rate` — `ROUND(100.0 * actioned_borrowers / NULLIF(count, 0), 2)`
- `delta_vs_prior_count` — WoW addressable delta, 2dp.
- `delta_vs_prior_approved` — WoW approved delta, 2dp.
- `delta_vs_prior_in_the_money` — WoW ITM delta, 2dp.

The existing `delta_vs_prior` column (pre-formatted string from
`gold.segment_population`) stays as-is for the Segment overview table;
the three new `delta_vs_prior_*` numeric columns are for chart widgets
that want typed deltas.

### `mip.semantics.lead_generation_metric_view`

Preserves `gold.lead_population` borrower grain. Segment membership remains
an array in `segment_codes`; segment filters must use
`array_contains(segment_codes, '<segment_code>')`, and aggregate borrower
counts must use `COUNT(DISTINCT clip)`. The view LEFT JOINs
`gold.borrower_lifecycle_state` keyed by `borrower_id`, with `COALESCE`
falling back to `'pending'` / `'none'` so unreviewed borrowers still appear.
Publishes:

- `segment_codes` / `primary_segment` — per-row dimensions. `primary_segment`
  is display-only; it is not a complete membership filter.
- `approval_status` / `outreach_status` — per-row lifecycle dimensions.
- `approval_rate` / `outreach_rate` — state-level window aggregates that keep
  one row per borrower.
- `delta_vs_prior_count` — WoW addressable delta sourced from the
  `_ALL` funnel snapshot by state.

## Widgets unblocked

- **Executive dashboard** counters `kpi_actioned` and the funnel-stages
  chart already read `approval_status`/`actioned` from `borrower_360`.
  They stay wired to `borrower_360`, but once `mip_sync_lifecycle_state`
  runs, the authoritative Lakebase state flows into `funnel_snapshot_daily`
  and segment-level KPIs on the Segment dashboard can source from the
  metric view.
- **Segment dashboard** `table_segment_overview` — the existing
  placeholder text referencing Lakebase authority can be replaced in a
  follow-up slice by surfacing `approval_rate` + `outreach_rate` from
  `segment_performance_metric_view`. This slice publishes the columns;
  the widget JSON edit is intentionally NOT part of this change so the
  dashboards agent's next wave owns the widget wiring.

## What's still a follow-up

1. **Wiring the new columns into dashboard widgets.** The dashboards
   JSON files in `dashboards/` still don't reference `approval_rate`
   / `outreach_rate` / `delta_vs_prior_*` — that's the dashboards
   agent's next move. This slice published the columns; the JSON edit
   is a separate concern.
2. **YoY / QoQ on executive KPIs.** The snapshot table now supports
   it, but the executive dashboard's `ds_funnel_totals` dataset still
   queries `borrower_360` directly. Swapping it to read from
   `funnel_snapshot_daily` with a `snapshot_date = CURRENT_DATE()`
   predicate and a second join for `CURRENT_DATE() - INTERVAL 90 DAYS`
   (QoQ) or `- INTERVAL 365 DAYS` (YoY) is a 10-line dashboard edit.
3. **Federated-catalog swap.** When UC foreign-catalog federation
   over Lakebase Postgres lands, `sql/transformations/gold_borrower_lifecycle_state.sql`
   can become a CTAS against the foreign catalog and the Python
   `sync_lifecycle_state.py` job can retire. Column contract on the
   gold table does not change.
4. **mean_rate_spread_bps / mean_equity_pct as measures on
   `segment_performance_metric_view`.** Still computed by the
   `ds_segment_overview` dataset via a runtime aggregation against
   `borrower_opportunity_metric_view`. Promoting them into
   `segment_population` would simplify the SQL but isn't necessary
   for correctness.

## Validation

- `pytest tests/unit/test_metric_view_ddl_contract.py -q` — contract
  test: view declarations, required columns, formula shape, required
  joins, semantic `COMMENT ON VIEW` lines, and the two new gold DDL
  blocks.
- `pytest tests/unit/test_gold_ddl_contract.py -q` — existing gold-DDL
  contract (covers USING DELTA + CLUSTER BY + PII denylist).
- `ruff check backend tests tools jobs` — style.
- `databricks bundle validate -t ci` — bundle schema check for the
  new `mip_sync_lifecycle_state` job resource.

## Do NOT

- **Do NOT query Lakebase directly from the metric view.** The JOIN
  goes through the gold mirror. Lakebase is authoritative; the
  dashboards read the mirror.
- **Do NOT write back to Lakebase from the sync job.** The job is
  strictly one-directional.
- **Do NOT add filtering to the metric view that would drop borrowers
  without a Lakebase row.** The LEFT JOIN + COALESCE is load-bearing:
  the dashboards need to see every addressable borrower even if nobody
  has reviewed them yet (the denominator of approval_rate must be the
  full population, not just reviewed rows).
