# Data Contract

This is the public-safe Module 0 data contract summary. A more detailed
internal implementation contract exists for engineering use, but external
reviewers should use this document as the stable entry point.

## Contracted Live Assets

- `mip.silver.property_master`
- `mip.silver.lien_current`
- `mip.silver.mortgage_events`
- `mip.silver.listing_activity`
- `mip.silver.heloc_propensity`
- `mip.silver.refi_propensity`
- `mip.gold.borrower_360`
- `mip.gold.lead_scores`
- `mip.gold.lead_population`
- `mip.gold.segment_population`
- `mip.gold.borrower_dossier`
- `mip.gold.source_readiness`
- `mip.ref.lender_dictionary`

## Contracted Pending Assets

- Building Permits: pending Cotality Delta Share. Filed permit activity must not
  be inferred from HELOC propensity or property condition fields.

## First-Party Demo Feed

- The Summit Mortgage demo workspace can populate `mip.first_party.*` through
  `sql/transformations/demo_first_party_feeds.sql`.
- Those rows are real Unity Catalog Delta rows, but each row carries
  `feed_mode='demo_synthetic'` and `synthetic_demo=true`.
- Customer/prod workspaces should set `MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=0`
  before running `tools/render_sql.py` / deploy and before connecting real LOS,
  servicing, CRM, interaction, and product-balance feeds.
- Source-readiness and `/api/v1/data-estate` must disclose the synthetic demo
  lane; it must not be described as real customer data.

## Non-Negotiables

- ZIP/postal values remain categorical strings, never numeric measures.
- Tenant-lender relationship logic comes from `mip.ref.lender_dictionary`; no
  brand-token substring fallback is allowed in production SQL.
- Public-demo mode masks raw CLIP, Owner Link, addresses, names, phones, emails,
  and competitor servicer names.
- Source-readiness and proof surfaces must show unavailable assets as
  unavailable instead of substituting demo data.
