# E2E live verification — 2026-04-23

> **Internal validation artifact. Not public release collateral.**

Base URL: `https://mip-app-2543889327043640.aws.databricksapps.com`  
Auth: `databricks auth token -p DEFAULT` (OAuth bearer, skyler@entrada.ai)  
Synthetic test-id prefix: `B-TEST-*`

## Endpoint results

| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |
| --- | --- | ---: | ---: | :---: | --- |
| health | `GET /api/health` | 200 | 493 | yes | keys/len: actor_cache_key, circuit_breakers, dependencies, mode, status |
| config.options | `GET /api/config/options` | 200 | 1923 | yes | keys/len: equity_thresholds, geographies, geographies_status, geography_scope, lender_name, lender_relationships, lien_status, occupancy |
| portfolio.unfiltered | `POST /api/portfolio/preview` | 200 | 525 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.all_states | `POST /api/portfolio/preview` | 200 | 533 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.all_states.owner.25pct | `POST /api/portfolio/preview` | 200 | 523 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| segments | `GET /api/segments` | 200 | 1051 | yes | keys/len: [array len=6] |
| leads.all | `GET /api/leads` | 200 | 1959 | yes | keys/len: [array len=500] |
| leads.itm | `GET /api/leads?segment=itm` | 200 | 5372 | yes | keys/len: [array len=500] |
| borrower.detail | `GET /api/borrowers/B-0QFTCDS92FP00` | 200 | 2230 | yes | keys/len: aging_days, approval_status, approved_at, assigned_at, assigned_to_email, assigned_to_label, assignment_expires_at, avm_value |
| borrower.evidence | `GET /api/borrowers/B-0QFTCDS92FP00/evidence` | 200 | 819 | yes | keys/len: [array len=9] |
| offers.recommend | `POST /api/offers/recommend` | 200 | 1014 | yes | keys/len: alternatives, borrower_id, confidence, evidence_ids, offer_code, offer_type, product_label, rationale |
| outreach.draft | `POST /api/outreach/draft` | 200 | 922 | yes | keys/len: body, borrower_id, channel, disclosure_state, disclosure_version, marketing_eligible, offer_code, status |
| outreach.approve.real | `POST /api/outreach/approve` | 200 | 1826 | yes | keys/len: approval_id, approved, assigned_to_email, audit_event_id, follow_up_at |
| outreach.reject.real | `POST /api/outreach/reject` | 200 | 1673 | yes | keys/len: approval_id, audit_event_id, rejected |
| outreach.approve.unknown_404 | `POST /api/outreach/approve` | 404 | 947 | yes |  |
| outreach.reject.unknown_404 | `POST /api/outreach/reject` | 404 | 876 | yes |  |
| audit.events | `GET /api/audit/events?limit=10` | 200 | 1100 | yes | keys/len: [array len=10] |
| genie.message | `POST /api/genie/message` | 200 | 1297 | yes | keys/len: actions, answer, conversation_id, elapsed_ms, follow_up_questions, message_id, metric_value, proof |
| admin.rules | `GET /api/admin/rules` | 200 | 1287 | yes | keys/len: offer_rules_version, rules_edited_at, thresholds |
| admin.sources | `GET /api/admin/sources` | 200 | 924 | yes | keys/len: [array len=21] |
| geo.state_rollups | `GET /api/geo/state-rollups` | 200 | 1855 | yes | keys/len: rollups, snapshot_date |
| geo.state_rollups.filtered | `GET /api/geo/state-rollups?segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 810 | yes | keys/len: rollups, snapshot_date |
| geo.county_rollups.filtered | `GET /api/geo/county-rollups?state=IL&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 1695 | yes | keys/len: rollups, scope_note, snapshot_date, state |
| geo.zip_rollups.filtered | `GET /api/geo/zip-rollups?county_fips=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 981 | yes | keys/len: fips_5, rollups, snapshot_date |
| leads.filtered_geo | `GET /api/leads?state=IL&county=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 6033 | yes | keys/len: [array len=500] |

## Clean payload samples

### health — `GET /api/health`

```json
{
  "status": "ok",
  "mode": "live",
  "dependencies": {
    "warehouse": "up",
    "lakebase": "up",
    "genie": "up"
  },
  "circuit_breakers": {
    "warehouse": "closed",
    "lakebase": "closed",
    "genie": "closed"
  },
  "actor_cache_key": "actor_01cd5c657e3bbfc5"
}
```
### config.options — `GET /api/config/options`

