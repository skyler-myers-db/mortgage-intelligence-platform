# Module 0 — Data Sources Gap Analysis

**Audience:** Internal implementation note. Not approved for public release or external recording without Cotality review.

**Scope:** Track the remaining data-source gaps for the Unity-Catalog-backed Module 0 app.
**Status:** The Cotality Delta Share `cotality_mortgage_data.corelogic`, FRED market-rate ingestion, silver/gold DDL, scoring UDFs, and Module 0 SQL transformations are implemented in-repo. This document now records the remaining data-source blockers and the minimum Cotality request needed to close them.

**TL;DR:** We can model the core refi, cash-out/equity, investor, current-customer retention, and distress-lite use-cases from the current share plus FRED. Cotality's suggestion to add **MLS Listings** and **Building Permits** is correct: MLS unlocks the blocked `listed` segment and purchase branch; Permits upgrade HELOC from equity-only to intent-driven. Everything else on the marketplace board is a future-module enhancement, not a Module 0 blocker.

---

## 1. What we have in the share

Catalog: `cotality_mortgage_data.corelogic`
Provider: `datashare_us_west_2` · Share: `corelogic_226d2860` · Live probe used for this analysis: Apr 21, 2026

| Table | Rows | Cols | Primary key | Role |
|---|---:|---:|---|---|
| `entrada_eval_voluntary_lien_status_marketing_v2` | 5,156,186 | 347 | `clip` (1:1) | **Current-state snapshot per property.** 4-lien stack, current rates, AVM, equity, LTV, current servicer. This is THE spine of Module 0. |
| `entrada_eval_property_domain_v3` | 5,192,915 | 246 | `clip` | Property characteristics: owner names/IDs, mailing vs. situs, tax/exempt, building attrs, lat/lon, foreclosure stage, CBSA/census. |
| `entrada_eval_mortgage_domain_v1` | 28,990,269 | 166 | composite txn id | **Historical mortgage events** — every origination, refi, HELOC, release. 1961→present per-CLIP. |
| `entrada_eval_owner_transfer_domain_v1` | 24,051,375 | 114 | composite txn id | **Historical deeds** — every sale/transfer, buyer/seller names, REO/short-sale/investor/cash flags. |
| `entrada_eval_mortgage_market_analytics_domain_v1` | 39,772,305 | 197 | composite txn id | Pre-joined mortgage⊕sale analytics view. Useful for purchase analysis; duplicates the mortgage+owner_transfer columns. |

### Geography (data-driven coverage)
The app discovers Cotality coverage from `mip.gold.county_rollup` and
`mip.gold.zip_rollup`; the product does not hardcode a county count. The
current validation workspace resolves to the following counties, which is not
the same as full-state Cotality coverage:

| State | Discovered county | Property snapshots | With open liens | Avg 1st-pos rate | Avg C-LTV |
|---|---|---:|---:|---:|---:|
| IL | Cook | 1.86M | 1.13M | 4.75% | 49.2% |
| CA | Orange | 0.90M | 0.58M | 4.04% | 30.6% |
| FL | Broward | 0.76M | 0.40M | 4.71% | 36.5% |
| TX | Dallas | 0.75M | 0.41M | 4.72% | 45.3% |
| WA | King | 0.74M | 0.49M | 4.16% | 47.0% |
| CO | Douglas | 0.16M | 0.10M | 4.12% | 54.1% |

**Walkthrough-posture implication:** the narrative should follow the discovered
coverage shown by the app. State-level UI buckets are useful for executive
scanning, but any drill-down or public narration should name the counties
actually returned by the coverage service rather than implying full-state
Cotality coverage.

### Rate-cohort distribution (first-position mortgage by origination year)
| Cohort | Count | Median rate | Demo relevance |
|---|---:|---:|---|
| 2023–2026 | 565K | 6.0–6.7% | **In-the-money refi pool** (rates ~6% vs. market moving lower). Prime segment. |
| 2020–2022 | 1.22M | 2.8–3.0% | **Locked-in** — NOT refi candidates, but **retention-sensitive cash-out/HELOC pool** (huge appreciation, zero rate-incentive to refi, strong appetite for 2nd-lien equity tap). |
| 2013–2019 | 608K | 3.5–4.5% | Marginal refi pool depending on market rate. |
| 2003–2012 | 609K | 3.5–6.6% | Long-tail — refinance candidates if market drops further, retention-cash-out candidates. |

