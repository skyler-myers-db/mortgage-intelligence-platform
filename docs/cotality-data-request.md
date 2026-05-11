# Cotality Data Request — Module 0

**Objective:** request only the Cotality products required to close Module 0's remaining data gaps. The current Delta Share already supports the refi, cash-out/equity, investor, current-customer retention, and distress-lite use-cases. Do not expand this request to broader marketplace products unless Module 0 scope changes.

## P0 Request

| Priority | Cotality product | Module 0 contract it unlocks | Required grain/key | Required freshness |
|---|---|---|---|---|
| P0 | MLS Listings | `borrower_360.listed_for_sale`, `listed` segment, and `fn_next_best_offer` `purchase` branch | CLIP-keyed listing row, or listing row mappable to CLIP | Daily or better |
| P0 | Building Permits | Permit-driven HELOC intent, `borrower_360.has_permit`, `permit` segment, and permit evidence rows | CLIP-keyed permit row, or permit row mappable to CLIP | Daily/weekly; include permit issue date |

## Why These Two

**MLS Listings** is the only honest source for active listed-for-sale intent. The existing mortgage, owner-transfer, property, and voluntary-lien tables observe historical transactions or current lien/property state; they cannot infer an active listing before a sale closes.

**Building Permits** is the missing intent trigger for HELOC. The current app can rank equity-only HELOC candidates from AVM/equity and no active second-position balance, but it cannot claim renovation intent until permit rows exist.

## Minimum Fields Needed

### MLS Listings

- `clip` or Cotality-supported fields required to resolve to CLIP.
- Listing status: active, pending, off-market/cancelled if available.
- Listing date, status date, days on market.
- List price and price-change date/amount if available.
- Property state/ZIP/CBSA or enough geography to validate the configured
  Module 0 state set and discovered county/ZIP coverage.
- Listing source/provenance and refresh timestamp.

### Building Permits

- `clip` or Cotality-supported fields required to resolve to CLIP.
- Permit id, permit type/category, issue date, status.
- Permit valuation or estimated project cost where available.
- Project description/category sufficient to distinguish renovation/addition/ADU/pool/solar from non-HELOC-relevant permits.
- Property state/ZIP/CBSA or enough geography to validate the configured
  Module 0 state set and discovered county/ZIP coverage.
- Permit source/provenance and refresh timestamp.

## Products Not Requested Now

Do not request AVM, Voluntary Lien Status, Mortgage Transaction Data, Owner Transfer and Sales Data, Loan Assignments, Loan Releases, or Mortgage Market Analytics for Module 0; equivalent source coverage is already present in the current Cotality Delta Share.

Customer 360 and persona/segment sample files discussed on Apr 16 would be useful accelerators for comparison and storytelling, but they should be treated as optional reference samples rather than runtime dependencies. The implemented app derives borrower 360 and segment membership from the Delta Share gold refresh.

Defer HPI Forecast, Pre-Foreclosure, CLIP MCP, climate, neighborhood, demographic, propensity, insurance, rent, and tax/enhancement products to later modules unless the product scope changes.

## Governance Notes

- The app must continue to surface synthetic borrower labels only; no raw names, street addresses, emails, or phone numbers are requested.
- New rows should land in Unity Catalog and feed `mip.silver.*` / `mip.gold.*` transformations, not a runtime API fallback.
- Evidence rows must cite real source table paths and source timestamps.
- The current `listed` and `permit` segments stay zero-count until these sources are licensed, delivered, and wired.
