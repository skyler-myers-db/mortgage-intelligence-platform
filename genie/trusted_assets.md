# Trusted Assets — Mortgage Lead Intelligence Genie Space

The Mortgage Lead Intelligence Genie Space is grounded on a curated set of
Unity Catalog assets. The logical asset names below use the default `mip`
catalog; customer deployments may render the same trusted assets into a
different configured catalog. Every in-scope analytic answer Genie returns
must cite one of the tables or metric views below. Refusals, source-gap
answers, and off-topic redirects may cite no analytic table. Nothing else is
in scope for this space.

These assets are authored and refreshed by the Databricks bundle:
source Delta Share tables are lifted from `cotality_mortgage_data.corelogic`,
intermediate features land in `mip.silver.*`, gold tables are rebuilt by the
`mip_refresh_scores` SQL task chain, and semantic views are applied from
`sql/metric_views/*`. Genie is bound only to the trusted gold + semantic
assets below through the serverless SQL warehouse referenced in
`databricks.yml`.

| Asset | Kind | Grain | Why Module 0 cares |
|---|---|---|---|
| `mip.gold.lead_population` | table | one row per score-qualified ranked lead | Defines the Lead Queue ranking universe after the opportunity-score floor. API/UI filters such as marketing eligibility and consent make a row action-ready. Do not use this as the broader Portfolio Builder denominator. |
| `mip.gold.segment_population` | table | (segment_code, state) + '_ALL' national rollup | Powers the Segment Intelligence route's segment rows — count + mean score per (segment, state). |
| `mip.gold.lead_scores` | table | one row per CLIP / borrower record | Canonical lead score — parity-pinned between `fn_lead_score.sql` and `backend/services/scoring.py`. |
| `mip.gold.borrower_360` | table | one row per CLIP / borrower record | Feeds the Borrower 360 route, the Evidence Drawer, and the dossier preview rail. |
| `mip.gold.borrower_dossier` | table | one row per borrower (denormalised) | Pre-joined single-row payload for `/api/borrowers/{id}`; carries an ARRAY<STRUCT> of up to 20 recent evidence events + top-3 trigger timeline. |
| `mip.gold.evidence_events` | table | refreshed trigger evidence table | The "why now" signal — trigger events with business/source timestamps, confidence, and source citations. |
| `mip.gold.source_readiness` | table | one row per source/feed | Non-PII readiness ledger for explaining live, configured-empty, not-configured, roadmap, error, and synthetic-demo feeds; use for data-gap answers instead of returning fake zero demand. |
| `mip.gold.lockin_cohort` | table | one row per borrower in the sub-3% 2020–2022 cohort | Size + composition of the rate-lock-in cohort that is retention / HELOC / cash-out addressable but will not rate-and-term refi. |
| `mip.gold.funnel_snapshot_daily` | table | state × segment × snapshot date | Daily scored-population, approval, and outreach snapshots. Use for trends over time instead of inventing trend lines from current borrower rows. |
| `mip.gold.county_rollup` | table | county geography rollup | Current discovered county coverage and marketable borrower rollups. |
| `mip.gold.zip_rollup` | table | ZIP geography rollup | Current discovered ZIP coverage and marketable borrower rollups; ZIPs are identifiers, not measures. |
| `mip.semantics.lead_generation_metric_view` | metric view | one row per ranked lead | Executive + Head-of-Growth funnel KPIs over the ranked lead queue. Segment filters must use `array_contains(segment_codes, '<segment_code>')`; aggregate counts must use `COUNT(DISTINCT clip)`. |
| `mip.semantics.segment_performance_metric_view` | metric view | segment × state plus '_ALL' national rollup | Segment strategy and A/B decisions: count, mean opportunity score, approval rate, outreach rate, and snapshot deltas. Segment economics such as mean rate spread or mean equity come from borrower-grain assets. |
| `mip.semantics.borrower_opportunity_metric_view` | metric view | one row per CLIP / borrower record | Borrower-grain opportunity surface with state, product, trigger, rate-spread, equity, and offer fields for read-time aggregation. Use `mip.gold.borrower_360.situs_cbsa_code` for MSA/CBSA questions. |

## Why each asset matters for Module 0

### `gold.lead_population`
The ranked Lead Queue population. This is the score-qualified subset used for
operational lead lists and top-N prioritization after the gold borrower
profile has been scored. It carries marketing eligibility, consent, and
suppression fields, but the row becomes action-ready only when API/UI filters
or explicit SQL predicates keep eligible borrowers. It is not the same as the
broader Portfolio Builder marketable population, which is computed from
`gold.borrower_360` with the selected portfolio criteria.