This cohort mix is **ideal** for Module 0: we can tell both the "refi-now" story (2023+ cohort) and the "retain with cash-out even at 7% market rate" story (2020–2022 cohort) off the same table.

### Signal coverage (what % of rows have each signal populated)
- `estimated_value_mktg` (AVM) & `estimated_equity`: **84%** (4.35M / 5.16M)
- `first_position_currently_assigned_lender_company_name` (current servicer): **59%** (3.03M). 263K cases where servicer ≠ originator → **recapture universe**.
- `owner_occupancy_code`: **94%** (4.87M). 68% owner-occupied.
- `owner_1_identifier` (Owner Link proxy): **84%** (4.34M), 3.44M distinct IDs — enables multi-property rollup.
- Absentee mailing (mailing addr ≠ situs): **35%** (1.80M) — strong investor/second-home signal.
- Corporate-owned (`owner_1_corporate_indicator='Y'`): **16%** (833K).

### Historical-event volumes (lifetime across the share)
11.4M refinances · 4.2M equity loans · 1.23M REO sales · 152K short sales · 160K investor purchases · 11.4M mortgage releases · 153K reverse mortgages · 29K active-stage foreclosures.

---

## 2. Module 0 segment-by-segment feasibility

From `CLAUDE.md`, the seven canonical borrower segments and what each needs:

| # | Segment | Needs | Can we build it? | Source |
|---|---|---|---|---|
| 1 | **Rate-&-term refi (in the money)** | Current rate ≥ market + 75bps, equity ≥ 15% | ✅ **Yes, with public PMMS** | voluntary_lien `first_position_mortgage_interest_rate`, `estimated_equity`, + FRED `MORTGAGE30US` |
| 2 | **Cash-out refi** | Rate-spread ≥ 0 AND ΔEquity ≥ threshold | ✅ **Yes** | voluntary_lien rate + `estimated_equity` + AVM appreciation via `estimated_value_mktg` vs. `purchase_amount` |
| 3 | **HELOC / 2nd-lien candidates** | High equity, clean 1st-lien, renovation/life-event intent | ⚠️ **Partial** — equity-only segment ships; renovation intent blocked without Building Permits | voluntary_lien equity + `COALESCE(second_position_mortgage_amount, 0) = 0`; **gap: Permits** |
| 4 | **Investor / multi-property** | Owner Link with ≥N properties OR corporate owner OR absentee mailing | ✅ **Yes** | property_v3 `owner_1_identifier` aggregation + `*_corporate_indicator` + mailing vs. situs |
| 5 | **Retention / recapture** | Current customer retention now; former-customer recapture from historical Summit-financed Owner Link relationships | ✅ **Current-customer side yes** / ✅ **Former-customer filter yes, subject to historical Summit coverage** | voluntary_lien `first_position_currently_assigned_lender_company_name` + governed `mip.ref.lender_dictionary`; `is_former_customer` is backed by owner-level historical Summit CLIP history with no current Summit-serviced lien |
| 6 | **Listed for sale (purchase mortgage)** | Active MLS listing | ❌ **Blocked** — not in current share, Cotality MLS required | **gap: MLS Listings** |
| 7 | **Distress / pre-foreclosure** | NOD/NTS filings, late-stage foreclosure | ⚠️ **Partial** — have `foreclosure_stage_code` snapshot (29K properties) + REO sale history (1.2M); **missing pre-NOD leading indicators** | property_v3 + owner_transfer; **nice-to-have: Pre-Foreclosure product** |

**Counting segments that ship today vs. require more data:**
- **Ship now:** Rate-&-term refi, Cash-out/equity, Investor/Multi-property, current-customer Retention, Distress-lite (current foreclosure stage + REO history only).
- **Blocked:** Listed-for-Sale (needs MLS), permit-driven HELOC intent (needs Building Permits).
- **Upgradeable (1):** Distress becomes much stronger with Pre-Foreclosure product.

---

## 3. Answer to Cotality's claim: "MLS and Building Permits round out the use-cases"

**Confirmed.** This is a correct and minimal request list for Module 0. Rationale per product:

