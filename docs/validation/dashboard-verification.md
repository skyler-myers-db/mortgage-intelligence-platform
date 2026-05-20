> **Internal implementation artifact. Not approved for public release.**

# Lakeview dashboards — widget verification report

The native app route `/analytics` is the end-user dashboard experience.
This Lakeview verification report exists for operator-side Databricks
dashboards only: it proves the companion AI/BI assets still resolve, but
release readiness for app users also requires the `/analytics` route and
`/api/v1/analytics/*` endpoints to pass their own tests and browser
walkthrough.

This doc pairs every widget shipped in `dashboards/*.lvdash.json` with
an automated verdict from
[`tests/integration/test_dashboard_widgets_resolve.py`](../../tests/integration/test_dashboard_widgets_resolve.py)
and a manual checklist for the five things the automated probe cannot
evaluate: colors, axis labels, legend placement, tooltip content, and
tab-through order in the Lakeview UI.

**Companion docs:**
- [`docs/dashboards.md`](../dashboards.md) — cold-start / pending-state
  behavior (why some widgets are allowed to show blank on day one).
- [`docs/validation/dashboards.md`](dashboards.md) — the shape-level
  validator report (emoji / warehouse-id / schema-prefix guards).

## Legend

| Verdict | Meaning |
|---|---|
| 🟢 rendered | Dataset query succeeds, every encoded column is in the result schema, row-shape rules pass. |
| 🟡 cold-start-zero-by-design | Dataset cells may be NULL or the row count may be low on a brand-new deploy; documented in `docs/dashboards.md`. Not a failure. |
| 🔴 broken | Automated test asserts against the current live warehouse; block release. |

The verdicts below are the *expected* states against a healthy deploy;
the authoritative live status is the output of the nightly run (see
"Automated verification" at the bottom of this doc).

---

## `executive_dashboard.lvdash.json`

### Page — Funnel

| Widget | Type | Dataset | Purpose | Verdict |
|---|---|---|---|---|
| `kpi_addressable` | counter | `ds_funnel_totals` | Addressable Borrowers — unique CLIPs in current Cotality gold coverage with an open lien. | 🟢 |
| `kpi_itm` | counter | `ds_funnel_totals` | In-the-Money Borrowers — rate spread ≥ 75 bps and equity ≥ 15 percent. | 🟢 |
| `kpi_offers` | counter | `ds_funnel_totals` | Offers Recommended — next-best-offer other than nurture. | 🟢 |
| `kpi_actioned` | counter | `ds_funnel_totals` | Outreach Actioned — approved + sent by a human loan officer. Authoritative in Lakebase; mirrored to gold on refresh. | 🟡 starts at 0 on cold deploy (no approvals yet). Populates as operators use the Approval Queue. See `docs/dashboards.md` §2. |
| `chart_funnel_stages` | bar | `ds_funnel_stages` | Horizontal funnel — Addressable → In-the-Money → High Opportunity (≥ 75) → Offer Recommended → Approved → Actioned. | 🟢 |
| `chart_score_distribution` | line | `ds_score_distribution` | Count of borrowers at each 5-point opportunity-score bucket. | 🟢 |

### Page — Geography

| Widget | Type | Dataset | Purpose | Verdict |
|---|---|---|---|---|
| `chart_state_opportunity` | bar | `ds_state_opportunity` | Current refreshed coverage states by borrower count, colored by mean opportunity score. | 🟢 |
| `chart_state_avm_value` | bar | `ds_state_avm_value` | Total AVM value per state. | 🟢 |
| `table_top_zips` | table | `ds_top_zips_itm` | 20 densest in-the-money ZIPs with city, borrower count, ITM count, mean score, mean rate spread. | 🟢 |

### Page — Economic incentive

| Widget | Type | Dataset | Purpose | Verdict |
|---|---|---|---|---|
| `chart_rate_spread_hist` | bar | `ds_rate_spread_hist` | 25-bp bins from -100 to 400 bps across all borrowers. | 🟢 |
| `chart_equity_vs_spread` | scatter | `ds_equity_vs_spread` | Equity% vs rate spread, colored by segment (≤ 5 000 rows). | 🟢 |
| `table_top_borrowers` | table | `ds_top_borrowers` | Top 10 borrowers by opportunity score — synthetic `display_name` only. | 🟢 |

---

## `segment_dashboard.lvdash.json`

### Page — Segment performance

