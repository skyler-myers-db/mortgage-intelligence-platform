# Module 0 Alignment TODO

Last updated: 2026-05-07

Scope: Close the gaps found when comparing the Module 0 requirements document and Cotality transcripts against the current app. This tracker is intentionally implementation-facing; each item must either be fixed in code/docs/tests or explicitly accepted as a scoped demo limitation.

## P0 - Truth And Semantics

- [x] Make lender/customer relationship semantics honest.
  - Implemented: tenant/current/former/competitor relationship semantics are driven by `mip.ref.lender_dictionary`, not a hard-coded brand token.
  - Implemented: Portfolio Builder and Lead Queue can filter by public-safe target-lender aliases returned from the live config endpoint.
  - Implemented: `Former customer` means historical tenant-lender Owner Link relationship with no current tenant-serviced lien, not "unknown/non-competitor."

- [x] Make HELOC filters actually HELOC/second-lien-specific.
  - Current state: Portfolio Builder `Open HELOC` maps to the same predicate as open first lien.
  - Required outcome: HELOC/second-lien filters use `second_pos_amount > 0`.

- [x] Remove hardcoded configured-state Genie footprint checks.
  - Current state: app has a resolver for tenant footprint, but Genie still carries a literal configured-state tuple.
  - Required outcome: Genie out-of-footprint guard uses `mip.ref.state_footprint` through `StateFootprintResolver`.

- [x] Make segment multi-select semantics explicit.
  - Current state: Segment Intelligence sends `segmentMode = "all"` and therefore shows the intersection of selected cards.
  - Required outcome: UI copy says "match all selected segments" and Lead Queue deep links/tests preserve that contract.

## P1 - Source Honesty And Docs

- [x] Correct public/product copy that overclaims pending feeds.
  - Current state: README says listing, permit, and HPI are used in the app.
  - Required outcome: copy distinguishes live sources from pending MLS/listing, Building Permits, optional HPI/CLIP-MCP.

- [x] Reconcile `docs/data-contract-module0.md` with current scoring SQL.
  - Current state: sub-score section still documents old piecewise scoring.
  - Required outcome: documented formulas match the continuous formulas in `gold_borrower_360.sql` and `gold_lead_scores.sql`.

- [x] Fill `docs/cotality-data-request.md`.
  - Current state: placeholder.
  - Required outcome: Cotality-facing ask names MLS/Listings, Building Permits, optional HPI, future CLIP-MCP, and the Apr 16 Customer 360/persona sample-file distinction.

- [x] Reconcile stale governance docs.
  - Current state: pre-cutover review still contains retired mock-mode and raw-CLIP guidance that conflicts with the current live-data app.
  - Required outcome: docs clearly state the current public-demo posture: redacted borrower labels, no street addresses, real CLIP only if approved by license posture, and no mock runtime fallback.

## P2 - Validation Gates

- [x] Backend unit tests for portfolio predicates.
  - Former customer, current customer, competitor customer, open first lien, HELOC/second lien, and tenant-footprint resolution.

- [x] Frontend tests for user-visible truth.
  - Fixed demo-lender label, no arbitrary lender input, segment intersection copy, pending-feed copy.

- [x] Docs guard tests.
  - README and key docs must not claim MLS/listing, Building Permits, HPI, Agent Bricks, MLflow, or CLIP-MCP are live unless the implementation is live.

- [x] Live walkthrough after local gates.
  - Portfolio Builder -> Segment Intelligence -> Lead Queue -> Borrower 360 -> Offer -> Ask Genie action.
  - Verify out-of-footprint Genie answer, HELOC filter behavior, and segment multi-select counts/routes.

## Remaining Signoff Conditions

- [x] FRED/MORTGAGE30US live refresh uses the official FRED feed.
  - Fixed: removed the brittle bespoke FRED user-agent and added retry/backoff.
  - Verified deployed: `mip_fred_rates_ingest` now reaches the official FRED CSV endpoint without the previous timeout-prone custom user agent.
  - Verified SQL on 2026-05-07: `mip.silver.market_rates_weekly` has exactly one latest `MORTGAGE30US` row: week 2026-05-04, rate 6.37, source `fred`.

- [x] Public-demo raw CLIP and Owner Link masking.
  - Fixed: API/UI/CSV/audit surfaces emit `clip_ref_*` and `owner_link_ref_*` display refs by default.
  - Internal escape hatch: `MIP_EXPOSE_RAW_COTALITY_IDS=1`; leave unset for demos/customers.
  - Verified deployed: `/api/leads` and a dynamically selected `/api/borrowers/{borrower_id}` payload return `clip_ref_*` and `owner_link_ref_*` display refs.
  - Raw IDs remain below the repository boundary in Unity Catalog for governed joins/auditability.

