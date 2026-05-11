# Partner Review Checklist

Last updated: 2026-05-07

Use this checklist before recording or presenting Module 0 with Databricks or Cotality reviewers.

## Story Alignment

- [x] Module 0 is framed as top-of-funnel lead generation and borrower segmentation.
- [x] The story starts with the mortgage AI data estate: customer first-party feeds, Cotality enrichment, Databricks governance/Genie/Lakebase, and Entrada transformations.
- [x] The app shows how a revenue or growth executive can ask a strategy question, inspect proof, open a governed cohort, review borrowers, build outreach, and approve manually.
- [x] Modules 1-4 are described as future extensions, not live workflow scope.

## Data Truth

- [x] Cotality public-record, lien, mortgage-event, CLIP, Owner Link, and AVM-derived fields are represented as live source lanes when Unity Catalog readiness confirms rows.
- [x] First-party lender tables exist as ingestion contracts under `mip.first_party`; the Summit Mortgage demo feed is live only when explicitly seeded and is labeled `demo_synthetic`, not real customer data.
- [x] MLS/Listings and Building Permits are explicitly labeled pending; the app does not treat them as zero demand.
- [x] FRED `MORTGAGE30US` refresh is required before public shipment and must show `source='fred'` for the latest rate row.

## Public Recording Safety

- [x] Public-demo masking is on by default.
- [x] Raw CLIP, Owner Link, owner names, street addresses, emails, phone numbers, and raw competitor lender names do not render in API/UI/CSV/audit surfaces.
- [x] Competitor lenders render as public-safe aliases (`Competitor A`, `Competitor B`, etc.).
- [x] Outreach drafts use placeholders such as `[first name]` and require human approval.

## Genie Review

- [x] Genie proof drawer includes trusted assets, row count, filters, data freshness when available, known data gaps, query trace, and SQL where returned.
- [x] ZIP/FIPS/CBSA/borrower ids render as identifiers, not numeric measures.
- [x] Protected-class and direct outreach-writing prompts are blocked before Genie execution.
- [x] Governed actions require confirmation and write Lakebase/audit state.
- [x] "Open this cohort in Lead Queue" must land on a filtered cohort route, not the generic queue.

## Live Validation Before External Use

- [x] Deploy the exact reviewed source.
  - Evidence: Databricks App deployment `01f14d00b90b15bba16e412e31a8edbd`, updated 2026-05-11T06:19:06Z, status `SUCCEEDED`. Enhanced DAB upload succeeded; the resource update path still returns Databricks permission error `PERMISSION_DENIED: You need "Can View" permission`, so the app was started with direct snapshot deploy from the refreshed workspace source path.
- [x] Run the Lakebase migration and score/source-readiness refresh jobs.
  - Evidence: FRED refresh run `1125872647053071`, silver refresh run `927414118382839`, Lakebase migration run `803051005237713`, gold refresh run `189210446175254`, lifecycle sync run `50010806203638`.
- [x] Run local backend/frontend tests.
  - Evidence: backend unit suite, focused live-SQL integration suite, `ruff`, frontend lint, frontend unit tests, frontend build, scaffold verification, and `git diff --check` passed.
- [x] Run live Playwright over Home -> Portfolio Builder -> Segments -> Lead Queue -> Borrower 360 -> Offer -> Ask Genie.
  - Evidence: live real-data Playwright passed 21 tests with 1 intentional degraded-mode skip; visual Playwright passed 4 tests.
- [x] Run the scripted Genie semantic regression set and manually inspect high-value prompts from the talk track.
  - Evidence: `docs/genie_eval/2026-05-07T01-10-00Z.md` passed 15/15 with 100.0/100 against the active app, including ZIP identifier handling, canonical SQL reconciliation, governed/refusal prompts, pending-permit gap handling, and the executive strategy question.
- [x] Capture evidence: app deployment timestamp, job run ids, SQL freshness rows, Playwright screenshots, and test logs.
  - Evidence: active app metadata, `/api/data-estate`, SQL probes, app logs, Playwright output, and Genie eval report were captured for this exact deployment.