### `gold.segment_population`
Per-(segment_code, state) rollup of borrower counts + mean lead score +
QoQ delta, plus a per-segment national `_ALL` row. Segment codes:
`itm`, `listed`, `permit`, `investor`, `equity`, `retention`. Segment
membership is evaluated once in `gold.borrower_360.segment_codes` (the
`listed` predicate is live from MLS and the legacy `permit` code is displayed
as HELOC Intent from Cotality HELOC propensity; true filed permits remain a
separate pending source); this
table is a straight aggregate over the resulting array so a Head of
Growth can answer "how big is each segment by coverage state" without a runtime
EXPLODE. The rules themselves live in
`sql/transformations/gold_borrower_360.sql` and
`sql/uc_functions/fn_in_the_money.sql`.

Listing-only questions must be answered from the live MLS/listing fields in
`gold.borrower_360` or the `listed` segment membership. Do not pair those
answers with Building Permit caveats unless the user explicitly asks about
filed permits.

### `gold.lead_scores`
The 0–100 lead score is the ranking used by the Lead Queue and Borrower 360
routes. It must stay pinned between the SQL function
(`fn_lead_score.sql`) and the Python fallback
(`backend/services/scoring.py`). Golden fixtures in
`tests/fixtures/scoring_parity/` enforce parity.

### `gold.borrower_360`
Unified borrower profile — property details, mortgage state, owner
relationships, and behavioral signals — denormalized for fast single-
borrower reads. This is the source of truth for the Evidence Drawer and
for unique borrower counts such as "how many borrowers are currently
in-the-money?"

### `gold.evidence_events`
Refreshed trigger evidence table rebuilt by the gold refresh job. Borrower
and offer surfaces that show "why now" read from this table through the
borrower dossier and evidence repository paths. Governed trigger `signal_type` values
are `rate_spread`, `equity`, `market_trend`, `competitor_lien`,
`multi_property`, `absentee_mailing`, `corporate_owner`, `recent_refi`,
`recent_payoff`, `recent_sale`, `foreclosure_stage`, `listing`,
`heloc_propensity`, and `refi_propensity`. Competitor-lien
evidence is always `signal_type = 'competitor_lien'`; no alias is valid. Filed
building-permit trigger feeds remain pending Cotality delivery and must be
disclosed instead of treating missing permit data as zero demand. Each row
carries a `source_table` citation back to the Cotality-derived intermediate
layer, but Genie should query this gold evidence table rather than reading
silver tables directly.

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

### `gold.county_rollup` / `gold.zip_rollup`
Geography scope assets. These let Genie and the app discover what the current
tenant/data-share coverage actually contains instead of relying on hardcoded
demo geography. Use them for coverage checks, maps, and drill-down questions.
Use `gold.county_rollup`, `gold.zip_rollup`, and `gold.borrower_360` to infer
current data coverage. `ref.state_footprint` is app fallback metadata only; it
is not a trusted Genie source or a fixed state whitelist for answers.

### `semantics.lead_generation_metric_view`
The funnel metric view the Executive Dashboard and Head-of-Growth questions
resolve to. Defines the canonical funnel stages so every surface (app,
dashboard, Genie) reports the same numbers.

### `semantics.segment_performance_metric_view`
Segment-level KPIs: count, mean opportunity score, approval rate, outreach
rate, and snapshot deltas. Powers the Segment Intelligence cards and answers
"which segment should I invest in next quarter." It does **not** expose
rate-spread or equity columns; questions about those economics should aggregate
`gold.borrower_360` or `semantics.borrower_opportunity_metric_view`.

### `semantics.borrower_opportunity_metric_view`
Borrower-grain opportunity view over `gold.borrower_360`. It exposes state,
segment membership, loan/product proxy, relationship flags, listing/propensity
triggers, current-lender alias, rate spread, equity, in-the-money, balance,
and opportunity score. Aggregate questions compute rollups at read time with
`COUNT(DISTINCT clip)`, `AVG(rate_spread_bps)`, `AVG(equity_pct)`, and similar
expressions. MSA/CBSA and ZIP questions should use `mip.gold.borrower_360`
directly because `situs_cbsa_code` and `zip` live on the gold borrower profile.

## Out of scope for this space

Anything outside the trusted assets listed above is
**not** trusted for this space, specifically:

- `cotality_mortgage_data.corelogic.*` and `mip.raw.*` — raw/source-share
  tables. Too wide, too noisy for conversational Q&A.
- `mip.silver.*` — intermediate features. Mixed grain and not
  governed with the same care as gold. Listing, HELOC-propensity, and
  refi-propensity signals are exposed through `gold.borrower_360`,
  `gold.evidence_events`, and semantic views instead.
- `mip_app.*` (Lakebase) — operational state (approvals, audit, sessions).
  Routed through the backend, not Genie.
- Any other catalog on the workspace outside the configured deployment catalog.
