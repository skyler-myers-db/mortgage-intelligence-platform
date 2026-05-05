# E2E live verification — 2026-04-23

Base URL: `https://mip-app-2543889327043640.aws.databricksapps.com`  
Auth: `databricks auth token -p DEFAULT` (OAuth bearer, skyler@entrada.ai)  
Synthetic test-id prefix: `B-TEST-*`

## Endpoint results

| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |
| --- | --- | ---: | ---: | :---: | --- |
| health | `GET /api/health` | 200 | 506 | yes | keys/len: app_env, breaker_state_changes_last_hour, circuit_breakers, counters_persistence, dependencies, fallback_identity_fallbacks_process_total, fallback_identity_fallbacks_total, log_export |
| portfolio.unfiltered | `POST /api/portfolio/preview` | 200 | 1804 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.chicago | `POST /api/portfolio/preview` | 200 | 1429 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.chicago.owner.25pct | `POST /api/portfolio/preview` | 200 | 1376 | yes | keys/len: approved_count, avg_score, data_refreshed_at, day_zero, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| segments | `GET /api/segments` | 200 | 842 | yes | keys/len: [array len=6] |
| leads.all | `GET /api/leads` | 200 | 1253 | yes | keys/len: [array len=500] |
| leads.itm | `GET /api/leads?segment=itm` | 200 | 1247 | yes | keys/len: [array len=500] |
| borrower.detail | `GET /api/borrowers/B-102FL7THC6Q3L` | 200 | 1476 | yes | keys/len: approval_status, avm_value, borrower_id, city, clip, clip_id, confidence, current_lien_balance |
| borrower.evidence | `GET /api/borrowers/B-102FL7THC6Q3L/evidence` | 200 | 952 | yes | keys/len: [array len=8] |
| offers.recommend | `POST /api/offers/recommend` | 200 | 1358 | yes | keys/len: alternatives, borrower_id, confidence, evidence_ids, offer_code, offer_type, product_label, rationale |
| outreach.draft | `POST /api/outreach/draft` | 200 | 839 | yes | keys/len: body, borrower_id, channel, offer_code, status, subject |
| outreach.approve.synthetic | `POST /api/outreach/approve` | 200 | 4037 | yes | keys/len: approval_id, approved, audit_event_id |
| outreach.reject.synthetic | `POST /api/outreach/reject` | 200 | 4016 | yes | keys/len: approval_id, audit_event_id, rejected |
| audit.events | `GET /api/audit/events?limit=10` | 200 | 957 | yes | keys/len: [array len=10] |
| genie.message | `POST /api/genie/message` | 200 | 12474 | yes | keys/len: actions, answer, conversation_id, elapsed_ms, follow_up_questions, message_id, metric_value, proof |
| admin.rules | `GET /api/admin/rules` | 200 | 893 | yes | keys/len: legacy_override, offer_rules_version, rules_edited_at, thresholds |
| admin.sources | `GET /api/admin/sources` | 200 | 960 | yes | keys/len: [array len=8] |
| geo.state_rollups | `GET /api/geo/state-rollups` | 200 | 864 | yes | keys/len: rollups, snapshot_date |

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