### MLS Listings — **CRITICAL for segment 6**
- **Why:** "Listed for sale = purchase mortgage opportunity" is named in `CLAUDE.md` as a core segment. There is no proxy in the 5 shared tables — by definition, a pre-sale listing doesn't yet generate a deed or a mortgage event, so neither `owner_transfer_domain_v1` nor `mortgage_domain_v1` can see it.
- **What it adds to the demo:** the "purchase mortgage" talk-track branch (going from rate-hold funnels to move-up buyer funnels), DOM/price-reduction offer triggers, competitive landscape per metro.
- **Marketplace product:** **MLS Listings** ("80% of active listings nationally"). Also **MLS Market Analytics** for market-level KPIs on the landing page.

### Building Permits — **ELEVATES segment 3 from "equity-only" to "intent-driven"**
- **Why:** Segment 3 HELOC is explicitly "strong equity and/or recent permits/renovation signals" in `CLAUDE.md`. Today we can only score the equity half. Permits are the intent half — a pulled ADU/addition/pool permit means the homeowner is about to spend money and is actively shoppable for a HELOC.
- **What it adds to the demo:** turns the HELOC segment from a static "these 800K people have equity" list into a dynamic trigger list ("341 borrowers pulled a permit in the last 30 days — offer a HELOC before they use a credit card").
- **Marketplace product:** **Building Permits** ("permit data for 1,500+ areas").

**Verdict on Cotality's statement:** confirm, do not expand the Module 0 request beyond these two. Any more and we slow their legal/delivery process without corresponding demo value.

---

## 4. What else on the marketplace should we know about (for later modules, not Module 0)

From the screenshot, grouped by Module 0 necessity:

### Redundant with what we already have — **DO NOT request**
- **Property Characteristic Information on US Properties** — we have v3 already in the share.
- **Property Characteristic Information on US Properties (Sample)** — sample of above, already attached to the workspace as a separate share. Ignore.
- **Total Home Value – AVM** — voluntary_lien already carries `estimated_value_mktg` / `..._high` / `..._low` / `confidence_score_mktg`. This is the AVM output baked into the marketing view. Requesting the standalone AVM adds no Module 0 value.
- **Mortgage Transaction Data** / **Owner Transfer and Sales Data** / **Voluntary Lien Status** / **Loan Assignments** / **Loan Releases** — all rolled into our 5 shared tables already.
- **Mortgage Market Analytics** — we have v1 of this pre-joined view.

### Useful for Module 0 polish, low priority — optional asks
- **Home Price Index** and **HPI Forecast** — gives us per-CBSA appreciation in the Geography drill-down. **Public FHFA HPI from FRED is good enough for the customer walkthrough.** Request only if the walkthrough needs forward-looking HPI ("prices up 4% next 12mo in Denver, offer now").
- **Pre-Foreclosure** — upgrades segment 7 from "have foreclosure stage" to "have NOD/NTS filings" → earlier distress detection. Not on the critical path; post-demo.
- **Tax Liens**, **Judgments**, **HOA and Mechanics Liens** — involuntary-lien products. Useful as a *filter-out* signal (skip borrowers in distress for outreach) but not required for the segment math. Post-demo.

### Module 1+ territory (retention, insurance cross-sell, climate, etc.) — **DO NOT request yet**
Per `CLAUDE.md` negative prompting ("Do not overbuild Modules 1–4 before Module 0 is stable"), defer:
- Climate Risk Analytics, Neighborhood Crime/Schools/Demographics/Employment/Real Estate, Propensity Scores, Market Risk Indicators, Rent Amount Model, Insurance Marketing Database, Historical Tax Assessment, Housing Analytics (Market/Rental/Listing Trends), Solar Contracts, ParcelPoint, Building Detail, Neighborhood Schools.

### CLIP MCP — request, but separately and later
- **Why defer:** CLIP MCP's job is to **match external/third-party property records to a CLIP**. The 5 shared tables are **already CLIP-keyed**. For the Module 0 walkthrough, we don't need to resolve inbound property records against a master ID — every record in our warehouse already has one.
- **When to request:** when we hook up Summit Mortgage's (or any customer's) portfolio CSV as the "bring your portfolio" Module 0 flow. That's post-evaluation delivery work, not the initial walkthrough.
- **Governance note:** MCP gives the agents tool-use against Cotality's API, which is a different security review path than reading a UC share. Plan this as its own ask with Cotality's legal + our governance-security-reviewer subagent in the loop.

---

## 5. Public datasets required

