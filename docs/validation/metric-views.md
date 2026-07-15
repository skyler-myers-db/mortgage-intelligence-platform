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
`mip_app.call_dispositions`). Although the bundle registers the app-state
database catalog, the metric views deliberately avoid a runtime cross-plane
join and its grant/latency coupling. They join a small borrower-keyed gold
mirror instead.

### New gold tables

- **`mip.gold.borrower_lifecycle_state`** (DDL: `sql/ddl/003_gold_tables.sql`
  §7). Sparse, at most one row per `borrower_id` that has a durable Lakebase
  approval or disposition. Columns: `approval_status`
  (`pending` / `approved` / `rejected` / `hold`), `outreach_status`
  (`none` / `queued` / `actioned`), `offer_code`, `approved_at`,
  `outreach_at`, `synced_at`, `refreshed_at`. `refreshed_at` is the
  Lakebase mirror refresh boundary for this lifecycle snapshot; it is not the
  scoring gold refresh boundary. Cluster BY `borrower_id`.

- **`mip.gold.funnel_snapshot_daily`** (DDL §8). One row per
  `(snapshot_date, state, segment_code)` incl. the `_ALL` rollups.
  Carries `addressable_borrowers`, `in_the_money_borrowers`,
  `high_opportunity_borrowers`, `offer_recommended_borrowers`,
  `approved_borrowers`, `actioned_borrowers`, `avg_opportunity_score`,
  `snapshot_at`.

### How they get written

1. **Primary app sync** — after Lakebase commits an accepted approval or
   rejection, `backend/services/job_trigger.py` schedules
   `backend/services/lifecycle_sync.py` on FastAPI `BackgroundTasks`. The
   service reads the durable current Lakebase lifecycle rows and applies a
   changed-row Delta `MERGE` through the existing SQL warehouse. It does not
   `INSERT OVERWRITE`, seed defaults, scan the lifecycle target for a count,
   or rebuild the population-wide funnel snapshot on each click. Consumers
   already use `LEFT JOIN` + `COALESCE('pending'/'none')` for borrowers with no
   lifecycle row.

2. **Durable repair sync** — when the cheap warehouse path fails, the app
   submits `mip_sync_lifecycle_state`. `jobs/sync_lifecycle_state.py` opens a
   psycopg3 connection to `mip-app-state` (same auth path as
   `jobs/lakebase_migrate.py` — workspace identity + short-lived
   credential), reads the latest approval/disposition per borrower, and runs
   the same canonical changed-row `MERGE` via Spark. Databricks stores the run
   state, queues concurrent recovery submissions, and retries the task twice.
   The durable Lakebase source means a dropped process-local background task
   can be repaired by any later app, Admin Data operations, deploy, or job run.

3. **Snapshot the funnel** — `sql/transformations/gold_funnel_snapshot_daily.sql`
   does a `MERGE INTO` keyed by `(snapshot_date, state, segment_code)`,
   so re-running the same day is idempotent. Joins `gold.borrower_360`
   against `gold.borrower_lifecycle_state` and aggregates across both
   per-segment and `_ALL` rollups per state and per-`_ALL` state. This full
   population aggregation runs at deploy/Admin/durable-repair boundaries, not
   for every successful app hook.

### Bundle wiring

`mip_sync_lifecycle_state` chains two steps
(`sync_from_lakebase → record_funnel_snapshot`). It has one queued run at a
time and two task retries. The optional 04:00 America/Chicago schedule is
declared but ships paused in every target; the normal freshness driver is the
warehouse-first app hook. `databricks.yml` is the canonical declaration.

**Upgrade note:** a workspace upgraded from the former seed/overwrite design
can retain historical synthetic `pending` / `none` rows until an intentional
one-time cleanup is approved. They are semantically harmless because the
consumer defaults are identical, and the changed-row predicate leaves them
untouched. Do not hide a multi-million-row Delta delete inside an app hook;
validate and run that storage cleanup as an explicit operator migration.

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
- **Segment dashboard** `table_segment_overview` — now surfaces
  `approval_rate` + `outreach_rate` from
  `segment_performance_metric_view` alongside segment size and economics.

## What's still a follow-up

1. **YoY / QoQ on executive KPIs.** The snapshot table now supports
   it, but the executive dashboard's `ds_funnel_totals` dataset still
   queries `borrower_360` directly. Swapping it to read from
   `funnel_snapshot_daily` with a `snapshot_date = CURRENT_DATE()`
   predicate and a second join for `CURRENT_DATE() - INTERVAL 90 DAYS`
   (QoQ) or `- INTERVAL 365 DAYS` (YoY) is a 10-line dashboard edit.
2. **Federated-catalog swap.** If the deployed Lakebase catalog becomes a
   supported direct metric-view source with the required grants and latency,
   the Python repair job can retire. The sparse gold column contract does not
   change.
3. **mean_rate_spread_bps / mean_equity_pct as measures on
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
- **Do NOT restore default-row seeding or table replacement.** A single
  lifecycle event must remain a changed-row `MERGE`, never a rewrite of
  `gold.borrower_360`'s full population.
