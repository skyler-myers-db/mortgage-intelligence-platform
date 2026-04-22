# Module 0 — Dashboard cold-start & pending-state behaviour

**Who this is for:** the operator doing a fresh deploy, the partner
reviewing dashboards on day one, the on-call engineer looking at a panel
that reads `0` or `pending` and wondering if it is broken.

**What this codifies:** which dashboard widgets depend on state that only
populates over time (snapshot cadence, lender approvals), what those
widgets should display on a brand-new deploy, and how to verify the
backing data cadence without opening a warehouse console.

**Companion docs:**
- [`docs/validation/dashboards.md`](validation/dashboards.md) — widget
  inventory + schema validation for both dashboards.
- [`docs/runbook.md`](runbook.md) — operator reactive/deploy runbook.
  Section 4 (deploy-from-scratch) and section 6 (stale-data recovery)
  both intersect with this doc; see §1 and §2 below when interpreting a
  first-day-of-deploy screenshot.

---

## 1. Cold-start behaviour — widgets that need ≥ 2 snapshots

Three dashboard measures depend on the `mip.gold.funnel_snapshot_daily`
table carrying at least one day-7 predecessor row for the current
scoring refresh:

| Measure | Surface | Backing query / view |
|---|---|---|
| `delta_vs_prior_count` | `mip.semantics.lead_generation_metric_view`; any widget reading this measure | [`sql/metric_views/lead_generation_metric_view.sql`](../sql/metric_views/lead_generation_metric_view.sql) — joins `latest_snapshot` vs `prior_snapshot` where `prior.snapshot_date <= latest.snapshot_date - INTERVAL 7 DAYS` |
| `delta_vs_prior_approved` | `mip.semantics.segment_performance_metric_view`; segment dashboard overview table ("Quarter-over-Quarter Change" column) | [`sql/metric_views/segment_performance_metric_view.sql`](../sql/metric_views/segment_performance_metric_view.sql) |
| `delta_vs_prior_in_the_money` | `mip.semantics.segment_performance_metric_view`; same overview table | same as above |

All three are computed as `(current_value - prior_value) / NULLIF(prior_value, 0) * 100` against the 7-day-prior snapshot.

