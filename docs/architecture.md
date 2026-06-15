# Mortgage Intelligence Platform Architecture

Last updated: 2026-06-15

Module 0 is a Databricks-native top-of-funnel mortgage lead-generation product. The runtime contract is:

1. Build a borrower population from governed Cotality public-record assets and optional customer first-party feeds.
2. Segment and rank borrowers with reviewed Unity Catalog SQL and SQL functions.
3. Explain every recommendation with source evidence, freshness, filters, and generated SQL where applicable.
4. Let a human save a lead, create a campaign, approve outreach, or reject a lead.
5. Persist every state-changing action in Lakebase with actor attribution and PII-safe metadata.

## Data Estate

The app exposes the data estate in four lanes so a reviewer can see what is live and what is pending.

| Lane | Live implementation | Notes |
|---|---|---|
| First-party lender data | `mip.first_party.*` tables for LOS applications, servicing, CRM/campaigns, interactions, and product balances | In the Summit Mortgage demo, these tables are populated by an explicit `feed_mode='demo_synthetic'` seed so reviewers can see the lender-owned ingestion lane. Customer/prod deploys set `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0` before SQL render/deploy and remain `not_configured` until real customer feeds are connected. |
| Cotality enrichment | `mip.silver.property_master`, `mip.silver.lien_current`, `mip.silver.mortgage_events`, `mip.silver.listing_activity`, Owner Link bridge, AVM fields, CLIP-keyed joins | MLS/listing activity is live when rows are present. Filed Building Permits remain visibly pending until Cotality/partner approval delivers that Delta Share. |
| Databricks governed AI layer | Unity Catalog gold tables, semantic views, Genie, Lakebase, Databricks Apps direct deployment | Genie answers are accepted only when they cite trusted assets and proof; state-changing actions require confirmation. |
| Entrada transformations | SQL transformations, scoring functions, next-best-offer logic, redaction, React/FastAPI workflows | Deterministic SQL functions are the score and offer source of truth. No ML score placeholder is used. |

## Runtime Components

| Component | Responsibility |
|---|---|
| Databricks Bundle | Deploys SQL, jobs, Genie configuration, Lakebase migration, and the Databricks App using the direct DAB engine. |
| Unity Catalog | Stores bronze/silver/gold/semantic/first-party assets. Catalog/schema/table comments document source and purpose. |
| FastAPI backend | Owns input validation, repository queries, Genie orchestration, PII redaction, and Lakebase writes. |
| React frontend | Renders the prototype-contracted Module 0 workflow: Home, Portfolio Builder, Segments, Lead Queue, Borrower 360, Offer, Ask Genie, Admin. |
| Lakebase | Stores campaigns, approvals, saved leads, saved drafts, Genie sessions/messages, materialized Genie cohorts, and audit rows. |
| Genie | Answers free-form questions over curated mortgage assets. The app renders dynamic charts/tables/maps from returned rows and exposes proof. |

## PII Boundary

Public-demo masking is enabled by default. API responses expose `clip_ref_*`, `owner_link_ref_*`, synthetic borrower labels, city/state/ZIP, score/offer fields, and public-safe lender aliases such as `Competitor A`. Raw owner names, street addresses, raw CLIPs, raw Owner Link ids, emails, and phone numbers must not cross the repository boundary unless an explicit internal operator flag is enabled and license approval exists.

## Governed Genie Actions

Genie is not just a chat response surface. Confirmed actions write governed state:

| Action | Backend behavior |
|---|---|
| Open this cohort in Lead Queue | Materializes `mip_app.genie_cohorts` plus optional `mip_app.genie_cohort_members`, returns `/lead-queue?cohort_id=...` with reviewed filters, and logs `GENIE_ACTION_OPEN_COHORT`. |
| Save borrowers | Writes actor-scoped `saved_leads` and one audited Genie action idempotently. |
| Create draft campaign | Writes `mip_app.campaigns` with the reviewed cohort criteria and audit metadata. |
| Show/compare actions | Write an audit event and never mutate borrower state without confirmation. Export actions are not enabled until a governed artifact writer exists. |

## Known External Dependencies

One transcript data requirement remains externally blocked: Cotality filed Building Permits. MLS/listing activity is connected through `mip.silver.listing_activity`; the Permit segment remains visible as a pending-source segment because hiding it would conceal a real roadmap dependency, while treating missing data as zero demand would be false.
