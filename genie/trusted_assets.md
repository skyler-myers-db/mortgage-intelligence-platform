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
| `mip.gold.lead_segment_membership` | table | borrower × segment | Powers the Segment Intelligence route and the "which segment is this borrower in" drill-down. |
| `mip.gold.lead_scores` | table | one row per borrower | Canonical lead score — parity-pinned between `fn_lead_score.sql` and `backend/services/scoring.py`. |
| `mip.gold.borrower_360` | table | one row per borrower | Feeds the Borrower 360 route, the Evidence Drawer, and the dossier preview rail. |
| `mip.gold.evidence_events` | table | append-only event ledger | The "why now" signal — trigger events with UTC timestamps, confidence, and source citations. |
| `mip.gold.recommended_offers` | table | one row per borrower × current offer | Next-best-offer output; the recommendation that a human approves before outreach. |
| `mip.semantics.lead_generation_metric_view` | metric view | funnel-wide | Executive + Head-of-Growth funnel KPIs: addressable → eligible → scored → approved → actioned. |
| `mip.semantics.segment_performance_metric_view` | metric view | segment | Segment strategy and A/B decisions: mean score, rate spread, equity, approval rate, outreach rate. |
| `mip.semantics.borrower_opportunity_metric_view` | metric view | region × product × trigger | Territory planning and campaign-budget allocation. |

## Why each asset matters for Module 0

### `gold.lead_population`
The addressable market. Every other gold asset joins back to this row set
on `borrower_id`. Without it, the funnel has no denominator and the
executive dashboard has no "how big is the pond" KPI.

### `gold.lead_segment_membership`
Segment membership is the primary lever a Head of Growth pulls when
deciding where to spend the next marketing dollar. The rules (In-the-Money,
HELOC/Cash-Out, Listed-for-Sale, Investor/Multi-Property, Retention/
Recapture) live in `sql/transformations/gold_lead_segment_membership.sql`
and are parity-pinned to the UC scalar functions under `sql/uc_functions/`.

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

### `gold.recommended_offers`
Next-best-offer per borrower from `fn_next_best_offer.sql`. Offer types:
Rate-Term Refi, Cash-Out, HELOC, Purchase, Retention. Each row includes
projected monthly savings and a confidence band. Human approval is
**required** before any outreach is queued — enforced by the approval
flow in Lakebase (`lakebase/schema.sql`) and the ApprovalBanner component.

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
