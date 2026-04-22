# Trusted Assets — Mortgage Lead Intelligence Genie Space

The Mortgage Lead Intelligence Genie Space is grounded on a curated set of
Unity Catalog assets from the `mip` catalog. Every answer Genie
returns must cite one of the tables or metric views below. Nothing else
is in scope for this space.

These assets are authored and refreshed by the Databricks bundle:
raw Cotality shares land in `mip.raw.*`, silver features in
`mip.silver.*`, and the trusted gold + semantic assets below are
materialized by `pipelines/lakeflow/*` and exposed to the app via the
serverless SQL warehouse referenced in `databricks.yml`.

| Asset | Kind | Grain | Why Module 0 cares |
|---|---|---|---|
| `mip.gold.lead_population` | table | one row per eligible borrower | Defines the addressable market; the denominator for every funnel metric. |
| `mip.gold.segment_population` | table | (segment_code, state) + '_ALL' national rollup | Powers the Segment Intelligence route's segment rows — count + mean score per (segment, state). |
| `mip.gold.lead_scores` | table | one row per borrower | Canonical lead score — parity-pinned between `fn_lead_score.sql` and `backend/services/scoring.py`. |
| `mip.gold.borrower_360` | table | one row per borrower | Feeds the Borrower 360 route, the Evidence Drawer, and the dossier preview rail. |
| `mip.gold.borrower_dossier` | table | one row per borrower (denormalised) | Pre-joined single-row payload for `/api/borrowers/{id}`; carries an ARRAY<STRUCT> of up to 20 recent evidence events + top-3 trigger timeline. |
| `mip.gold.evidence_events` | table | append-only event ledger | The "why now" signal — trigger events with UTC timestamps, confidence, and source citations. |
| `mip.gold.lockin_cohort` | table | one row per borrower in the sub-3% 2020–2022 cohort | Size + composition of the rate-lock-in cohort that is retention / HELOC / cash-out addressable but will not rate-and-term refi. |
| `mip.semantics.lead_generation_metric_view` | metric view | funnel-wide | Executive + Head-of-Growth funnel KPIs: addressable → eligible → scored → approved → actioned. |
| `mip.semantics.segment_performance_metric_view` | metric view | segment | Segment strategy and A/B decisions: mean score, rate spread, equity, approval rate, outreach rate. |
| `mip.semantics.borrower_opportunity_metric_view` | metric view | region × product × trigger | Territory planning and campaign-budget allocation. |

## Why each asset matters for Module 0

### `gold.lead_population`
The addressable market. Every other gold asset joins back to this row set
on `borrower_id`. Without it, the funnel has no denominator and the
executive dashboard has no "how big is the pond" KPI.

### `gold.segment_population`
Per-(segment_code, state) rollup of borrower counts + mean lead score +
QoQ delta, plus a per-segment national `_ALL` row. Segment codes:
`itm`, `listed`, `permit`, `investor`, `equity`, `retention`. Segment
membership is evaluated once in `gold.borrower_360.segment_codes` (the
`BLOCKED` predicates for listed/permit are forced false there); this
table is a straight aggregate over the resulting array so a Head of
Growth can answer "how big is each segment in Texas" without a runtime
EXPLODE. The rules themselves live in
`sql/transformations/gold_borrower_360.sql` and
`sql/uc_functions/fn_in_the_money.sql`.

### `gold.lead_scores`
The 0–100 lead score is the ranking used by the Lead Queue and Borrower 360
routes. It must stay pinned between the SQL function
(`fn_lead_score.sql`) and the Python fallback
(`backend/services/scoring.py`). Golden fixtures in
`tests/fixtures/scoring_parity/` enforce parity.

### `gold.borrower_360`
Unified borrower profile — property details, mortgage state, owner
relationships, and behavioral signals — denormalized for fast single-
borrower reads. This is the source of truth for the Evidence Drawer.

### `gold.evidence_events`
Append-only event ledger. Every card in the UI that shows "why now" reads
from this table (via `backend/services/evidence.py`). Trigger types
include rate-drop, equity-crossed, permit-filed, listed-for-sale,
lien-change. Each row carries a `source_table` citation back to the
Cotality silver layer.

### `gold.borrower_dossier`
One row per `borrower_id` pre-joined with everything the
`/api/borrowers/{id}` dossier payload needs: property + mortgage state,
segment membership, lead score, and an `ARRAY<STRUCT>` of up to 20
recent evidence events (with the top-3 trigger timeline called out
separately). Built by `sql/transformations/gold_borrower_dossier.sql`
in lockstep with `gold.borrower_360` on every `mip_refresh_scores`
run. Single-borrower reads hit this table for a one-round-trip indexed
lookup; aggregate questions should prefer `gold.borrower_360` or the
`borrower_opportunity` metric view.

### `gold.lockin_cohort`
Pre-materialised cohort of borrowers who originated (or last refinanced
into) a sub-3 % first-position mortgage between 2020-01-01 and 2022-12-31.
One row per CLIP with origination date / rate / loan type / current equity
and rate spread alongside. The purpose is to let Genie answer lock-in
sizing questions (sample question 5) without touching silver — the
`first_pos_rate`, `first_pos_date` fields on silver.lien_current are the
source, but silver is out-of-scope for this space per the rules below.
Refreshed by `mip_refresh_scores` from
`sql/transformations/gold_lockin_cohort.sql`.

### `semantics.lead_generation_metric_view`
The funnel metric view the Executive Dashboard and Head-of-Growth questions
resolve to. Defines the canonical funnel stages so every surface (app,
dashboard, Genie) reports the same numbers.

### `semantics.segment_performance_metric_view`
Segment-level KPIs: count, mean lead score, mean rate spread, mean equity,
approval rate, outreach rate. Powers the Segment Intelligence cards and
answers "which segment should I invest in next quarter".

### `semantics.borrower_opportunity_metric_view`
Borrower-opportunity rollups sliced by region (MSA/ZIP), product, and
trigger type. Powers the geography drill-down map and territory-planning
questions.

## Out of scope for this space

Anything outside `mip.gold.*` and `mip.semantics.*` is
**not** trusted for this space, specifically:

- `mip.raw.*` — Cotality-share raw tables. Too wide, too noisy for
  conversational Q&A.
- `mip.silver.*` — intermediate features. Mixed grain and not
  governed with the same care as gold.
- `mip_app.*` (Lakebase) — operational state (approvals, audit, sessions).
  Routed through the backend, not Genie.
- Any other catalog on the workspace.
