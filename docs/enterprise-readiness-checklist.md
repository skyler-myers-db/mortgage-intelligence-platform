# Enterprise Readiness Checklist

Last updated: 2026-05-06

This checklist separates implemented controls from the validation evidence that must be refreshed for each customer-facing release.

## Implemented Controls

- [x] Unity Catalog DDL covers raw, silver, gold, semantics, ref, and first-party schemas.
- [x] Optional first-party lender contracts exist under `mip.first_party.*`; the Summit Mortgage demo seed reports as live `demo_synthetic`, while customer/prod workspaces report `not_configured` until real feeds are populated.
- [x] Genie is scoped to curated gold/semantic assets and rejects untrusted source assets for governed actions.
- [x] Lakebase schema stores campaigns, approvals, saved workspace state, Genie sessions/messages, materialized Genie cohorts, cohort members, and audit events.
- [x] App source uses Databricks Asset Bundles with `engine: direct`.
- [x] No runtime mock fallback is available in the deployed app.
- [x] Public-demo masking is default-on for CLIP, Owner Link, borrower labels, subject property, and competitor lender names.
- [x] Evidence ids trace to Unity Catalog source tables and audit rows store only reviewed metadata keys.
- [x] Outreach approval cannot be completed without a backend Lakebase write.
- [x] API filters are parameterized; repository SQL does not accept raw SQL fragments from requests.
- [x] Protected-class Genie prompts and direct outreach-writing prompts fail closed.

## Per-Release Evidence To Refresh

- [ ] `databricks bundle validate -t dev --profile DEFAULT`.
- [ ] App deploy completes and `databricks apps get mip-app --profile DEFAULT -o json` shows the new active deployment.
- [ ] Lakebase migration job has run against the target workspace.
- [ ] Score/source-readiness jobs have refreshed downstream gold tables after SQL changes.
- [ ] Latest `mip.silver.market_rates_weekly` `MORTGAGE30US` row is from FRED, not seed.
- [ ] Backend tests pass.
- [ ] Frontend build and component tests pass.
- [ ] Live Playwright walkthrough passes against the deployed Databricks App URL.
- [ ] Genie semantic regression passes the release threshold and includes ZIP/FIPS/identifier chart checks.
- [ ] Source-readiness page shows live Cotality assets, live MLS/listing activity, explicit pending filed Building Permits, and truthful first-party status.

## Customer-Specific Items

- [ ] Confirm Cotality license posture for any public recording before raw or semi-raw identifiers are shown.
- [ ] For customer/prod workspaces, set `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0` and connect real LOS, servicing, CRM/campaign, interactions, and product-balance feeds.
- [ ] Replace Summit Mortgage tenant defaults with customer-specific footprint, lender aliases, rules, and Genie space instructions.
- [ ] Confirm Databricks App service principal has only required Unity Catalog, Lakebase, warehouse, and Genie permissions.
- [ ] Confirm audit export/retention requirements with the customer compliance owner.