### portfolio.unfiltered — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 5156184,
  "high_intent_leads": 147742,
  "top_tier_opportunities": 3074,
  "offers_recommended": 4468137,
  "avg_score": 36,
  "trends": {
    "marketable_population": {
      "series": [
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
        147742.0
      ],
      "delta_pct": 0.0,
      "direction": "flat",
      "comparison_label": "vs 2026-04-22"
    },
    "top_tier_opportunities": {
      "series": [
        3081.0,
        3074.0
      ],
      "delta_pct": -0.2,
      "direction": "flat",
      "comparison_label": "vs 2026-04-23"
    },
    "offers_recommended": {
      "series": [
        4468137.0,
        4468007.0,
        4468137.0
      ],
      "delta_pct": 0.0,
      "direction": "flat",
      "comparison_label": "vs 2026-04-22"
    },
    "avg_score": {
      "series": [
        42.0,
        36.0,
        36.0
      ],
      "delta_pct": -14.3,
      "direction": "down",
      "comparison_label": "vs 2026-04-22"
    },
    "approved_count": {
      "series": [
        1.0,
        1.0,
        2.0
      ],
      "delta_pct": 100.0,
      "direction": "up",
      "comparison_label": "vs 2026-04-22"
    },
    "in_outreach_count": {
      "series": [
        0.0,
        0.0,
        0.0
      ],
      "delta_pct": null,
      "direct
```

### portfolio.chicago — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 1851040,
  "high_intent_leads": 70939,
  "top_tier_opportunities": 1162,
  "offers_recommended": 1504711,
  "avg_score": 35,
  "trends": {}
}
```

### portfolio.chicago.owner.25pct — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 924898,
  "high_intent_leads": 48342,
  "top_tier_opportunities": 876,
  "offers_recommended": 924898,
  "avg_score": 39,
  "trends": {}
}
```

### segments — `GET /api/segments`

```json
{
  "code": "itm",
  "name": "In the Money",
  "count": 147742,
  "delta": "+0%",
  "avg_score": 60,
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
  "clip": "9154364327",
  "segment_codes": [
    "itm",
    "investor",
    "equity"
  ],
  "equity_estimate": 153163,
  "rate_spread_bps": 397,
  "opportunity_score": 86,
  "confidence": 81,
  "recommended_offer": "Refinance + HELOC",
  "why_now": "Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.",
  "evidence_ids": [
    "ev-2839ff827d7e",
    "ev-93da831520d8",
    "ev-5a6e84f25fed"
  ],
  "approval_status": "pending",
  "is_owner_occupied": false,
  "is_investor": true,
  "related_property_count": 346,
  "current_lien_balance": 15000,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": false
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
  "clip": "9154364327",
  "segment_codes": [
    "itm",
    "investor",
    "equity"
  ],
  "equity_estimate": 153163,
  "rate_spread_bps": 397,
  "opportunity_score": 86,
  "confidence": 81,
  "recommended_offer": "Refinance + HELOC",
  "why_now": "Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.",
  "evidence_ids": [
    "ev-2839ff827d7e",
    "ev-93da831520d8",
    "ev-5a6e84f25fed"
  ],
  "approval_status": "pending",
  "is_owner_occupied": false,
  "is_investor": true,
  "related_property_count": 346,
  "current_lien_balance": 15000,
  "second_pos_amount": 0,
  "has_permit": false,
  "listed_for_sale": false
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
  "evidence_id": "ev-2839ff827d7e",
  "source_product": "Voluntary Lien",
  "source_table": "mip.silver.lien_current",
  "signal_type": "rate_spread",
  "signal_value": "+397 bps",
  "display_text": "Current lien rate is 397 bps vs. par.",
  "confidence": 0.92,
  "timestamp": "2026-05-04 22:05:20.385"
}
```

### offers.recommend — `POST /api/offers/recommend`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "offer_code": "refi_plus_heloc",
  "offer_type": "refi_plus_heloc",
  "product_label": "Refinance + HELOC",
  "confidence": 81,
  "rationale": "Rate is well above current market rates and the home has very strong home equity -- a strong candidate for a refinance with a HELOC alongside it."
}
```

### outreach.draft — `POST /api/outreach/draft`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "offer_code": "OFFER-B-102FL7THC6Q3L",
  "channel": "email",
  "subject": "Refinance + HELOC opportunity for Owner 3b3ba2e0",
  "body": "Hi Owner 3b3ba2e0,\n\nBased on recent public-record signals in CALUMET CITY, IL, you may qualify for Refinance + HELOC. Current rate sits meaningfully above market and the home carries strong equity -- a refinance with a HELOC cross-sell fits.\n\nReply to this note and a licensed officer will follow up. This draft is for human review only; no outreach has been sent.",
  "status": "draft"
}
```

### outreach.approve.synthetic — `POST /api/outreach/approve`

```json
{
  "approved": true,
  "approval_id": "2bc61ff7-e46f-4027-9309-81d7600b2fee",
  "audit_event_id": "b76abd2b-e2da-403c-a19e-5afe6a853ee8"
}
```

### outreach.reject.synthetic — `POST /api/outreach/reject`

```json
{
  "rejected": true,
  "approval_id": "d40f7eae-0eac-4d61-8a33-c27e00d27eea",
  "audit_event_id": "055cbe55-2b96-42f4-b14c-368a65d9602c"
}
```

### audit.events — `GET /api/audit/events?limit=10`

```json
{
  "event_id": "055cbe55-2b96-42f4-b14c-368a65d9602c",
  "actor": "skyler@entrada.ai",
  "action": "outreach.reject",
  "entity_type": "approval",
  "entity_id": "d40f7eae-0eac-4d61-8a33-c27e00d27eea",
  "payload_json": {
    "offer_code": "refi",
    "approval_id": "d40f7eae-0eac-4d61-8a33-c27e00d27eea",
    "borrower_id": "B-TEST-71F5EF12"
  },
  "evidence_ids": [],
  "created_at": "2026-05-05T17:54:08.909307+00:00",
  "event_type": "OUTREACH_REJECT",
  "subject_clip": null,
  "subject_segment": null,
  "request_id": null
}
```

### genie.message — `POST /api/genie/message`

```json
{
  "conversation_id": "01f148ab6b781806b0224bd668f44efb",
  "question": "Which zips have the most in-the-money refi candidates?",
  "answer": "You want to see which ZIP codes have the highest number of borrowers who are currently in-the-money for refinancing.\n\nThe ZIP codes with the most in-the-money refi candidates are led by **60617** with 1,503 borrowers, followed by **60628** (1,482), **60629** (1,387), **60643** (1,108), and **60620** (1,095). These areas have the highest counts of borrowers who are currently in a favorable position for refinancing. \n\nSource: mip.gold.borrower_360",
  "source": "genie",
  "trusted_assets": [
    "mip.gold.borrower_360"
  ],
  "message_id": "01f148ab6b831cbbaee2bb0207acc8e4"
}
```

### admin.rules — `GET /api/admin/rules`

```json
{
  "offer_rules_version": "itm_77eddaa7d767",
  "rules_edited_at": "2026-05-04 21:55:43.474781",
  "thresholds": [
    {
      "key": "mip_min_spread_bps",
      "value": 75.0,
      "unit": "bps",
      "label": "Min spread (bps)",
      "description": "Minimum rate spread vs. market before a borrower is considered in the money.",
      "sort_order": 1,
      "last_updated": "2026-05-04 21:55:43.474781"
    },
    {
      "key": "mip_min_equity_pct",
      "value": 15.0,
      "unit": "pct",
      "label": "Min equity (%)",
      "description": "Minimum equity percentage required to qualify as in the money.",
      "sort_order": 2,
      "last_updated": "2026-05-04 21:55:43.474781"
    },
    {
      "key": "mip_heloc_equity_min_pct",
      "value": 35.0,
      "unit": "pct",
      "label": "HELOC equity floor (%)",
      "description": "Equity floor required for HELOC eligibility and refi+HELOC cross-sell.",
      "sort_order": 3,
      "last_updated": "2026-05-04 21:55:43.474781"
    },
    {
      "key": "mip_cashout_equity_min_pct",
      "value": 25.0,
      "unit": "pct",
      "label": "Cash-out equity floor (%)",
      "description": "Equity floor required for cash-out refi eligibility when rate economics are absent.",
      "sort_order": 4,
      "last_updated": "2026-05-04 21:55:43.474781"
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
{
  "name": "Cotality Public Records",
  "status": "live",
  "rows": 5192913,
  "last_updated": "2026-05-04 22:05:22.99",
  "note": "Delta Share \u00b7 nightly"
}
```

### geo.state_rollups — `GET /api/geo/state-rollups`

```json
{
  "rollups": [
    {
      "state": "IL",
      "addressable": 1851040,
      "in_the_money": 70939,
      "top_tier_opportunities": 1162,
      "avg_score": 35,
      "top_segment_code": "equity"
    },
    {
      "state": "CA",
      "addressable": 900371,
      "in_the_money": 18724,
      "top_tier_opportunities": 305,
      "avg_score": 38,
      "top_segment_code": "equity"
    },
    {
      "state": "FL",
      "addressable": 752572,
      "in_the_money": 21528,
      "top_tier_opportunities": 282,
      "avg_score": 38,
      "top_segment_code": "equity"
    },
    {
      "state": "TX",
      "addressable": 750962,
      "in_the_money": 19323,
      "top_tier_opportunities": 331,
      "avg_score": 37,
      "top_segment_code": "equity"
    },
    {
      "state": "WA",
      "addressable": 737682,
      "in_the_money": 15646,
      "top_tier_opportunities": 975,
      "avg_score": 36,
      "top_segment_code": "equity"
    },
    {
      "state": "CO",
      "addressable": 163557,
      "in_the_money": 1582,
      "top_tier_opportunities": 19,
      "avg_score": 35,
      "top_segment_code": "equity"
    }
  ],
  "snapshot_date": "2026-05-04"
}
```

## Red flags

(none)

## Teardown

Synthetic approvals/rejections written with `B-TEST-*` IDs. Clean up via:

```sql
DELETE FROM mip_app.approvals WHERE borrower_id LIKE 'B-TEST-%';
DELETE FROM mip_app.audit_events WHERE borrower_id LIKE 'B-TEST-%';
```
