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
| Create draft campaign | Reserves a Lakebase campaign in `building`, materializes one immutable treatment/holdout assignment plus manifest in `mip.audit.campaign_treatment_snapshot`, then atomically marks the campaign `ready` and writes variants/audit. Outreach reads only the pinned Delta version and intersects it with current eligibility. The synchronous Module 0 builder fails closed above 10,000 post-dedup selected primaries before any Delta MERGE; operators must refine larger cohorts, rather than depending on the warehouse client's 30-second cancellation budget for a multi-million-row write. |
| Show/compare actions | Write an audit event and never mutate borrower state without confirmation. Export actions are not enabled until a governed artifact writer exists. |

The size preflight and the MERGE both enforce the 10,000 post-dedup selected-primary ceiling,
and the MERGE also requires the exact preflight source snapshot id. A refresh
or cohort growth between those statements therefore produces no ready
manifest. Failed builds are terminal for that campaign id (no same-id
recomputation of an append-only proof); they may only be archived, then the
operator creates a new campaign with a refined cohort. Expired in-progress
leases rotate to a new materialization id so a late worker cannot finalize an
older write. A live at-cap latency measurement is still required before release
signoff. Portfolio preview and create responses expose the typed
`campaign_build_limit` and `campaign_build_eligible` fields so the product can
disable oversized builds and guide an operator to refine filters before submit.

## Known External Dependencies

One transcript data requirement remains externally blocked: Cotality filed Building Permits. MLS/listing activity is connected through `mip.silver.listing_activity`; the Permit segment remains visible as a pending-source segment because hiding it would conceal a real roadmap dependency, while treating missing data as zero demand would be false.