### Required for Module 0 (already integrated) — **1 dataset**
- **Freddie Mac PMMS 30-year fixed rate** via FRED series `MORTGAGE30US`.
  - Why: `fn_in_the_money` needs `rate_spread_bps = borrower_rate - market_rate`. Without a market rate series we cannot compute the spread.
  - Cadence: weekly, small CSV.
  - Integration: Databricks job writes the FRED CSV/seed to `mip.silver.market_rates_weekly`, lagged by one day.
  - License: FRED is free for redistribution.

### Optional / nice-to-have for polish
- **FRED `MORTGAGE15US`** — for 15-year offer lane in `fn_next_best_offer`.
- **FHFA HPI (state/CBSA)** — per-geography appreciation in the Geography drill-down.
- **HMDA public LAR (FFIEC)** — market-share benchmarks for the "we saw 1,241 competitor originations last quarter" retention narrative. Heavy dataset; only if the demo needs it.
- **Census ACS (via open catalog)** — geographic rollups on the landing page. Not scoring-critical.

### Not needed
- Weather, climate, school data — Module 1+ territory.

---

## 6. Mapping — share → silver → gold

This is the implemented bridge from the Cotality share to the `mip` silver/gold contract (per `CLAUDE.md`). Raw names and street addresses are read only where needed for hashing/derivation and are not persisted into silver/gold UI surfaces.

### Silver (1:1 typed lift with minimal filtering)
```
mip.silver.property_master
  <- SELECT clip, fips_county_code, situs_city, situs_state, ZIP5(situs_zip_code),
           situs_core_based_statistical_area_cbsa, block_level_latitude, block_level_longitude,
           sha2(owner_1_full_name || salt) AS owner_name_hash,
           owner_1_identifier, owner_1_corporate_indicator, owner_occupancy_code,
           mailing_city, mailing_state,
           calculated_total_value, assessed_total_value, total_tax_amount, tax_year,
           foreclosure_stage_code, last_foreclosure_transaction_date,
           year_built, total_living_area_square_feet_all_bldgs,
           total_number_of_bedrooms_all_bldgs, total_number_of_bathrooms
     FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
     WHERE situs_state IS NOT NULL     -- source coverage is data-driven

mip.silver.lien_current
  <- SELECT clip, owner_occupancy_code,
           situs_state, ZIP5(situs_zip_code),
           total_number_of_open_mortgage_liens, total_amount_of_open_mortgage_liens,
           estimated_value_mktg, estimated_value_high_mktg, estimated_value_low_mktg,
           confidence_score_mktg, value_as_of_date_mktg,
           estimated_equity, estimated_combined_ltv_loan_to_value,
           purchase_amount, purchase_recording_date, purchase_combined_ltv_loan_to_value,
           first_position_mortgage_date, first_position_mortgage_recorded_document_year,
           first_position_mortgage_amount, first_position_mortgage_interest_rate,
           first_position_mortgage_interest_rate_type_code,
           first_position_mortgage_term, first_position_mortgage_loan_type_code,
           first_position_mortgage_purpose_code,
           first_position_mortgage_ltv_loan_to_value,
           first_position_lender_company_name, first_position_currently_assigned_lender_company_name,
           second_position_mortgage_amount, second_position_mortgage_interest_rate,
           second_position_mortgage_purpose_code, second_position_lender_company_name
     FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
     WHERE situs_state IS NOT NULL

mip.silver.mortgage_events
  <- SELECT clip, mortgage_composite_transaction_id, mortgage_derived_date, mortgage_amount,
           mortgage_interest_rate_cascade, mortgage_purpose_code, mortgage_loan_type_code,
           refinance_loan_indicator, equity_loan_indicator, reverse_mortgage_indicator,
           lender_company_name, mortgage_release_date, mortgage_status_indicator,
           borrower_1_identifier
     FROM cotality_mortgage_data.corelogic.entrada_eval_mortgage_domain_v1
     WHERE deed_situs_state_static IS NOT NULL

mip.silver.owner_transfer_events
  <- SELECT clip, sale_derived_date, sale_amount, sale_type_code,
           cash_purchase_indicator, investor_purchase_indicator, foreclosure_reo_indicator,
           short_sale_indicator, new_construction_indicator, resale_indicator, interfamily_related_indicator,
           buyer_1_full_name, buyer_1_corporate_indicator, buyer_1_identifier,
           buyer_mailing_state
     FROM cotality_mortgage_data.corelogic.entrada_eval_owner_transfer_domain_v1
     WHERE deed_situs_state_static IS NOT NULL

mip.silver.market_rates_weekly
  <- FRED MORTGAGE30US CSV (weekly job)
```