```json
{
  "lender_name": "Summit Mortgage",
  "rum_enabled": false,
  "geographies": [
    "All available states",
    "Illinois",
    "California",
    "Florida",
    "Texas",
    "Washington",
    "Colorado"
  ],
  "geographies_status": "live",
  "geography_scope": {
    "state_count": 6,
    "county_count": 6,
    "zip_count": 677,
    "snapshot_date": "2026-06-14",
    "source_table": "mip.gold.county_rollup",
    "scope_label": "Cotality data coverage: available counties across available states",
    "counties": [
      {
        "state": "CA",
        "fips_5": "06059",
        "county_name": "Orange",
        "addressable_borrowers": 900371
      },
      {
        "state": "CO",
        "fips_5": "08035",
        "county_name": "Douglas",
        "addressable_borrowers": 163557
      },
      {
        "state": "FL",
        "fips_5": "12011",
        "county_name": "Broward",
        "addressable_borrowers": 752572
      },
      {
        "state": "IL",
        "fips_5": "17031",
        "county_name": "Cook",
        "addressable_borrowers": 1851040
      },
      {
        "state": "TX",
        "fips_5": "48113",
        "county_name": "Dallas",
        "addressable_borrowers": 750962
      },
      {
        "state": "WA",
        "fips_5": "53033",
        "county_name": "King",
        "addressable_borrowers": 737682
      }
    ]
  },
  "occupancy": [
    "Owner-occupied",
    "Non-owner-occupied",
    "All"
  ],
  "lien_status": [
    "Open 1st lien",
    "Open HELOC",
    "Free & clear",
    "Any"
  ],
  "lender_relationships": [
    "All",
    "Current customer",
    "Former customer",
    "Competitor customer"
  ],
  "products": [
    "All products",
    "Refi",
    "HELOC",
    "Cash-out",
    "Purchase",
    "Retention"
  ],
  "equity_thresholds": [
    "\u2265 15%",
    "\u2265 25%",
    "\u2265 40%",
    "Any"
  ],
  "target_lender_refs": [
    "All",
    "Summit Mortgage",
    "Competitor Other",
    "Competitor A",
    "Competitor B",
    "Competitor G",
    "Competitor C",
    "Competitor F",
    "Competitor D",
    "Competitor H",
    "Competitor I",
    "Competitor E",
    "Competitor J",
    "Competitor O",
    "Competitor M",
    "Competitor L",
    "Competitor T",
    "Competitor V",
    "Competitor R",
    "Competitor Q",
    "Competitor N",
    "Competitor S"
  ],
  "target_lender_refs_status": "live"
}
```
### portfolio.unfiltered — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 233420,
  "high_intent_leads": 5136,
  "top_tier_opportunities": 364,
  "offers_recommended": 202661,
  "avg_score": 39,
  "trends": {},
  "trend_status": "not_applicable",
  "trend_note": "Trend lines are hidden for this filtered build because daily snapshots are not stored at this custom filter grain.",
  "data_refreshed_at": "2026-06-14T06:47:14.081000Z",
  "approved_count": null,
  "in_outreach_count": null,
  "day_zero": false
}
```
### portfolio.all_states — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 233420,
  "high_intent_leads": 5136,
  "top_tier_opportunities": 364,
  "offers_recommended": 202661,
  "avg_score": 39,
  "trends": {},
  "trend_status": "not_applicable",
  "trend_note": "Trend lines are hidden for this filtered build because daily snapshots are not stored at this custom filter grain.",
  "data_refreshed_at": "2026-06-14T06:47:14.081000Z",
  "approved_count": null,
  "in_outreach_count": null,
  "day_zero": false
}
```
### portfolio.all_states.owner.25pct — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 129903,
  "high_intent_leads": 3740,
  "top_tier_opportunities": 277,
  "offers_recommended": 129903,
  "avg_score": 43,
  "trends": {},
  "trend_status": "not_applicable",
  "trend_note": "Trend lines are hidden for this filtered build because daily snapshots are not stored at this custom filter grain.",
  "data_refreshed_at": "2026-06-14T06:47:14.081000Z",
  "approved_count": null,
  "in_outreach_count": null,
  "day_zero": false
}
```
### segments — `GET /api/segments`

```json
{
  "code": "itm",
  "name": "In the Money",
  "count": 111726,
  "delta": "+0%",
  "avg_score": 62,
  "description": "Lien rate >= 75 bps above par and equity >= 15%.",
  "color": "#5CE1E6"
}
```
### leads.all — `GET /api/leads`

```json
{
  "borrower_id": "B-0QFTCDS92FP00",
  "display_name": "Owner bf57bbb5",
  "city": "EVANSTON",
  "state": "IL",
  "zip": "60201",
  "clip": "clip_ref_05f0d03cdc07",
  "segment_codes": [
    "itm",
    "listed",
    "permit",
    "investor",
    "equity"
  ],
  "equity_estimate": 518844,
  "rate_spread_bps": 268,
  "opportunity_score": 90,
  "confidence": 90,
  "recommended_offer_code": "purchase",
  "recommended_offer": "Purchase Mortgage",
  "why_now": "The home is actively listed -- a purchase mortgage on the next home is the right offer.",
  "evidence_ids": [
    "ev-e135282b27bb",
    "ev-86f4ae860c47",
    "ev-21ae23be0cea"
  ],
  "approval_status": "pending",
  "outreach_status": "none",
  "approved_at": null,
  "outreach_at": null,
  "is_owner_occupied": true,
  "is_investor": true,
  "is_current_customer": false,
  "is_former_customer": false,
  "is_competitor_lien": true,
  "related_property_count": 9,
  "current_lien_balance": 168095,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": true,
  "listing_status_category": "A",
  "listing_status_description": "Active",
  "listing_date": "2026-04-17",
  "listing_status_date": "2026-05-20",
  "listing_price": 799000,
  "listing_days_on_market": 48,
  "listing_service": "MRED",
  "heloc_propensity_score": 701,
  "heloc_propensity_run_date": "2026-05-08",
  "has_heloc_propensity_trigger": true,
  "refi_propensity_score": 702,
  "refi_propensity_run_date": "2026-05-08",
  "has_refi_propensity_trigger": true,
  "current_lender_ref": "Competitor O",
  "marketing_eligible": true,
  "consent_status": "opt_in",
  "suppression_reason": null,
  "last_touch_at": "2026-05-14T00:00:00Z",
  "eligible_recontact_at": null,
  "assigned_to_email": null,
  "assigned_to_label": null,
  "assigned_at": null,
  "assignment_expires_at": null,
  "latest_disposition_outcome": null,
  "latest_disposition_at": null,
  "latest_callback_at": null,
  "aging_days": null
}
```
### leads.itm — `GET /api/leads?segment=itm`

```json
{
  "borrower_id": "B-0QFTCDS92FP00",
  "display_name": "Owner bf57bbb5",
  "city": "EVANSTON",
  "state": "IL",
  "zip": "60201",
  "clip": "clip_ref_05f0d03cdc07",
  "segment_codes": [
    "itm",
    "listed",
    "permit",
    "investor",
    "equity"
  ],
  "equity_estimate": 518844,
  "rate_spread_bps": 268,
  "opportunity_score": 90,
  "confidence": 90,
  "recommended_offer_code": "purchase",
  "recommended_offer": "Purchase Mortgage",
  "why_now": "The home is actively listed -- a purchase mortgage on the next home is the right offer.",
  "evidence_ids": [
    "ev-e135282b27bb",
    "ev-86f4ae860c47",
    "ev-21ae23be0cea"
  ],
  "approval_status": "pending",
  "outreach_status": "none",
  "approved_at": null,
  "outreach_at": null,
  "is_owner_occupied": true,
  "is_investor": true,
  "is_current_customer": false,
  "is_former_customer": false,
  "is_competitor_lien": true,
  "related_property_count": 9,
  "current_lien_balance": 168095,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": true,
  "listing_status_category": "A",
  "listing_status_description": "Active",
  "listing_date": "2026-04-17",
  "listing_status_date": "2026-05-20",
  "listing_price": 799000,
  "listing_days_on_market": 48,
  "listing_service": "MRED",
  "heloc_propensity_score": 701,
  "heloc_propensity_run_date": "2026-05-08",
  "has_heloc_propensity_trigger": true,
  "refi_propensity_score": 702,
  "refi_propensity_run_date": "2026-05-08",
  "has_refi_propensity_trigger": true,
  "current_lender_ref": "Competitor O",
  "marketing_eligible": true,
  "consent_status": "opt_in",
  "suppression_reason": null,
  "last_touch_at": "2026-05-14T00:00:00Z",
  "eligible_recontact_at": null,
  "assigned_to_email": null,
  "assigned_to_label": null,
  "assigned_at": null,
  "assignment_expires_at": null,
  "latest_disposition_outcome": null,
  "latest_disposition_at": null,
  "latest_callback_at": null,
  "aging_days": null
}
```
### borrower.detail — `GET /api/borrowers/B-0QFTCDS92FP00`

```json
{
  "borrower_id": "B-0QFTCDS92FP00",
  "display_name": "Owner bf57bbb5",
  "city": "EVANSTON",
  "state": "IL",
  "zip": "60201",
  "clip": "",
  "segment_codes": [
    "itm",
    "listed",
    "permit",
    "investor",
    "equity"
  ],
  "equity_estimate": 518844,
  "rate_spread_bps": 268,
  "opportunity_score": 90,
  "confidence": 90,
  "recommended_offer_code": "purchase",
  "recommended_offer": "Purchase Mortgage",
  "why_now": "The home is actively listed -- a purchase mortgage on the next home is the right offer.",
  "evidence_ids": [
    "ev-e135282b27bb",
    "ev-86f4ae860c47",
    "ev-21ae23be0cea"
  ],
  "approval_status": "approved",
  "outreach_status": "queued",
  "approved_at": "2026-06-14T06:48:07.431713Z",
  "outreach_at": null,
  "is_owner_occupied": true,
  "is_investor": true,
  "is_current_customer": false,
  "is_former_customer": false,
  "is_competitor_lien": true,
  "related_property_count": 9,
  "current_lien_balance": 168095,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": true,
  "listing_status_category": "A",
  "listing_status_description": "Active",
  "listing_date": "2026-04-17",
  "listing_status_date": "2026-05-20",
  "listing_price": 799000,
  "listing_days_on_market": 48,
  "listing_service": "MRED",
  "heloc_propensity_score": 701,
  "heloc_propensity_run_date": "2026-05-08",
  "has_heloc_propensity_trigger": true,
  "refi_propensity_score": 702,
  "refi_propensity_run_date": "2026-05-08",
  "has_refi_propensity_trigger": true,
  "current_lender_ref": "Competitor O",
  "marketing_eligible": true,
  "consent_status": "opt_in",
  "suppression_reason": null,
  "last_touch_at": "2026-05-14T00:00:00Z",
  "eligible_recontact_at": null,
  "assigned_to_email": null,
  "assigned_to_label": null,
  "assigned_at": null,
  "assignment_expires_at": null,
  "latest_disposition_outcome": null,
  "latest_disposition_at": null,
  "latest_callback_at": null,
  "aging_days": 0,
  "clip_id": "clip_ref_05f0d03cdc07",
  "owner_link_id": "owner_link_ref_40d918d2fd65",
  "subject_property": "Synthetic property \u00b7 EVANSTON, IL 60201",
  "avm_value": 686939,
  "current_rate": 9.2,
  "ltv": 24,
  "situs_cbsa_code": "16980",
  "first_pos_loan_type": "CNV",
  "is_absentee": false,
  "is_corporate_owner": false,
  "has_first_party_relationship": true,
  "first_party_relationship_depth": 4,
  "first_party_recent_interactions": 1,
  "first_party_recent_application": true,
  "first_party_synthetic_demo": true,
  "trigger_timeline": [
    {
      "evidence_id": "ev-e135282b27bb",
      "source_product": "MLS Listings",
      "source_table": "mip.silver.listing_activity",
      "signal_type": "listing",
      "signal_value": "Active",
      "display_text": "Current MLS status is Active at $799000 list price after 48 days on market.",
      "confidence": 0.94,
      "timestamp": "2026-05-20"
    },
    {
      "evidence_id": "ev-86f4ae860c47",
      "source_product": "Voluntary Lien + Market Rates",
      "source_table": "mip.silver.lien_current",
      "signal_type": "rate_spread",
      "signal_value": "+268 bps",
      "display_text": "Current lien rate is 268 bps vs. par.",
      "confidence": 0.92,
      "timestamp": "2026-06-14 04:10:38.849"
    },
    {
      "evidence_id": "ev-21ae23be0cea",
      "source_product": "AVM",
      "source_table": "mip.silver.lien_current",
      "signal_type": "equity",
      "signal_value": "$519K",
      "display_text": "AVM-backed equity estimate.",
      "confidence": 0.8,
      "timestamp": "2026-06-14"
    }
  ],
  "evidence_events": [
    {
      "evidence_id": "ev-e135282b27bb",
      "source_product": "MLS Listings",
      "source_table": "mip.silver.listing_activity",
      "signal_type": "listing",
      "signal_value": "Active",
      "display_text": "Current MLS status is Active at $799000 list price after 48 days on market.",
      "confidence": 0.94,
      "timestamp": "2026-05-20"
    },
    {
      "evidence_id": "ev-86f4ae860c47",
      "source_product": "Voluntary Lien + Market Rates",
      "source_table": "mip.silver.lien_current",
      "signal_type": "rate_spread",
      "signal_value": "+268 bps",
      "display_text": "Current lien rate is 268 bps vs. par.",
      "confidence": 0.92,
      "timestamp": "2026-06-14 04:10:38.849"
    },
    {
      "evidence_id": "ev-21ae23be0cea",
      "source_product": "AVM",
      "source_table": "mip.silver.lien_current",
      "signal_type": "equity",
      "signal_value": "$519K",
      "display_text": "AVM-backed equity estimate.",
      "confidence": 0.8,
      "timestamp": "2026-06-14"
    },
    {
      "evidence_id": "ev-87925bd95d47",
      "source_product": "Market Rates",
      "source_table": "mip.silver.market_rates_weekly",
      "signal_type": "market_trend",
      "signal_value": "6.52% par",
      "display_text": "Latest MORTGAGE30US market rate (FRED observation week 2026-06-08).",
      "confidence": 0.92,
      "timestamp": "2026-06-08"
    },
    {
      "evidence_id": "ev-4bc43aefb24a",
      "source_product": "HELOC Propensity",
      "source_table": "mip.silver.heloc_propensity",
      "signal_type": "heloc_propensity",
      "signal_value": "701/999",
      "display_text": "Cotality HELOC propensity score is 701 out of 999.",
      "confidence": 0.701,
      "timestamp": "2026-05-08"
    },
    {
      "evidence_id": "ev-643d948f6a78",
      "source_product": "Refi Propensity",
      "source_table": "mip.silver.refi_propensity",
      "signal_type": "refi_propensity",
      "signal_value": "702/999",
      "display_text": "Cotality refinance propensity score is 702 out of 999.",
      "confidence": 0.702,
      "timestamp": "2026-05-08"
    },
    {
      "evidence_id": "ev-088b20732e5e",
      "source_product": "Voluntary Lien",
      "source_table": "mip.silver.lien_current",
      "signal_type": "competitor_lien",
      "signal_value": "Competitor Other",
      "display_text": "Current servicer is not the lender of record.",
      "confidence": 0.89,
      "timestamp": "2026-06-14 04:10:38.849"
    },
    {
      "evidence_id": "ev-9e9a1982ca33",
      "source_product": "Owner Link",
      "source_table": "mip.gold.property_owner_bridge",
      "signal_type": "multi_property",
      "signal_value": "9 properties",
      "display_text": "Owner Link identifies related properties under the same entity.",
      "confidence": 0.85,
      "timestamp": "2026-06-14 06:16:04.208758"
    },
    {
      "evidence_id": "ev-b8d972aeb71a",
      "source_product": "Mortgage Domain",
      "source_table": "mip.silver.mortgage_events",
      "signal_type": "recent_payoff",
      "signal_value": "2025-10-21",
      "display_text": "Mortgage release recorded within the last 12 months.",
      "confidence": 0.89,
      "timestamp": "2025-10-21"
    }
  ],
  "why_panel": {
    "rate_spread_bps": 268,
    "market_rate": 0.0652,
    "equity_pct": 76,
    "in_the_money": true,
    "in_the_money_reason": "Current rate sits well above market rates and the home has 76% equity -- both refinance triggers are met.",
    "min_spread_bps": 75,
    "min_equity_pct": 15,
    "sources": [
      "mip.gold.fn_rate_spread",
      "mip.gold.fn_in_the_money",
      "mip.gold.borrower_dossier"
    ],
    "source_labels": [
      {
        "name": "mip.gold.fn_rate_spread",
        "display_label": "Market rate comparison"
      },
      {
        "name": "mip.gold.fn_in_the_money",
        "display_label": "In-the-money rule"
      },
      {
        "name": "mip.gold.borrower_dossier",
        "display_label": "Borrower dossier"
      }
    ]
  }
}
```
### borrower.evidence — `GET /api/borrowers/B-0QFTCDS92FP00/evidence`

```json
{
  "evidence_id": "ev-e135282b27bb",
  "source_product": "MLS Listings",
  "source_table": "mip.silver.listing_activity",
  "signal_type": "listing",
  "signal_value": "Active",
  "display_text": "Current MLS status is Active at $799000 list price after 48 days on market.",
  "confidence": 0.94,
  "timestamp": "2026-05-20"
}
```
### offers.recommend — `POST /api/offers/recommend`

```json
{
  "borrower_id": "B-0QFTCDS92FP00",
  "offer_code": "purchase",
  "offer_type": "purchase",
  "product_label": "Purchase Mortgage",
  "confidence": 90,
  "rationale": "Home is actively listed for sale -- present a purchase mortgage on the next home before the current lien pays off at close.",
  "evidence_ids": [
    "ev-e135282b27bb",
    "ev-86f4ae860c47",
    "ev-21ae23be0cea"
  ],
  "sources": [
    "mip.gold.fn_next_best_offer",
    "mip.silver.listing_activity",
    "mip.gold.fn_lead_score"
  ],
  "source_labels": [
    {
      "name": "mip.gold.fn_next_best_offer",
      "display_label": "Next-best-offer model"
    },
    {
      "name": "mip.silver.listing_activity",
      "display_label": "MLS listing activity"
    },
    {
      "name": "mip.gold.fn_lead_score",
      "display_label": "Lead score model"
    }
  ],
  "alternatives": [
    {
      "offer_code": "refi_plus_heloc",
      "product_label": "Refinance + HELOC",
      "reason_not_chosen": "Active listing \u2014 the current lien is about to be paid off at close; refi loses to purchase."
    }
  ],
  "thresholds_applied": {
    "min_spread_bps": 75,
    "min_equity_pct": 15,
    "heloc_equity_min_pct": 35,
    "cashout_equity_min_pct": 25,
    "retention_min_spread_bps": 50
  }
}
```
### outreach.draft — `POST /api/outreach/draft`

```json
{
  "borrower_id": "B-0QFTCDS92FP00",
  "offer_code": "purchase",
  "channel": "email",
  "subject": "A Purchase Mortgage review for your property",
  "body": "Hello,\n\nPublic-record signals in EVANSTON, IL point to your current mortgage with another servicer, which may make Purchase Mortgage timely. The home is actively listed -- a purchase mortgage on the next home is the right offer.\n\nReply YES and a licensed loan officer will follow up -- no obligation.\n\nThis draft is for human review only; no outreach has been sent.\n\nSummit Mortgage, NMLS #123456. Equal Housing Lender. This is not a commitment to lend. Terms subject to credit, collateral, and underwriting approval. To opt out of marketing, reply unsubscribe or contact Summit Mortgage at its governed compliance address.",
  "status": "draft",
  "disclosure_version": "summit-demo-2026-05-v1",
  "disclosure_state": "_ALL",
  "marketing_eligible": true
}
```
### outreach.approve.real — `POST /api/outreach/approve`

```json
{
  "approved": true,
  "approval_id": "4602e7cc-f994-4f91-b2e4-a1ecad69c8b2",
  "audit_event_id": "b1f79ac8-f79e-49c5-b903-f912aa3d4779",
  "assigned_to_email": null,
  "follow_up_at": null
}
```
### outreach.reject.real — `POST /api/outreach/reject`

```json
{
  "rejected": true,
  "approval_id": "9d7605c0-9b5b-4c00-948b-cf263321b406",
  "audit_event_id": "2b01f673-353e-417c-bafd-4043b948b240"
}
```
### audit.events — `GET /api/audit/events?limit=10`

```json
{
  "event_id": "4d0cc7bc-d60b-4d55-81c1-4b63aca55072",
  "actor": "skyler@entrada.ai",
  "action": "draft_outreach",
  "entity_type": "outreach_draft",
  "entity_id": "B-0QFTCDS92FP00",
  "payload_json": {
    "channel": "email",
    "offer_code": "purchase",
    "campaign_id": null,
    "variant_name": null,
    "last_touch_at": "2026-05-14T00:00:00+00:00",
    "consent_status": "opt_in",
    "disclosure_state": "_ALL",
    "disclosure_channel": "email",
    "disclosure_version": "summit-demo-2026-05-v1",
    "marketing_eligible": true,
    "suppression_reason": null,
    "eligible_recontact_at": null
  },
  "evidence_ids": [],
  "created_at": "2026-06-14T06:49:22.765582+00:00",
  "event_type": "DRAFT_OUTREACH",
  "subject_clip": "clip_ref_05f0d03cdc07",
  "subject_segment": null,
  "request_id": null,
  "correlation_id": "4260100a92874dfa85ed0976d3a721fc"
}
```
### genie.message — `POST /api/genie/message`

```json
{
  "conversation_id": "",
  "question": "How many borrowers are currently in-the-money?",
  "answer": "There are 111,726 borrowers currently in-the-money. This is a unique borrower count from mip.gold.borrower_360 at the gold borrower grain, so multi-segment borrowers are counted once.",
  "source": "trusted_sql",
  "trusted_assets": [
    "mip.gold.borrower_360"
  ],
  "message_id": "trusted-sql-ae293f4c7f08ec6c",
  "elapsed_ms": 0,
  "question_hash": "ae293f4c7f08ec6c",
  "sql_query": "SELECT COUNT(*) AS in_the_money_borrowers\n     , MAX(refreshed_at) AS refreshed_at\nFROM mip.gold.borrower_360\nWHERE in_the_money = TRUE",
  "row_count": 1,
  "proof": {
    "sql_query": "SELECT COUNT(*) AS in_the_money_borrowers\n     , MAX(refreshed_at) AS refreshed_at\nFROM mip.gold.borrower_360\nWHERE in_the_money = TRUE",
    "source_assets": [
      "mip.gold.borrower_360"
    ],
    "data_freshness": [
      {
        "asset": "mip.gold.borrower_360",
        "refreshed_at": "2026-06-14T06:16:04.208Z",
        "status": "live",
        "note": "freshness returned by the generated SQL result"
      }
    ],
    "row_count": 1,
    "filters": [
      "in_the_money = TRUE"
    ],
    "trusted": true,
    "reasoning_trace": [],
    "known_data_gaps": [],
    "conversation_id": "",
    "message_id": "trusted-sql-ae293f4c7f08ec6c",
    "elapsed_ms": 0,
    "generated_at": "2026-06-14T06:49:25.197079+00:00"
  },
  "visualization": {
    "kind": "metric",
    "title": "in_the_money_borrowers",
    "x": null,
    "y": "in_the_money_borrowers",
    "series": null,
    "reason": "single-row numeric result"
  },
  "actions": [
    {
      "id": "open-cohort",
      "label": "Open this cohort in Lead Queue",
      "action_type": "open_cohort",
      "description": "Navigate into the lead queue with this Genie result audited.",
      "requires_confirmation": true,
      "route": "/lead-queue?segment=itm",
      "borrower_ids": [],
      "criteria": {
        "source": "trusted_sql",
        "source_assets": [
          "mip.gold.borrower_360"
        ],
        "visualization_kind": "metric",
        "row_count": 1,
        "sql_hash": "aad8aa53a6a1ef48",
        "result_filters": {
          "segment_codes": [
            "itm"
          ],
          "segment_mode": "any"
        }
      },
      "request_id": "genie-action-ef8acd95-3347-4933-868d-573832c5d335",
      "confirmation_token": "eyJhY3Rpb25fdHlwZSI6Im9wZW5fY29ob3J0IiwiYWN0b3IiOiJza3lsZXJAZW50cmFkYS5haSIsImJvcnJvd2VyX2lkcyI6W10sImNvbnZlcnNhdGlvbl9pZCI6IiIsImNyaXRlcmlhX2hhc2giOiJlNmE2ZDA1MzE4NzUxYjA2IiwiZXhwIjoxNzgxNDI2OTY1LCJraWQiOiJwcm9jZXNzIiwibWVzc2FnZV9pZCI6InRydXN0ZWQtc3FsLWFlMjkzZjRjN2YwOGVjNmMiLCJub25jZSI6IjdaeUw2M015ZUYxdXk4VnciLCJxdWVzdGlvbl9oYXNoIjoiYWUyOTNmNGM3ZjA4ZWM2YyIsInJlcXVlc3RfaWQiOiJnZW5pZS1hY3Rpb24tZWY4YWNkOTUtMzM0Ny00OTMzLTg2OGQtNTczODMyYzVkMzM1Iiwicm91dGUiOiIvbGVhZC1xdWV1ZT9zZWdtZW50PWl0bSIsInRydXN0ZWRfYXNzZXRzIjpbIm1pcC5nb2xkLmJvcnJvd2VyXzM2MCJdLCJ2IjoxfQ.OR6DK1b8WCkkf0xBbx2BVn7gxUUz98ldUHNBTrRjTcM"
    },
    {
      "id": "create-campaign-draft",
      "label": "Create draft campaign",
      "action_type": "create_draft_campaign",
      "description": "Create a Lakebase draft campaign from this governed Genie result.",
      "requires_confirmation": true,
      "route": "/lead-queue?segment=itm",
      "borrower_ids": [],
      "criteria": {
        "source": "trusted_sql",
        "source_assets": [
          "mip.gold.borrower_360"
        ],
        "visualization_kind": "metric",
        "row_count": 1,
        "sql_hash": "aad8aa53a6a1ef48",
        "result_filters": {
          "segment_codes": [
            "itm"
          ],
          "segment_mode": "any"
        }
      },
      "request_id": "genie-action-01e5ebd1-5e53-4fd7-ab36-ac220a62d9d4",
      "confirmation_token": "eyJhY3Rpb25fdHlwZSI6ImNyZWF0ZV9kcmFmdF9jYW1wYWlnbiIsImFjdG9yIjoic2t5bGVyQGVudHJhZGEuYWkiLCJib3Jyb3dlcl9pZHMiOltdLCJjb252ZXJzYXRpb25faWQiOiIiLCJjcml0ZXJpYV9oYXNoIjoiZTZhNmQwNTMxODc1MWIwNiIsImV4cCI6MTc4MTQyNjk2NSwia2lkIjoicHJvY2VzcyIsIm1lc3NhZ2VfaWQiOiJ0cnVzdGVkLXNxbC1hZTI5M2Y0YzdmMDhlYzZjIiwibm9uY2UiOiJnY1BDelJ4YkNYMWNveUVqIiwicXVlc3Rpb25faGFzaCI6ImFlMjkzZjRjN2YwOGVjNmMiLCJyZXF1ZXN0X2lkIjoiZ2VuaWUtYWN0aW9uLTAxZTVlYmQxLTVlNTMtNGZkNy1hYjM2LWFjMjIwYTYyZDlkNCIsInJvdXRlIjoiL2xlYWQtcXVldWU_c2VnbWVudD1pdG0iLCJ0cnVzdGVkX2Fzc2V0cyI6WyJtaXAuZ29sZC5ib3Jyb3dlcl8zNjAiXSwidiI6MX0.mcH6qToYikEd9dVS3dV15O6_sVfe5PxN2FaeAKPZwrM"
    }
  ],
  "metric_value": "111,726",
  "table_rows": [
    {
      "in_the_money_borrowers": 111726,
      "refreshed_at": "2026-06-14T06:16:04.208Z"
    }
  ],
  "follow_up_questions": []
}
```
### admin.rules — `GET /api/admin/rules`

```json
{
  "offer_rules_version": "itm_1c8cadaf925e",
  "rules_edited_at": "2026-06-14 06:16:04.208758",
  "thresholds": [
    {
      "key": "mip_min_spread_bps",
      "value": 75.0,
      "unit": "bps",
      "label": "Min spread (bps)",
      "description": "Minimum rate spread vs. market before a borrower is considered in the money.",
      "sort_order": 1,
      "last_updated": "2026-06-14 04:05:20.105489"
    },
    {
      "key": "mip_min_equity_pct",
      "value": 15.0,
      "unit": "pct",
      "label": "Min equity (%)",
      "description": "Minimum equity percentage required to qualify as in the money.",
      "sort_order": 2,
      "last_updated": "2026-06-14 04:05:20.105489"
    },
    {
      "key": "mip_heloc_equity_min_pct",
      "value": 35.0,
      "unit": "pct",
      "label": "HELOC equity floor (%)",
      "description": "Equity floor required for HELOC eligibility and refi+HELOC cross-sell.",
      "sort_order": 3,
      "last_updated": "2026-06-14 04:05:20.105489"
    },
    {
      "key": "mip_cashout_equity_min_pct",
      "value": 25.0,
      "unit": "pct",
      "label": "Cash-out equity floor (%)",
      "description": "Equity floor required for cash-out refi eligibility when rate economics are absent.",
      "sort_order": 4,
      "last_updated": "2026-06-14 04:05:20.105489"
    },
    {
      "key": "mip_retention_min_spread_bps",
      "value": 50.0,
      "unit": "bps",
      "label": "Retention min spread (bps)",
      "description": "Lowered spread bar used for retention outreach on existing customers.",
      "sort_order": 5,
      "last_updated": "2026-06-14 04:05:20.105489"
    },
    {
      "key": "mip_market_rate",
      "value": 0.0652,
      "unit": "rate_fraction",
      "label": "Market rate reference",
      "description": "Operating market rate used by gold rate-spread calculations; sourced from FRED MORTGAGE30US during gold refresh.",
      "sort_order": 6,
      "last_updated": "2026-06-14 06:16:04.208758"
    }
  ]
}
```
### admin.sources — `GET /api/admin/sources`

```json
[
  {
    "name": "Cotality Public Records",
    "status": "live",
    "rows": 5192913,
    "last_updated": "2026-06-14 04:10:56.221",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Delta Share \u00b7 nightly",
    "synthetic_demo": false
  },
  {
    "name": "Voluntary Lien",
    "status": "live",
    "rows": 5156184,
    "last_updated": "2026-06-14 04:10:38.849",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Delta Share \u00b7 nightly",
    "synthetic_demo": false
  },
  {
    "name": "MMA Mortgage Analytics",
    "status": "live",
    "rows": 26624795,
    "last_updated": "2026-06-14 04:11:03.203",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Delta Share \u00b7 nightly",
    "synthetic_demo": false
  },
  {
    "name": "CLIP",
    "status": "live",
    "rows": 5192913,
    "last_updated": "2026-06-14 04:10:56.221",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Mastered property id",
    "synthetic_demo": false
  },
  {
    "name": "Owner Link",
    "status": "live",
    "rows": 3438056,
    "last_updated": "2026-06-14 04:11:56.675762",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Mastered owner graph",
    "synthetic_demo": false
  },
  {
    "name": "AVM",
    "status": "live",
    "rows": 4347482,
    "last_updated": "2026-06-14 04:10:38.849",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Delta Share \u00b7 weekly; freshness uses AVM as-of date when supplied, otherwise lien ingest timestamp",
    "synthetic_demo": false
  },
  {
    "name": "FRED Market Rates",
    "status": "live",
    "rows": 284,
    "last_updated": "2026-06-11 20:54:20.51229",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "FRED MORTGAGE30US weekly observations \u00b7 scoring uses the single is_latest snapshot \u00b7 live",
    "synthetic_demo": false
  },
  {
    "name": "First-party LOS / Applications",
    "status": "demo_synthetic",
    "rows": 1424102,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Summit Mortgage synthetic LOS/application feed \u00b7 connected",
    "synthetic_demo": true
  },
  {
    "name": "First-party Servicing Portfolio",
    "status": "demo_synthetic",
    "rows": 913237,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Summit Mortgage synthetic servicing feed \u00b7 connected",
    "synthetic_demo": true
  },
  {
    "name": "First-party CRM / Campaigns",
    "status": "demo_synthetic",
    "rows": 2184912,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Summit Mortgage synthetic CRM/campaign feed \u00b7 connected",
    "synthetic_demo": true
  },
  {
    "name": "First-party Customer Interactions",
    "status": "demo_synthetic",
    "rows": 2842533,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Summit Mortgage synthetic interaction feed \u00b7 connected",
    "synthetic_demo": true
  },
  {
    "name": "First-party Product Balances",
    "status": "demo_synthetic",
    "rows": 1931547,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Summit Mortgage synthetic banking-product feed \u00b7 connected",
    "synthetic_demo": true
  },
  {
    "name": "MLS Listings",
    "status": "live",
    "rows": 6965436,
    "last_updated": "2026-06-12 17:31:13.657409",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Cotality MLS listing feed \u00b7 current active/under-contract rows drive listed_for_sale",
    "synthetic_demo": false
  },
  {
    "name": "Cotality HELOC Propensity",
    "status": "live",
    "rows": 4561016,
    "last_updated": "2026-06-12 18:52:53.822843",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Cotality HELOC propensity model feed \u00b7 drives HELOC Intent; not a permit filing source",
    "synthetic_demo": false
  },
  {
    "name": "Cotality Refi Propensity",
    "status": "live",
    "rows": 4561016,
    "last_updated": "2026-06-12 18:52:25.174202",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Cotality refinance propensity model feed \u00b7 adds intent score context",
    "synthetic_demo": false
  },
  {
    "name": "Building Permits",
    "status": "roadmap",
    "rows": null,
    "last_updated": null,
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Pending: no true filed building-permit table or permit filing columns found in cotality_mortgage_data.corelogic. Do not claim permit filings live.",
    "synthetic_demo": false
  },
  {
    "name": "UC Gold Borrower 360",
    "status": "live",
    "rows": 5156184,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Governed borrower profile table \u00b7 refreshed",
    "synthetic_demo": false
  },
  {
    "name": "UC Gold Lead Scores",
    "status": "live",
    "rows": 5156184,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Deterministic score and offer primitives \u00b7 refreshed",
    "synthetic_demo": false
  },
  {
    "name": "UC Gold Lead Population",
    "status": "live",
    "rows": 363290,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Ranked lead queue table \u00b7 refreshed",
    "synthetic_demo": false
  },
  {
    "name": "UC Gold Segment Population",
    "status": "live",
    "rows": 42,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Segment rollup table \u00b7 refreshed",
    "synthetic_demo": false
  },
  {
    "name": "UC Gold Borrower Dossier",
    "status": "live",
    "rows": 5156184,
    "last_updated": "2026-06-14 06:16:04.208758",
    "checked_at": "2026-06-14 06:16:04.208758",
    "note": "Borrower dossier pre-join \u00b7 refreshed",
    "synthetic_demo": false
  }
]
```
### geo.state_rollups — `GET /api/geo/state-rollups`

```json
{
  "rollups": [
    {
      "state": "IL",
      "addressable": 1851040,
      "in_the_money": 55037,
      "top_tier_opportunities": 1829,
      "avg_score": 36,
      "top_segment_code": "equity"
    },
    {
      "state": "CA",
      "addressable": 900371,
      "in_the_money": 13615,
      "top_tier_opportunities": 657,
      "avg_score": 40,
      "top_segment_code": "equity"
    },
    {
      "state": "FL",
      "addressable": 752572,
      "in_the_money": 15997,
      "top_tier_opportunities": 407,
      "avg_score": 39,
      "top_segment_code": "equity"
    },
    {
      "state": "TX",
      "addressable": 750962,
      "in_the_money": 14434,
      "top_tier_opportunities": 480,
      "avg_score": 38,
      "top_segment_code": "equity"
    },
    {
      "state": "WA",
      "addressable": 737682,
      "in_the_money": 11864,
      "top_tier_opportunities": 1452,
      "avg_score": 38,
      "top_segment_code": "equity"
    },
    {
      "state": "CO",
      "addressable": 163557,
      "in_the_money": 779,
      "top_tier_opportunities": 42,
      "avg_score": 38,
      "top_segment_code": "equity"
    }
  ],
  "snapshot_date": "2026-06-14"
}
```
### geo.state_rollups.filtered — `GET /api/geo/state-rollups?segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "rollups": [
    {
      "state": "IL",
      "addressable": 1216,
      "in_the_money": 1216,
      "top_tier_opportunities": 87,
      "avg_score": 65,
      "top_segment_code": null
    },
    {
      "state": "WA",
      "addressable": 294,
      "in_the_money": 294,
      "top_tier_opportunities": 67,
      "avg_score": 68,
      "top_segment_code": null
    },
    {
      "state": "TX",
      "addressable": 418,
      "in_the_money": 418,
      "top_tier_opportunities": 21,
      "avg_score": 65,
      "top_segment_code": null
    },
    {
      "state": "CA",
      "addressable": 335,
      "in_the_money": 335,
      "top_tier_opportunities": 35,
      "avg_score": 66,
      "top_segment_code": null
    },
    {
      "state": "FL",
      "addressable": 395,
      "in_the_money": 395,
      "top_tier_opportunities": 15,
      "avg_score": 64,
      "top_segment_code": null
    },
    {
      "state": "CO",
      "addressable": 15,
      "in_the_money": 15,
      "top_tier_opportunities": 1,
      "avg_score": 65,
      "top_segment_code": null
    }
  ],
  "snapshot_date": null
}
```
### geo.county_rollups.filtered — `GET /api/geo/county-rollups?state=IL&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "state": "IL",
  "rollups": [
    {
      "fips_5": "17031",
      "state": "IL",
      "county_name": "Cook",
      "addressable_borrowers": 1216,
      "in_the_money_borrowers": 1216,
      "high_opportunity_borrowers": 87,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity"
    }
  ],
  "snapshot_date": null,
  "scope_note": "Cotality data coverage: available counties across available states; available counties in IL"
}
```
### geo.zip_rollups.filtered — `GET /api/geo/zip-rollups?county_fips=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "fips_5": "17031",
  "rollups": [
    {
      "zip": "60628",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 34,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0U6FQG08SGCW2"
    },
    {
      "zip": "60638",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 24,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1XS0HWWEJSI4V"
    },
    {
      "zip": "60617",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 24,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0Z83GBYHYAZWR"
    },
    {
      "zip": "60629",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 23,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0Y0U8SQUY57ZE"
    },
    {
      "zip": "60643",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 22,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1IQIT53NYH4R4"
    },
    {
      "zip": "60632",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 21,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-10ZT0OCM2AQ5O"
    },
    {
      "zip": "60453",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 21,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0AIQ5MDWSVM82"
    },
    {
      "zip": "60193",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 17,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0W4YAIAUSZRDO"
    },
    {
      "zip": "60016",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 17,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1E2MBG8I4XQMA"
    },
    {
      "zip": "60153",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 16,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1UOAWI3MJXM5L"
    },
    {
      "zip": "60804",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 16,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0NNZ4SUJRLCUD"
    },
    {
      "zip": "60076",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 16,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1O6C56296R2UP"
    },
    {
      "zip": "60411",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 16,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-17CER6BZWNF4C"
    },
    {
      "zip": "60068",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 15,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0DTSQ2O8M4I7U"
    },
    {
      "zip": "60619",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 15,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-05Z0QJW96DG8L"
    },
    {
      "zip": "60053",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 15,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1OSZ1272RPW0J"
    },
    {
      "zip": "60630",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 14,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-097PEJS5BKK73"
    },
    {
      "zip": "60004",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 14,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0ABO8H6ZQR3IQ"
    },
    {
      "zip": "60714",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 14,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1EAFH9UC5UC00"
    },
    {
      "zip": "60618",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 14,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0CWB5XSX7EQHD"
    },
    {
      "zip": "60202",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 14,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-12U1M1X8PQ55M"
    },
    {
      "zip": "60452",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 13,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1F2QS4H4Z5CFI"
    },
    {
      "zip": "60608",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 13,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1D7TF8OX0P0DI"
    },
    {
      "zip": "60652",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 13,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0EB4D7LXLI43M"
    },
    {
      "zip": "60459",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 13,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-02W9JI7NAKFL4"
    },
    {
      "zip": "60062",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 13,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1XSXQ0ULACZ4F"
    },
    {
      "zip": "60067",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 13,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1TWJ8CH7P41U6"
    },
    {
      "zip": "60402",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 12,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0HBLLIOX31E8R"
    },
    {
      "zip": "60074",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 12,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-17IOF8H43NMKW"
    },
    {
      "zip": "60426",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 12,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-10C69V0TN0XRQ"
    },
    {
      "zip": "60056",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-02GBGH812S0Z8"
    },
    {
      "zip": "60625",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-00QIZ1G3KE7HD"
    },
    {
      "zip": "60631",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1HILNYGS0QMRG"
    },
    {
      "zip": "60131",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1KY3KA3OBYCHN"
    },
    {
      "zip": "60657",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-06TSD70PEG8TR"
    },
    {
      "zip": "60462",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0TC6I8EKOZ03I"
    },
    {
      "zip": "60010",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-13INTLVZJ2ZDO"
    },
    {
      "zip": "60636",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 11,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1LJ1KK1IWSQJA"
    },
    {
      "zip": "60623",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-14UFMNTS4LVNB"
    },
    {
      "zip": "60201",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0QFTCDS92FP00"
    },
    {
      "zip": "60645",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-09S3SHPICD3VY"
    },
    {
      "zip": "60651",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0UK6GMHG6M3ZI"
    },
    {
      "zip": "60620",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-02LVE27EY013L"
    },
    {
      "zip": "60611",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-10SAPRETS0H4I"
    },
    {
      "zip": "60025",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 69,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1XDQW5PTOJW3R"
    },
    {
      "zip": "60091",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-06RNLF69H2W39"
    },
    {
      "zip": "60609",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0AR1YMMZH0OFD"
    },
    {
      "zip": "60438",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0V4P2KC6IYQOX"
    },
    {
      "zip": "60525",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 72,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0FJJBMOBCQULL"
    },
    {
      "zip": "60706",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1P7UQ268VY6LD"
    },
    {
      "zip": "60007",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 10,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1J5QAJCRDUKO8"
    },
    {
      "zip": "60445",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0E89YIMGPCR4E"
    },
    {
      "zip": "60169",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-172ELHPXI89DH"
    },
    {
      "zip": "60154",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0ELUPF8W4DNDE"
    },
    {
      "zip": "60656",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 59,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0LAL8UHTLFYGB"
    },
    {
      "zip": "60610",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1JBK71IGXBJFP"
    },
    {
      "zip": "60712",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-17WANAO1W6ZLU"
    },
    {
      "zip": "60655",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0MY6QGZGUNI2C"
    },
    {
      "zip": "60018",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 61,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0FL2V3B4RJWWC"
    },
    {
      "zip": "60803",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-087JC8RVJ670I"
    },
    {
      "zip": "60487",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-12BN9KA62UKTZ"
    },
    {
      "zip": "60428",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 9,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0WLVWH9EVYYJK"
    },
    {
      "zip": "60616",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-07HI69FFOULET"
    },
    {
      "zip": "60644",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0BZYAE5W99FH9"
    },
    {
      "zip": "60164",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1H69KMRZET8KI"
    },
    {
      "zip": "60409",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0AIL2E198BK8X"
    },
    {
      "zip": "60406",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1G16VH1VCHBHB"
    },
    {
      "zip": "60634",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0T5JXDF0SOLI6"
    },
    {
      "zip": "60827",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 8,
      "avg_opportunity_score": 61,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-00WMA3WZQBVLI"
    },
    {
      "zip": "60070",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-04U6XFA2BN77I"
    },
    {
      "zip": "60558",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0LGTCMSLBP8CO"
    },
    {
      "zip": "60107",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1UCN47726SLLZ"
    },
    {
      "zip": "60624",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-05OUASSSFU7L8"
    },
    {
      "zip": "60477",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-17DZ2T3ZAFXLY"
    },
    {
      "zip": "60008",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 59,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-09R15784CUY9U"
    },
    {
      "zip": "60646",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0E5EUDGUY3H2J"
    },
    {
      "zip": "60302",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 69,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0GUN49LOG927S"
    },
    {
      "zip": "60419",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-071HXO8G5ENX8"
    },
    {
      "zip": "60641",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0SVSYTQFWZKCM"
    },
    {
      "zip": "60660",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0O63M20ET7DWD"
    },
    {
      "zip": "60614",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-17F54V9ZQWNTD"
    },
    {
      "zip": "60005",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 7,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1STAS7AJXESUX"
    },
    {
      "zip": "60639",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-12W1MU4KF4JF7"
    },
    {
      "zip": "60526",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-11AEN468E1R16"
    },
    {
      "zip": "60633",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1S6D2E42Y63SV"
    },
    {
      "zip": "60104",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-096REF9S4EG4A"
    },
    {
      "zip": "60093",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1THCF2Q2SKO8I"
    },
    {
      "zip": "60465",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-019WOUB9XIS1M"
    },
    {
      "zip": "60637",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0JAPTKR4HITXB"
    },
    {
      "zip": "60649",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-09ND6VK9U23NI"
    },
    {
      "zip": "60640",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0XYYA9KXG98HG"
    },
    {
      "zip": "60707",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1QYORQZ02EZLO"
    },
    {
      "zip": "60160",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1M6F7Q3AG43EE"
    },
    {
      "zip": "60621",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1JTIHNFOBV23J"
    },
    {
      "zip": "60090",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-198DL7Z7NWNAL"
    },
    {
      "zip": "60513",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 6,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0VTIRB9IV0QL3"
    },
    {
      "zip": "60613",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 5,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0FDUQ7FYPHZT9"
    },
    {
      "zip": "60478",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 5,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0C45HQ893XPU9"
    },
    {
      "zip": "60466",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 5,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1CVU1HCFN6F77"
    },
    {
      "zip": "60155",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 5,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-171BBCUHAMZPX"
    },
    {
      "zip": "60422",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 5,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1DV8WN9ICZIE2"
    },
    {
      "zip": "60546",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 5,
      "avg_opportunity_score": 69,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1CSFJ311C1KDU"
    },
    {
      "zip": "60120",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0GXTYFFTGZH6R"
    },
    {
      "zip": "60473",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1R1JRIZKS16G2"
    },
    {
      "zip": "60615",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 69,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0XU81R66FK19O"
    },
    {
      "zip": "60162",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 58,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1VF8DKHK4O7G4"
    },
    {
      "zip": "60192",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-07ELR3CPA34PV"
    },
    {
      "zip": "60805",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1DVEYRXI2Z0C5"
    },
    {
      "zip": "60612",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1A2I7DMEBFEWJ"
    },
    {
      "zip": "60026",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1HSBY6CA0A052"
    },
    {
      "zip": "60439",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 72,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-190K8XD83J9JU"
    },
    {
      "zip": "60647",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1O9MYDX40YM7F"
    },
    {
      "zip": "60429",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 61,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-141K44A8ZGLYI"
    },
    {
      "zip": "60425",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 61,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1T2W7VEWNGTA3"
    },
    {
      "zip": "60194",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1KUA4TBHSDM7X"
    },
    {
      "zip": "60467",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-08RSXCRQKOM0B"
    },
    {
      "zip": "60659",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 4,
      "avg_opportunity_score": 59,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1K5I9EUVRH1D8"
    },
    {
      "zip": "60455",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 54,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-06HN1TZ9RN06Q"
    },
    {
      "zip": "60022",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0Y86RKIZB2QLA"
    },
    {
      "zip": "60305",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1TCLPK9X2A3UI"
    },
    {
      "zip": "60457",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-114KVCC1KQWY0"
    },
    {
      "zip": "60469",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1XUHXUD76UKZ0"
    },
    {
      "zip": "60475",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 74,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0LU6YW4366U3M"
    },
    {
      "zip": "60077",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1TQ3DOOIRPVDM"
    },
    {
      "zip": "60622",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 67,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1H5WCRX6HTPMR"
    },
    {
      "zip": "60130",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-02AHDUN1EKCBJ"
    },
    {
      "zip": "60418",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 60,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-05CMMG06A3LBF"
    },
    {
      "zip": "60461",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0JVVKRXHYI7LG"
    },
    {
      "zip": "60430",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 61,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1RITMS42PKPZQ"
    },
    {
      "zip": "60176",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 3,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0665T7BW6W0NW"
    },
    {
      "zip": "60103",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 76,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1PD7POIQTGS71"
    },
    {
      "zip": "60626",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 73,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-15JVP474XQFQ7"
    },
    {
      "zip": "60203",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 74,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-04EXPR5CEEM54"
    },
    {
      "zip": "60173",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0C45YEAA9GLAP"
    },
    {
      "zip": "60480",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0QD5O3GLT6292"
    },
    {
      "zip": "60443",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0NKEKQA55POXN"
    },
    {
      "zip": "60304",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-18CQT6O4FATL7"
    },
    {
      "zip": "60456",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 60,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1PAJDMCKJBM9H"
    },
    {
      "zip": "60472",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 74,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1OCHN6YKQGMT4"
    },
    {
      "zip": "60471",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1B5J4JVAPF1UA"
    },
    {
      "zip": "60195",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 72,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-00R53LTGWOQGG"
    },
    {
      "zip": "60527",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 57,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0O11EXWHEU6RM"
    },
    {
      "zip": "60464",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 57,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-04W5D1FGYR6KY"
    },
    {
      "zip": "60165",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1CD1295333LG2"
    },
    {
      "zip": "60463",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 71,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1I40YGSGEM6AN"
    },
    {
      "zip": "60163",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 2,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0WRHMGTH4JHDF"
    },
    {
      "zip": "60415",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 65,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0LB8CW5Z9MP9M"
    },
    {
      "zip": "60521",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 70,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0WKV15AI5OAME"
    },
    {
      "zip": "60601",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0QGK5FDOG841F"
    },
    {
      "zip": "60605",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-07PMNXG2PXWH4"
    },
    {
      "zip": "60642",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 60,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0K47RVC0LC6AQ"
    },
    {
      "zip": "60043",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 70,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1T1PFU17OT8VT"
    },
    {
      "zip": "60089",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1T53PYOBF5T2B"
    },
    {
      "zip": "60607",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 66,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-05BA6F3N14VP3"
    },
    {
      "zip": "60133",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 68,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-14CMD51IRUBYH"
    },
    {
      "zip": "60654",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0DU57MN0YKANC"
    },
    {
      "zip": "60172",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 1,
      "avg_opportunity_score": 75,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1EZZ8OHZCQZA8"
    }
  ],
  "snapshot_date": null
}
```
### leads.filtered_geo — `GET /api/leads?state=IL&county=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "borrower_id": "B-0QFTCDS92FP00",
  "display_name": "Owner bf57bbb5",
  "city": "EVANSTON",
  "state": "IL",
  "zip": "60201",
  "clip": "clip_ref_05f0d03cdc07",
  "segment_codes": [
    "itm",
    "listed",
    "permit",
    "investor",
    "equity"
  ],
  "equity_estimate": 518844,
  "rate_spread_bps": 268,
  "opportunity_score": 90,
  "confidence": 90,
  "recommended_offer_code": "purchase",
  "recommended_offer": "Purchase Mortgage",
  "why_now": "The home is actively listed -- a purchase mortgage on the next home is the right offer.",
  "evidence_ids": [
    "ev-e135282b27bb",
    "ev-86f4ae860c47",
    "ev-21ae23be0cea"
  ],
  "approval_status": "approved",
  "outreach_status": "queued",
  "approved_at": "2026-06-14T06:48:07.431000Z",
  "outreach_at": null,
  "is_owner_occupied": true,
  "is_investor": true,
  "is_current_customer": false,
  "is_former_customer": false,
  "is_competitor_lien": true,
  "related_property_count": 9,
  "current_lien_balance": 168095,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": true,
  "listing_status_category": "A",
  "listing_status_description": "Active",
  "listing_date": "2026-04-17",
  "listing_status_date": "2026-05-20",
  "listing_price": 799000,
  "listing_days_on_market": 48,
  "listing_service": "MRED",
  "heloc_propensity_score": 701,
  "heloc_propensity_run_date": "2026-05-08",
  "has_heloc_propensity_trigger": true,
  "refi_propensity_score": 702,
  "refi_propensity_run_date": "2026-05-08",
  "has_refi_propensity_trigger": true,
  "current_lender_ref": "Competitor O",
  "marketing_eligible": true,
  "consent_status": "opt_in",
  "suppression_reason": null,
  "last_touch_at": "2026-05-14T00:00:00Z",
  "eligible_recontact_at": null,
  "assigned_to_email": null,
  "assigned_to_label": null,
  "assigned_at": null,
  "assignment_expires_at": null,
  "latest_disposition_outcome": null,
  "latest_disposition_at": null,
  "latest_callback_at": null,
  "aging_days": 0
}
```

## Red flags

(none)

## Teardown

No synthetic approval/rejection rows are expected. `B-TEST-*` IDs are used only for unknown-borrower 404 probes.