## Apr 30 Databricks/Cotality Touchpoint Scope

- [x] Make the "under the hood" mortgage AI data estate visible in-product.
  - Implemented: `/api/data-estate` groups readiness rows into first-party lender data, Cotality enrichment, Databricks governed AI layer, and Entrada transformations.
  - Implemented: Home shows the data-estate panel and public-demo masking state.

- [x] Add real first-party ingestion contracts and an explicitly synthetic Summit demo feed.
  - Implemented: `mip.first_party.loan_applications`, `servicing_portfolio`, `crm_campaign_membership`, `customer_interactions`, and `product_balances`.
  - Implemented: `sql/transformations/demo_first_party_feeds.sql` populates realistic Summit Mortgage demo rows with `feed_mode='demo_synthetic'` and `synthetic_demo=true`.
  - Implemented: `gold.borrower_360` and `gold.lead_scores` consume first-party servicing/application/CRM/interaction/product signals for current/former-customer and relationship scoring.
  - Implemented: source readiness and `/api/data-estate` disclose synthetic demo feeds; customer/prod workspaces can disable them with `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0`.

- [x] Support public-safe target lien-holder filtering.
  - Implemented: current lender aliases are public-safe (`Summit Mortgage`, `Competitor A`, `Competitor Other`).
  - Implemented: Portfolio Builder can filter by the live target-lender alias list and deep-link that predicate into Lead Queue.

- [x] Make Genie governed actions execute real workflows.
  - Implemented: "Open this cohort in Lead Queue" materializes a Lakebase `genie_cohorts` row and optional `genie_cohort_members` rows, then opens a `cohort_id` route.
  - Implemented: Lead Queue replays the Lakebase cohort filters so an edited or generic URL cannot widen the reviewed Genie result.

- [x] Replace placeholder partner/architecture docs.
  - Implemented: `docs/architecture.md`, `docs/partner-review-checklist.md`, and `docs/enterprise-readiness-checklist.md` now describe the shipped controls and per-release evidence gates.

- [x] Refresh deployed source, downstream gold tables, and source-readiness rows after these code/SQL changes.
  - Verified deployed: Databricks App deployment `01f14d00b90b15bba16e412e31a8edbd`, updated 2026-05-11T06:19:06Z. Enhanced DAB upload succeeded; Databricks still blocks the resource update call with `PERMISSION_DENIED`, so the active app was started with direct snapshot deploy from the refreshed workspace source path.
  - Verified jobs: FRED refresh run `1125872647053071`, silver refresh run `927414118382839`, Lakebase migration run `803051005237713`, gold refresh run `189210446175254`, lifecycle sync run `50010806203638`.
  - Verified API/SQL: `/api/data-estate` labels Summit first-party feeds as `demo_synthetic`; Cotality MLS/Listings and Building Permits remain pending with no live rows.
- [x] Run local backend/frontend tests after these code/SQL changes.
  - Verified: unit suite, focused live-SQL integration suite, `ruff`, frontend lint, frontend unit tests, frontend build, scaffold verification, and `git diff --check` all passed.
- [x] Run live smoke/API checks after deploying this exact pass.
  - Verified: live smoke passed against `https://mip-app-2543889327043640.aws.databricksapps.com`.
  - Verified browser: Playwright real-data suite passed 21 tests with 1 intentional degraded-mode skip; visual suite passed 4 tests.
  - Verified Genie semantics: `docs/genie_eval/2026-05-07T01-10-00Z.md` passed 15/15 with 100.0/100, including ZIP identifier handling, canonical SQL reconciliation, protected-class refusal, out-of-footprint refusal, and pending-permit gap handling.
- [ ] Obtain independent reviewer signoff after tests and live proof complete.
  - Current status: not signed off. The 2026-05-21 hardening review requires a fresh green deployed Playwright matrix, Genie eval proof, authenticated non-admin RBAC proof, and reviewer convergence before this can be marked complete again.

## Scope Limitations Still Explicit

- [ ] Cotality MLS/Listings Delta Share is still pending, so `Listed for Sale` remains an awaiting-feed segment.
- [ ] Cotality Building Permits Delta Share is still pending, so `Permit Activity` remains an awaiting-feed segment.
- [ ] No finite test suite can prove every possible future free-form Genie prompt. The live gate covers the scripted executive workflows, source/proof/action paths, ZIP cohort action, policy-blocked/degraded states, and value-level identifier masking. New high-value prompts should be added to the Genie semantic regression matrix before client use.