### Gold (scoring-ready, matches existing UDF signatures)
```
mip.gold.property_owner_bridge   -- Owner Link rollup (count properties per owner_1_identifier)
mip.gold.borrower_360            -- one row per CLIP: joined lien_current + property + owner + latest market rate
mip.gold.lead_scores             -- fn_lead_score(economic, intent, fit, relationship, evidence) per CLIP
mip.gold.evidence_events         -- timeline per CLIP: refis, payoffs, sales, foreclosure stage, rate changes
mip.gold.lead_population         -- filtered ranked top-N for demo surface
```

### Component score definitions (feeds `fn_lead_score`)
- **economic_incentive (0.35):** continuous blend from rate_spread_bps and equity_pct using the frozen `fn_rate_spread` / `fn_in_the_money` contract.
- **intent_trigger (0.30):** today, from recent refi/payoff events, competitor-lien signal, investor signal, rate drift, equity proxy, and current-customer bump. **Upgrades dramatically with Permits + MLS.**
- **fit (0.15):** from loan_type_code, owner_occupancy_code, property type, geography match to lender LO coverage.
- **relationship (0.10):** from current-servicer Summit relationship, former-customer relationship, competitor-lien flag, owner-link breadth, and owner-level historical Summit distinct-CLIP count.
- **evidence (0.10):** count of distinct Cotality source rows plus bounded second-position evidence tail (used for the evidence-drawer confidence UI).

---

## 7. Remaining implementation and data-source work

1. **Keep the implemented UC path refreshed**: FRED `MORTGAGE30US`, silver lifts, gold CTAS, metric views, and `sql/_rendered` output are the repo-backed data path.
2. **Request MLS Listings + Building Permits from Cotality**: these unblock the `listed` segment and convert HELOC from equity-only to intent-driven.
3. **Refresh and validate former-customer recapture live**: `is_former_customer` now uses a distinct historical-Summit Owner Link predicate, but live UC counts are only certified after the next gold refresh and parity check.
4. **Optional post-demo enrichment**: HPI forecast / Pre-Foreclosure / CLIP MCP remain later asks, not prerequisites for Module 0 truth.

---

## 8. Prioritized request list to Cotality

| Priority | Product | Why | Blocks what |
|---|---|---|---|
| **P0** | **MLS Listings** | Only source for "listed for sale" signal | Segment 6 (purchase mortgage opportunity) |
| **P0** | **Building Permits** | Only source for renovation-intent trigger | Segment 3 upgrade (HELOC intent, not just equity) |
| P2 | HPI Forecast | Per-CBSA 12mo appreciation forecast for Geography drill-down | Nice-to-have polish; public FHFA HPI covers the must-have |
| P2 | Pre-Foreclosure | NOD/NTS leading indicator | Segment 7 upgrade; we have enough for demo without it |
| P3 | CLIP MCP | Inbound property-record resolution | Post-walkthrough, for "bring your portfolio" flow only |
| — | Everything else on the marketplace (AVM, HPI, Climate, Neighborhood *, Propensity Scores, Insurance, Historical Tax, MLS Analytics, Loan Assignments/Releases, Rent Amount Model, Mortgage Market Analytics, Total Home Value, Voluntary Lien Status, Property Characteristic Information, Pre-Foreclosure alt products) | Either redundant with existing share or out of Module 0 scope | — |

---

## 9. Appendix — probe queries used for this analysis

Run against warehouse `cfa0e10eed4f00a5` (sidewinder) on profile `DEFAULT`, Apr 21 2026.

Row counts, geographic coverage, cohort distribution, signal-completeness, Owner Link cardinality, distress/refi event volumes — all reproducible from the SQL files at `/tmp/*.sql` in the investigation transcript. Summary:

- 5 tables · 103M total rows · CLIP is clean primary key on voluntary_lien (1:1) and property_v3 (1:1).
- Current coverage discovery resolves to the counties listed above, totaling
  5.16M property snapshots in this validation workspace.
- 84% AVM coverage, 94% occupancy coverage, 59% current-servicer coverage, 83% Owner Link coverage.
- 565K borrowers in the 2023+ high-rate cohort (active in-the-money pool).
- 1.22M borrowers in the 2020–2022 sub-3% cohort (retention + cash-out pool).
- 263K servicer-transferred loans (recapture candidates).
- 833K corporate owners + 1.80M absentee mailings (investor segment).
