# E2E live verification — 2026-04-23

> **Internal implementation artifact. Not approved for public release.**
>
> **Superseded 2026-05-09:** Current `tools/verify_live.py` no longer uses
> synthetic `B-TEST-*` borrower IDs for positive approve/reject probes.
> Synthetic IDs are now negative probes that must return 404; positive
> outreach probes use real borrower IDs returned by `/api/leads`. Regenerate
> this report after deploying the 2026-05-09 audit fixes before using it as
> current release evidence.

Base URL: `https://mip-app-2543889327043640.aws.databricksapps.com`  
Auth: `databricks auth token -p DEFAULT` (OAuth bearer, skyler@entrada.ai)  
Historical synthetic test-id prefix from this superseded run: `B-TEST-*`

## Endpoint results

| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |
| --- | --- | ---: | ---: | :---: | --- |
| health | `GET /api/health` | 200 | 2500 | yes | keys/len: app_env, breaker_state_changes_last_hour, circuit_breakers, counters_persistence, dependencies, fallback_identity_fallbacks_process_total, fallback_identity_fallbacks_total, log_export |
| config.options | `GET /api/config/options` | 200 | 2069 | yes | keys/len: equity_thresholds, geographies, geographies_status, geography_scope, lender_name, lender_relationships, lien_status, occupancy |
| portfolio.unfiltered | `POST /api/portfolio/preview` | 200 | 2304 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.all_states | `POST /api/portfolio/preview` | 200 | 507 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.all_states.owner.25pct | `POST /api/portfolio/preview` | 200 | 1565 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| segments | `GET /api/segments` | 200 | 1052 | yes | keys/len: [array len=6] |
| leads.all | `GET /api/leads` | 200 | 1329 | yes | keys/len: [array len=500] |
| leads.itm | `GET /api/leads?segment=itm` | 200 | 1620 | yes | keys/len: [array len=500] |
| borrower.detail | `GET /api/borrowers/B-102FL7THC6Q3L` | 200 | 1283 | yes | keys/len: approval_status, avm_value, borrower_id, city, clip, clip_id, confidence, current_lender_ref |
| borrower.evidence | `GET /api/borrowers/B-102FL7THC6Q3L/evidence` | 200 | 927 | yes | keys/len: [array len=8] |
| offers.recommend | `POST /api/offers/recommend` | 200 | 1278 | yes | keys/len: alternatives, borrower_id, confidence, evidence_ids, offer_code, offer_type, product_label, rationale |
| outreach.draft | `POST /api/outreach/draft` | 200 | 872 | yes | keys/len: body, borrower_id, channel, offer_code, status, subject |
| outreach.approve.historical_synthetic | `POST /api/outreach/approve` | 200 | 1575 | yes | Superseded 2026-05-09; current verifier expects synthetic IDs to 404 and uses real borrowers for positive probes. |
| outreach.reject.historical_synthetic | `POST /api/outreach/reject` | 200 | 1398 | yes | Superseded 2026-05-09; current verifier expects synthetic IDs to 404 and uses real borrowers for positive probes. |
| audit.events | `GET /api/audit/events?limit=10` | 200 | 1044 | yes | keys/len: [array len=10] |
| genie.message | `POST /api/genie/message` | 200 | 12713 | yes | keys/len: actions, answer, conversation_id, elapsed_ms, follow_up_questions, message_id, metric_value, proof |
| admin.rules | `GET /api/admin/rules` | 200 | 919 | yes | keys/len: legacy_override, offer_rules_version, rules_edited_at, thresholds |
| admin.sources | `GET /api/admin/sources` | 200 | 820 | yes | keys/len: [array len=19] |
| geo.state_rollups | `GET /api/geo/state-rollups` | 200 | 1537 | yes | keys/len: rollups, snapshot_date |
| geo.state_rollups.filtered | `GET /api/geo/state-rollups?segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 1180 | yes | keys/len: rollups, snapshot_date |
| geo.county_rollups.filtered | `GET /api/geo/county-rollups?state=IL&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 2382 | yes | keys/len: rollups, scope_note, snapshot_date, state |
| geo.zip_rollups.filtered | `GET /api/geo/zip-rollups?county_fips=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 2100 | yes | keys/len: fips_5, rollups, snapshot_date |
| leads.filtered_geo | `GET /api/leads?state=IL&county=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25` | 200 | 1759 | yes | keys/len: [array len=500] |

## Clean payload samples

### health — `GET /api/health`

```json
{
  "status": "ok",
  "mode": "live",
  "app_env": "sandbox",
  "warehouse_id": "81d08d4fa2d799e9",
  "dependencies": {
    "warehouse": "up",
    "lakebase": "up",
    "genie": "up"
  },
  "circuit_breakers": {
    "warehouse": "closed",
    "lakebase": "closed",
    "genie": "closed"
  }
}
```
### config.options — `GET /api/config/options`

```json
{
  "lender_name": "Summit Mortgage",
  "geographies": [
    "All current states",
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
    "snapshot_date": "2026-05-08",
    "source_table": "mip.gold.county_rollup",
    "scope_label": "Cotality data coverage: current counties across current states",
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
  ]
}
```
### portfolio.unfiltered — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 5156184,
  "high_intent_leads": 134534,
  "top_tier_opportunities": 4320,
  "offers_recommended": 4472648,
  "avg_score": 37,
  "trends": {
    "marketable_population": {
      "series": [
        5156184.0,
        5156184.0,
        5156184.0,
        5156184.0,
        5156184.0,
        5156184.0
      ],
      "delta_pct": 0.0,
      "direction": "flat",
      "comparison_label": "vs 2026-04-22"
    },
    "high_intent_leads": {
      "series": [
        147742.0,
        147742.0,
        147742.0,
        147742.0,
        134534.0,
        134534.0
      ],
      "delta_pct": -8.9,
      "direction": "down",
      "comparison_label": "vs 2026-04-22"
    },
    "top_tier_opportunities": {
      "series": [
        3081.0,
        3074.0,
        4542.0,
        4320.0,
        4320.0
      ],
      "delta_pct": 40.2,
      "direction": "up",
      "comparison_label": "vs 2026-04-23"
    },
    "offers_recommended": {
      "series": [
        4468137.0,
        4468007.0,
        4468137.0,
        4477262.0,
        4472648.0,
        4472648.0
      ],
      "delta_pct": 0.1,
      "direction": "flat",
      "comparison_label": "vs 2026-04-22"
    },
    "avg_score": {
      "series": [
        42.0,
        36.0,
        36.0,
        37.0,
        37.0,
        37.0
      ],
      "delta_pct": -11.9,
      "direction": "down",
      "comparison_label": "vs 2026-04-22"
    },
    "approved_count": {
      "series": [
        1.0,

```
### portfolio.all_states — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 5156184,
  "high_intent_leads": 134534,
  "top_tier_opportunities": 4320,
  "offers_recommended": 4472648,
  "avg_score": 37,
  "trends": {
    "marketable_population": {
      "series": [
        5156184.0,
        5156184.0,
        5156184.0,
        5156184.0,
        5156184.0,
        5156184.0
      ],
      "delta_pct": 0.0,
      "direction": "flat",
      "comparison_label": "vs 2026-04-22"
    },
    "high_intent_leads": {
      "series": [
        147742.0,
        147742.0,
        147742.0,
        147742.0,
        134534.0,
        134534.0
      ],
      "delta_pct": -8.9,
      "direction": "down",
      "comparison_label": "vs 2026-04-22"
    },
    "top_tier_opportunities": {
      "series": [
        3081.0,
        3074.0,
        4542.0,
        4320.0,
        4320.0
      ],
      "delta_pct": 40.2,
      "direction": "up",
      "comparison_label": "vs 2026-04-23"
    },
    "offers_recommended": {
      "series": [
        4468137.0,
        4468007.0,
        4468137.0,
        4477262.0,
        4472648.0,
        4472648.0
      ],
      "delta_pct": 0.1,
      "direction": "flat",
      "comparison_label": "vs 2026-04-22"
    },
    "avg_score": {
      "series": [
        42.0,
        36.0,
        36.0,
        37.0,
        37.0,
        37.0
      ],
      "delta_pct": -11.9,
      "direction": "down",
      "comparison_label": "vs 2026-04-22"
    },
    "approved_count": {
      "series": [
        1.0,

```
### portfolio.all_states.owner.25pct — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 2824263,
  "high_intent_leads": 96747,
  "top_tier_opportunities": 3033,
  "offers_recommended": 2824263,
  "avg_score": 41,
  "trends": {}
}
```
### segments — `GET /api/segments`

```json
{
  "code": "itm",
  "name": "In the Money",
  "count": 134534,
  "delta": "+0%",
  "avg_score": 61,
  "description": "Lien rate >= 75 bps above par and equity >= 15%.",
  "color": "#5CE1E6"
}
```
### leads.all — `GET /api/leads`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "display_name": "Owner 3b3ba2e0",
  "city": "CALUMET CITY",
  "state": "IL",
  "zip": "60409",
  "clip": "clip_ref_39d931a7bed1",
  "segment_codes": [
    "itm",
    "investor",
    "equity"
  ],
  "equity_estimate": 153163,
  "rate_spread_bps": 390,
  "opportunity_score": 88,
  "confidence": 85,
  "recommended_offer": "Refinance + HELOC",
  "why_now": "Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.",
  "evidence_ids": [
    "ev-494e7397d8c7",
    "ev-ea6209b0cb8c",
    "ev-5a6e84f25fed"
  ],
  "approval_status": "pending",
  "is_owner_occupied": false,
  "is_investor": true,
  "related_property_count": 346,
  "current_lien_balance": 15000,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": false,
  "current_lender_ref": "Competitor Other"
}
```
### leads.itm — `GET /api/leads?segment=itm`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "display_name": "Owner 3b3ba2e0",
  "city": "CALUMET CITY",
  "state": "IL",
  "zip": "60409",
  "clip": "clip_ref_39d931a7bed1",
  "segment_codes": [
    "itm",
    "investor",
    "equity"
  ],
  "equity_estimate": 153163,
  "rate_spread_bps": 390,
  "opportunity_score": 88,
  "confidence": 85,
  "recommended_offer": "Refinance + HELOC",
  "why_now": "Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.",
  "evidence_ids": [
    "ev-494e7397d8c7",
    "ev-ea6209b0cb8c",
    "ev-5a6e84f25fed"
  ],
  "approval_status": "pending",
  "is_owner_occupied": false,
  "is_investor": true,
  "related_property_count": 346,
  "current_lien_balance": 15000,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": false,
  "current_lender_ref": "Competitor Other"
}
```
### borrower.detail — `GET /api/borrowers/B-102FL7THC6Q3L`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "display_name": "Owner 3b3ba2e0",
  "city": "CALUMET CITY",
  "state": "IL",
  "zip": "60409",
  "clip": ""
}
```
### borrower.evidence — `GET /api/borrowers/B-102FL7THC6Q3L/evidence`

```json
{
  "evidence_id": "ev-494e7397d8c7",
  "source_product": "Voluntary Lien + Market Rates",
  "source_table": "mip.silver.lien_current",
  "signal_type": "rate_spread",
  "signal_value": "+390 bps",
  "display_text": "Current lien rate is 390 bps vs. par.",
  "confidence": 0.92,
  "timestamp": "2026-05-08 18:12:03.351"
}
```
### offers.recommend — `POST /api/offers/recommend`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "offer_code": "refi_plus_heloc",
  "offer_type": "refi_plus_heloc",
  "product_label": "Refinance + HELOC",
  "confidence": 85,
  "rationale": "Rate is well above current market rates and the home has very strong home equity -- a strong candidate for a refinance with a HELOC alongside it."
}
```
### outreach.draft — `POST /api/outreach/draft`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "offer_code": "OFFER-B-102FL7THC6Q3L",
  "channel": "email",
  "subject": "Refinance + HELOC opportunity for your property",
  "body": "Hi [first name],\n\nBased on recent public-record signals in CALUMET CITY, IL, you may qualify for Refinance + HELOC. Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.\n\nReply to this note and a licensed officer will follow up. This draft is for human review only; no outreach has been sent.",
  "status": "draft"
}
```
### outreach.approve.historical_synthetic — `POST /api/outreach/approve`

Superseded 2026-05-09: this historical sample is not current verifier behavior.

```json
{
  "approved": true,
  "approval_id": "c778d6a5-0693-4bf9-957d-4ac2c29c9449",
  "audit_event_id": "9bc221f0-88d2-47a7-8797-2f6ae989a6a7"
}
```
### outreach.reject.historical_synthetic — `POST /api/outreach/reject`

Superseded 2026-05-09: this historical sample is not current verifier behavior.

```json
{
  "rejected": true,
  "approval_id": "e74b59f3-0985-4f95-a911-a5f3a68bc6cb",
  "audit_event_id": "7ccd0bd3-f452-4f41-9306-68be185ac7e4"
}
```
### audit.events — `GET /api/audit/events?limit=10`

```json
{
  "event_id": "7ccd0bd3-f452-4f41-9306-68be185ac7e4",
  "actor": "skyler@entrada.ai",
  "action": "outreach.reject",
  "entity_type": "approval",
  "entity_id": "e74b59f3-0985-4f95-a911-a5f3a68bc6cb",
  "payload_json": {
    "offer_code": "refi",
    "approval_id": "e74b59f3-0985-4f95-a911-a5f3a68bc6cb",
    "borrower_id": "B-TEST-A88A8DAE"
  },
  "evidence_ids": [],
  "created_at": "2026-05-08T18:38:21.106118+00:00",
  "event_type": "OUTREACH_REJECT",
  "subject_clip": null,
  "subject_segment": null,
  "request_id": null
}
```
### genie.message — `POST /api/genie/message`

```json
{
  "conversation_id": "01f14b0d178c1c498cef84605f0809d0",
  "question": "Which zips have the most in-the-money refi candidates?",
  "answer": "I ranked ZIP codes by unique borrowers currently in-the-money for refinance from mip.gold.borrower_360. The current leader is ZIP 60617 (IL) with 1,447 borrowers; the cohort action below carries these ZIP filters into Lead Queue.",
  "source": "trusted_sql",
  "trusted_assets": [
    "mip.gold.borrower_360"
  ],
  "message_id": "01f14b0d179c1b308c751f612a3a57ac"
}
```
### admin.rules — `GET /api/admin/rules`

```json
{
  "offer_rules_version": "itm_77eddaa7d767",
  "rules_edited_at": "2026-05-08 18:11:28.387726",
  "thresholds": [
    {
      "key": "mip_min_spread_bps",
      "value": 75.0,
      "unit": "bps",
      "label": "Min spread (bps)",
      "description": "Minimum rate spread vs. market before a borrower is considered in the money.",
      "sort_order": 1,
      "last_updated": "2026-05-08 18:11:28.387726"
    },
    {
      "key": "mip_min_equity_pct",
      "value": 15.0,
      "unit": "pct",
      "label": "Min equity (%)",
      "description": "Minimum equity percentage required to qualify as in the money.",
      "sort_order": 2,
      "last_updated": "2026-05-08 18:11:28.387726"
    },
    {
      "key": "mip_heloc_equity_min_pct",
      "value": 35.0,
      "unit": "pct",
      "label": "HELOC equity floor (%)",
      "description": "Equity floor required for HELOC eligibility and refi+HELOC cross-sell.",
      "sort_order": 3,
      "last_updated": "2026-05-08 18:11:28.387726"
    },
    {
      "key": "mip_cashout_equity_min_pct",
      "value": 25.0,
      "unit": "pct",
      "label": "Cash-out equity floor (%)",
      "description": "Equity floor required for cash-out refi eligibility when rate economics are absent.",
      "sort_order": 4,
      "last_updated": "2026-05-08 18:11:28.387726"
    },
    {
      "key": "mip_retention_min_spread_bps",
      "value": 50.0,
      "unit": "bps",
      "label": "Retention min spread (bps)",
      "description": "Lowered sp
```
### admin.sources — `GET /api/admin/sources`

```json
[
  {
    "name": "Cotality Public Records",
    "status": "live",
    "rows": 5192913,
    "last_updated": "2026-05-08 18:12:05.75",
    "note": "Delta Share \u00b7 nightly",
    "checked_at": "2026-05-08 18:14:32.481895",
    "synthetic_demo": false
  },
  {
    "name": "Voluntary Lien",
    "status": "live",
    "rows": 5156184,
    "last_updated": "2026-05-08 18:12:03.351",
    "note": "Delta Share \u00b7 nightly",
    "checked_at": "2026-05-08 18:14:32.481895",
    "synthetic_demo": false
  },
  {
    "name": "MMA Mortgage Analytics",
    "status": "live",
    "rows": 26624795,
    "last_updated": "2026-05-08 18:12:13.038",
    "note": "Delta Share \u00b7 nightly",
    "checked_at": "2026-05-08 18:14:32.481895",
    "synthetic_demo": false
  },
  {
    "name": "CLIP",
    "status": "live",
    "rows": 5192913,
    "last_updated": "2026-05-08 18:12:05.75",
    "note": "Mastered property id",
    "checked_at": "2026-05-08 18:14:32.481895",
    "synthetic_demo": false
  },
  {
    "name": "Owner Link",
    "status": "live",
    "rows": 3438056,
    "last_updated": "2026-05-08 18:13:01.37545",
    "note": "Mastered owner graph",
    "checked_at": "2026-05-08 18:14:32.481895",
    "synthetic_demo": false
  },
  {
    "name": "AVM",
    "status": "live",
    "rows": 4347482,
    "last_updated": "2026-05-08 18:12:03.351",
    "note": "Delta Share \u00b7 weekly; freshness uses AVM as-of date when supplied, otherwise lien ingest timestamp",
    "checked_at": "2026-05-08 18:14:32.
```
### geo.state_rollups — `GET /api/geo/state-rollups`

```json
{
  "rollups": [
    {
      "state": "IL",
      "addressable": 1851040,
      "in_the_money": 67352,
      "top_tier_opportunities": 1519,
      "avg_score": 36,
      "top_segment_code": "equity"
    },
    {
      "state": "CA",
      "addressable": 900371,
      "in_the_money": 16544,
      "top_tier_opportunities": 519,
      "avg_score": 39,
      "top_segment_code": "equity"
    },
    {
      "state": "FL",
      "addressable": 752572,
      "in_the_money": 18880,
      "top_tier_opportunities": 401,
      "avg_score": 38,
      "top_segment_code": "equity"
    },
    {
      "state": "TX",
      "addressable": 750962,
      "in_the_money": 16913,
      "top_tier_opportunities": 459,
      "avg_score": 37,
      "top_segment_code": "equity"
    },
    {
      "state": "WA",
      "addressable": 737682,
      "in_the_money": 13779,
      "top_tier_opportunities": 1403,
      "avg_score": 37,
      "top_segment_code": "equity"
    },
    {
      "state": "CO",
      "addressable": 163557,
      "in_the_money": 1066,
      "top_tier_opportunities": 19,
      "avg_score": 36,
      "top_segment_code": "equity"
    }
  ],
  "snapshot_date": "2026-05-08"
}
```
### geo.state_rollups.filtered — `GET /api/geo/state-rollups?segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "rollups": [
    {
      "state": "IL",
      "addressable": 31146,
      "in_the_money": 31146,
      "top_tier_opportunities": 1037,
      "avg_score": 63,
      "top_segment_code": null
    },
    {
      "state": "CA",
      "addressable": 9155,
      "in_the_money": 9155,
      "top_tier_opportunities": 277,
      "avg_score": 64,
      "top_segment_code": null
    },
    {
      "state": "FL",
      "addressable": 10230,
      "in_the_money": 10230,
      "top_tier_opportunities": 209,
      "avg_score": 63,
      "top_segment_code": null
    },
    {
      "state": "CO",
      "addressable": 559,
      "in_the_money": 559,
      "top_tier_opportunities": 16,
      "avg_score": 61,
      "top_segment_code": null
    },
    {
      "state": "WA",
      "addressable": 6869,
      "in_the_money": 6869,
      "top_tier_opportunities": 688,
      "avg_score": 66,
      "top_segment_code": null
    },
    {
      "state": "TX",
      "addressable": 10726,
      "in_the_money": 10726,
      "top_tier_opportunities": 294,
      "avg_score": 64,
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
      "addressable_borrowers": 31146,
      "in_the_money_borrowers": 31146,
      "high_opportunity_borrowers": 1037,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity"
    }
  ],
  "snapshot_date": null,
  "scope_note": "Cotality data coverage: current counties across current states; current county count available in selected state"
}
```
### geo.zip_rollups.filtered — `GET /api/geo/zip-rollups?county_fips=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "fips_5": "17031",
  "rollups": [
    {
      "zip": "60617",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 697,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1EEEN00S99GXC"
    },
    {
      "zip": "60628",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 663,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0NHUFQ8DANXHD"
    },
    {
      "zip": "60629",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 637,
      "avg_opportunity_score": 62,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-09UU5N5FRPW4F"
    },
    {
      "zip": "60620",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 538,
      "avg_opportunity_score": 64,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0R15JZMFF5CK3"
    },
    {
      "zip": "60453",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 535,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-1AY12RDL08UTI"
    },
    {
      "zip": "60643",
      "state": "IL",
      "county_fips_5": "17031",
      "addressable_borrowers": 494,
      "avg_opportunity_score": 63,
      "top_segment_code": "equity",
      "sample_borrower_id": "B-0H6OTA4IYX1TD"
    },
    {
      "zip
```
### leads.filtered_geo — `GET /api/leads?state=IL&county=17031&segment_codes=itm%2Cequity&segment_mode=all&occupancy=Owner-occupied&lien_status=Open+1st+lien&min_equity_pct_label=%E2%89%A5+25%25`

```json
{
  "borrower_id": "B-06P7DNZ9E8YOM",
  "display_name": "Owner c7d163d3",
  "city": "MELROSE PARK",
  "state": "IL",
  "zip": "60160",
  "clip": "clip_ref_2593a92b3c3d",
  "segment_codes": [
    "itm",
    "investor",
    "equity"
  ],
  "equity_estimate": 283228,
  "rate_spread_bps": 340,
  "opportunity_score": 85,
  "confidence": 80,
  "recommended_offer": "Refinance + HELOC",
  "why_now": "Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.",
  "evidence_ids": [
    "ev-60878660c333",
    "ev-13c796b6d989",
    "ev-55ff2c4ff348"
  ],
  "approval_status": "pending",
  "is_owner_occupied": true,
  "is_investor": true,
  "related_property_count": 5,
  "current_lien_balance": 41000,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": false,
  "current_lender_ref": "Competitor Other"
}
```

## Red flags

(none)

## Teardown

Historical synthetic approvals/rejections from this superseded run used `B-TEST-*` IDs. Current verification should not create positive synthetic approvals/rejections. Clean up the historical rows in sandbox only via:

```sql
DELETE FROM mip_app.approvals WHERE borrower_id LIKE 'B-TEST-%';
DELETE FROM mip_app.audit_events WHERE borrower_id LIKE 'B-TEST-%';
```