**First-day-of-deploy expectation:** these measures render `NULL` (or a
displayed `0` / "no prior period" label, depending on the Lakeview chart
type's NULL rendering) until the `mip_sync_lifecycle_state` job has
recorded at least two daily snapshot rows into
`mip.gold.funnel_snapshot_daily`. On a brand-new deploy the very first
invocation of that job writes row #1; the delta widgets first carry a
non-NULL value 24 h later (row #2), after the hourly cron fires the
next day's `record_funnel_snapshot` task.

Concretely, for a deploy that lands today (`CURRENT_DATE()` = today):
- Today: row #1 with `snapshot_date = today` lands. All `delta_vs_prior_*`
  measures resolve to `NULL` because the `prior_snapshot` CTE finds no
  row `<= today - INTERVAL 7 DAYS`.
- Tomorrow: row #2 with `snapshot_date = tomorrow` lands. Measures still
  `NULL` because the window is 7 days, not 1 — the prior-snapshot CTE
  still finds no qualifying row.
- Day 8: the first snapshot's date is now `<= today - INTERVAL 7 DAYS`,
  so the measures resolve to real WoW deltas for the first time.

This is a feature of the WoW window, not a bug. If a partner is reviewing
a freshly-deployed workspace and asks why these columns are blank, cite
this doc and point at `mip.gold.funnel_snapshot_daily` row count + dates.

### 1.1 How operators verify the snapshot cadence

The snapshot table is populated by the `mip_sync_lifecycle_state` job in
`databricks.yml` on an hourly cron (`0 15 * ? * * *` America/Chicago).
The `record_funnel_snapshot` task is the one that MERGEs today's per-
(state, segment) counts into `mip.gold.funnel_snapshot_daily`. Because
the MERGE is keyed on `(snapshot_date, state, segment_code)`, hourly runs
on the same calendar day are idempotent — the same day's row gets
rewritten, not duplicated.

Quick operator probes:

```bash
# 1. Row count + date coverage.
databricks api post /api/2.0/sql/statements --json '{
  "statement": "SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date) FROM mip.gold.funnel_snapshot_daily",
  "warehouse_id": "'"$DATABRICKS_WAREHOUSE_ID"'"
}' | jq

# 2. Most recent refresh timestamp (useful if the cron paused).
databricks api post /api/2.0/sql/statements --json '{
  "statement": "SELECT snapshot_date, MAX(snapshot_at) FROM mip.gold.funnel_snapshot_daily GROUP BY snapshot_date ORDER BY snapshot_date DESC LIMIT 5",
  "warehouse_id": "'"$DATABRICKS_WAREHOUSE_ID"'"
}' | jq

# 3. Confirm the cron job is unpaused + last success.
databricks jobs list --name mip_sync_lifecycle_state
databricks jobs get-run <run_id_from_above> | jq '{state, start_time, end_time}'
```

A healthy deploy shows `COUNT(DISTINCT snapshot_date)` growing by 1 per
calendar day; if it stalls, re-run the job manually:

```bash
databricks bundle run mip_sync_lifecycle_state -t dev
```

---

## 2. Pending-approval state — expected empties on a brand-new deploy

The lifecycle-aware dashboard measures read through
`mip.gold.borrower_lifecycle_state`, a table written by
`jobs/sync_lifecycle_state.py` as a one-directional mirror of Lakebase
(`mip_app.approvals` + outreach events). On a brand-new deploy no operator
has approved or actioned anything yet, so the mirror is seeded with
`approval_status = 'pending'` and `outreach_status = 'none'` for every
borrower in `mip.gold.borrower_360`. This seeding is what
[`sql/transformations/gold_borrower_lifecycle_state.sql`](../sql/transformations/gold_borrower_lifecycle_state.sql)
performs via the `seed_default_state` task.

**First-day-of-deploy expectation:** every downstream measure that
filters on `approval_status = 'approved'` or `outreach_status = 'actioned'`
returns `0`, and any rate measure that divides by
`COUNT(WHERE approval_status = 'approved')` resolves to `0.0` or `NULL`.
Specifically:

| Measure | Default before real approvals land |
|---|---|
| `approval_rate` (per-(state, segment) window in `lead_generation_metric_view`) | `0.0` |
| `outreach_rate` (same) | `0.0` |
| `approved_borrowers` (executive dashboard `ds_funnel_totals` / `ds_funnel_stages`) | `0` |
| `actioned_borrowers` (same) | `0` |
| `approval_rate` / `outreach_rate` (segment-performance metric view — the segment dashboard overview table) | `NULL` (the metric view carries placeholder text acknowledging these are Lakebase-authoritative; see [`docs/validation/dashboards.md`](validation/dashboards.md) §Follow-up) |

These populate as operators use the app. Every click on the "Approve"
button in the Approval Queue writes a row to `mip_app.approvals` in
Lakebase; the next hourly `sync_from_lakebase` run mirrors that row into
`mip.gold.borrower_lifecycle_state`, and the next `record_funnel_snapshot`
run updates the per-(state, segment) counts on
`mip.gold.funnel_snapshot_daily`. The turnaround from a UI click to a
lit-up dashboard cell is bounded by the hourly cron — typically under 1h.

**Kill-switch for the impatient:** if you need to demo a partially-lit
dashboard on day one without waiting for the cron, the same job can be
run on demand:

```bash
databricks bundle run mip_sync_lifecycle_state -t dev
```

That runs all three tasks (`seed_default_state` → `sync_from_lakebase` →
`record_funnel_snapshot`) in sequence, so any approvals already in
Lakebase become visible on the dashboards within the run window.

---

## 3. Summary checklist

When a reviewer asks "why is that cell blank / zero / NULL" on a fresh
deploy, work the checklist:

1. Is the measure one of `delta_vs_prior_count` /
   `delta_vs_prior_approved` / `delta_vs_prior_in_the_money`? → It
   requires ≥ 2 distinct `snapshot_date` values in
   `mip.gold.funnel_snapshot_daily` separated by ≥ 7 days. See §1.
2. Is the measure an approval or outreach count / rate? → It reads
   through `mip.gold.borrower_lifecycle_state`, which is seeded
   `'pending' / 'none'` by default. See §2.
3. Neither? → Treat as a genuine data-freshness or pipeline issue and go
   to [`docs/runbook.md`](runbook.md) §6 (stale real data).

---

*Owner: data-modeler + principal-architect. Review cadence: whenever a
new snapshot-dependent measure or lifecycle-dependent measure is added.*