| Widget | Type | Dataset | Purpose | Verdict |
|---|---|---|---|---|
| `table_segment_overview` | table | `ds_segment_overview` | Per-segment size + economics + QoQ delta label. Approval / outreach rates are explicitly Lakebase-authoritative and not joined here. | 🟡 the `delta_vs_prior_label` / `Quarter-over-Quarter Change` column renders "n/a" on deploys < 8 days old. See `docs/dashboards.md` §1. Row count is non-zero; the cell is NULL by design. |
| `chart_segment_counts` | bar | `ds_segment_counts` | Borrower count per segment across the footprint. | 🟢 |
| `chart_segment_avg_score` | bar | `ds_segment_avg_score` | Mean opportunity score per segment. | 🟢 |

### Page — Segment by geography

| Widget | Type | Dataset | Purpose | Verdict |
|---|---|---|---|---|
| `pivot_segment_by_state` | pivot | `ds_segment_by_state` | Rows = state, cols = segment, cell = borrower count using Lakeview v3 multi-cell encoding. | 🟢 |
| `chart_top_segments_per_state` | bar | `ds_top_segments_per_state` | Top 3 segments per state, colored by segment. | 🟢 |

### Page — Triggers

| Widget | Type | Dataset | Purpose | Verdict |
|---|---|---|---|---|
| `chart_evidence_daily` | line | `ds_evidence_daily` | 30-day refresh-date evidence-event volume, colored by `signal_type`. | 🟢 |
| `chart_evidence_by_signal` | bar | `ds_evidence_by_signal` | Evidence counts by `signal_type`, colored by `source_product`. Demonstrates the `permit` / `listing` gap until those Cotality products are licensed. | 🟢 |

---

## Manual eyeball checklist (human, per deploy)

The automated probe can only assert on the structural contract (SQL
works, columns resolve, row count fits the chart type). Five things the
Lakeview renderer decides that still need a human look:

For **each dashboard** (`Executive`, `Segment`) open it after
`./scripts/deploy.sh -t dev` and check:

1. **Colors and theme** — Lakeview renders the MIP/Entrada AI/BI palette.
   Verify the 6 segments in `chart_equity_vs_spread` / `chart_top_segments_per_state`
   are visually distinguishable and that the state opportunity bar uses a
   monotonic score color ramp.
2. **Axis labels** — every chart's `displayName` (e.g., "Rate Spread vs
   Market (bps, 25-bp bins)") actually appears on the rendered axis,
   not the underlying field name. Lakeview occasionally drops the
   display name if the field expression and fieldName mismatch.
3. **Legend placement** — scatter (`chart_equity_vs_spread`) and grouped
   bar (`chart_top_segments_per_state`, `chart_evidence_by_signal`) have
   a legend visible without scrolling.
4. **Tooltip content** — hovering a point on the scatter or state bar
   shows every encoded field (state, borrower_count, mean_opportunity_score
   on the state bar; equity_pct, rate_spread_bps, segment, state, opportunity_score
   on the scatter). If a tooltip is missing a field, the `fields` array
   in the widget's query block is out of sync with the encodings.
5. **Tab and frame titles** — every widget carries `frame.title` and
   `frame.description`; both should render without truncation at
   1440 × 900. Truncated descriptions are a layout-width bug, not a copy
   bug.

If any of the five fails, capture the screenshot into
`docs/screenshots/validation/` and note the widget in the PR body.

### Cold-start screenshots to keep

Partners often review the dashboard within 24 h of a fresh deploy. Save
a deliberate day-1 screenshot of:

- `table_segment_overview` → the "Quarter-over-Quarter Change" column
  should read "n/a" or be blank. This is the documented cold-start
  behavior (`docs/dashboards.md` §1). Having a screenshot on hand means
  the reviewer doesn't have to be walked through the explanation live.
- `kpi_actioned` → reads `0` on cold deploy until operators approve from
  the Approval Queue (`docs/dashboards.md` §2).

---

## Automated verification

The integration test runs once per nightly:

```bash
pytest tests/integration/test_dashboard_widgets_resolve.py -q --tb=short
```

- Skips cleanly when `DATABRICKS_HOST` / `DATABRICKS_TOKEN` /
  `DATABRICKS_WAREHOUSE_ID` are not set (PR CI stays green).
- Executes once per dataset (16 datasets total; results cached
  across the per-widget assertions so the warehouse is not hit once
  per widget).
- Fails the job when any widget encodes a column not present in the
  dataset schema, or when a `bar` / `line` chart returns < 2 distinct
  x values (except for the documented cold-start widgets).
- Prints the `| dashboard | widget | query_ok | rows | notes |`
  markdown table to stdout; CI logs preserve it for auditing.

The nightly workflow is `.github/workflows/nightly.yml`; the dashboard
widget-resolve step is release-blocking (no `continue-on-error`).

---

*Owner: data-modeler. Review cadence: every time a new dashboard widget
or dataset is added, or when a gold/semantics column is renamed.*
