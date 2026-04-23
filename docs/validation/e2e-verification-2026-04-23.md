# E2E live verification — 2026-04-23

Base URL: `https://mip-app-2543889327043640.aws.databricksapps.com`  
Auth: `databricks auth token -p DEFAULT` (OAuth bearer, skyler@entrada.ai)  
Synthetic test-id prefix: `B-TEST-*`

## Endpoint results

| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |
| --- | --- | ---: | ---: | :---: | --- |
| health | `GET /api/health` | 200 | 1139 | yes | keys/len: app_env, breaker_state_changes_last_hour, circuit_breakers, counters_persistence, dependencies, log_export, mode, recent_errors_count |
| portfolio.unfiltered | `POST /api/portfolio/preview` | 200 | 398 | yes | keys/len: approved_count, avg_score, cost_per_contact, data_refreshed_at, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.chicago | `POST /api/portfolio/preview` | 200 | 378 | yes | keys/len: approved_count, avg_score, cost_per_contact, data_refreshed_at, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.chicago.owner.25pct | `POST /api/portfolio/preview` | 200 | 401 | yes | keys/len: approved_count, avg_score, cost_per_contact, data_refreshed_at, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| segments | `GET /api/segments` | 200 | 938 | yes | keys/len: [array len=4] |
| leads.all | `GET /api/leads` | 200 | 1297 | yes | keys/len: [array len=500] |
| leads.itm | `GET /api/leads?segment=itm` | 200 | 1334 | yes | keys/len: [array len=500] |
| borrower.detail | `GET /api/borrowers/B-102FL7THC6Q3L` | 200 | 942 | yes | keys/len: approval_status, avm_value, borrower_id, city, clip, clip_id, confidence, current_lien_balance |
| borrower.evidence | `GET /api/borrowers/B-102FL7THC6Q3L/evidence` | 200 | 897 | yes | keys/len: [array len=8] |
| offers.recommend | `POST /api/offers/recommend` | 200 | 1454 | yes | keys/len: alternatives, borrower_id, confidence, evidence_ids, offer_code, offer_type, product_label, rationale |
| outreach.draft | `POST /api/outreach/draft` | 200 | 903 | yes | keys/len: body, borrower_id, channel, offer_code, status, subject |
| outreach.approve.synthetic | `POST /api/outreach/approve` | 200 | 622 | yes | keys/len: approval_id, approved, audit_event_id |
| outreach.reject.synthetic | `POST /api/outreach/reject` | 200 | 655 | yes | keys/len: approval_id, audit_event_id, rejected |
| audit.events | `GET /api/audit/events?limit=10` | 200 | 586 | yes | keys/len: [array len=10] |
| genie.message | `POST /api/genie/message` | 200 | 1050 | yes | keys/len: answer, conversation_id, follow_up_questions, metric_value, question, source, table_rows, trusted_assets |
| admin.rules | `GET /api/admin/rules` | 200 | 412 | yes | keys/len: legacy_override, offer_rules_version, rules_edited_at, thresholds |
| admin.sources | `GET /api/admin/sources` | 503 | 5359 | NO | ERROR: {"detail":"warehouse dependency is down: DatabricksSqlError: Databricks SQL statement did not succeed (state='FAILED' statement_id='01f13ee6-bd80-1760-ba9a-ac8d |
| geo.state_rollups | `GET /api/geo/state-rollups` | 200 | 406 | yes | keys/len: rollups, snapshot_date |

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
    "genie": "closed",
    "lakebase": "closed"
  }
}
```

### portfolio.unfiltered — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 5156184,
  "high_intent_leads": 147742,
  "top_tier_opportunities": 3081,
  "offers_recommended": 4468007,
  "avg_score": 36,
  "trends": {
    "marketable_population": {
      "series": [
        5156184.0,
        5156184.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "high_intent_leads": {
      "series": [
        147742.0,
        147742.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "top_tier_opportunities": {
      "series": [
        0.0,
        3081.0
      ],
      "delta_pct": null,
      "direction": "flat"
    },
    "offers_recommended": {
      "series": [
        4468137.0,
        4468007.0
      ],
      "delta_pct": -0.0,
      "direction": "flat"
    },
    "avg_score": {
      "series": [
        42.0,
        36.0
      ],
      "delta_pct": -14.3,
      "direction": "down"
    },
    "approved_count": {
      "series": [
        1.0,
        1.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "in_outreach_count": {
      "series": [
        0.0,
        0.0
      ],
      "delta_pct": null,
      "direction": "flat"
    }
  }
}
```

### portfolio.chicago — `POST /api/portfolio/preview`

```json
{
  "marketable_population": 1851040,
  "high_intent_leads": 70939,
  "top_tier_opportunities": 1163,
  "offers_recommended": 1504616,
  "avg_score": 35,
  "trends": {
    "marketable_population": {
      "series": [
        5156184.0,
        5156184.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "high_intent_leads": {
      "series": [
        147742.0,
        147742.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "top_tier_opportunities": {
      "series": [
        0.0,
        3081.0
      ],
      "delta_pct": null,
      "direction": "flat"
    },
    "offers_recommended": {
      "series": [
        4468137.0,
        4468007.0
      ],
      "delta_pct": -0.0,
      "direction": "flat"
    },
    "avg_score": {
      "series": [
        42.0,
        36.0
      ],
      "delta_pct": -14.3,
      "direction": "down"
    },
    "approved_count": {
      "series": [
        1.0,
        1.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "in_outreach_count": {
      "series": [
        0.0,
        0.0
      ],
      "delta_pct": null,
      "direction": "flat"
    }
  }
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
  "trends": {
    "marketable_population": {
      "series": [
        5156184.0,
        5156184.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "high_intent_leads": {
      "series": [
        147742.0,
        147742.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "top_tier_opportunities": {
      "series": [
        0.0,
        3081.0
      ],
      "delta_pct": null,
      "direction": "flat"
    },
    "offers_recommended": {
      "series": [
        4468137.0,
        4468007.0
      ],
      "delta_pct": -0.0,
      "direction": "flat"
    },
    "avg_score": {
      "series": [
        42.0,
        36.0
      ],
      "delta_pct": -14.3,
      "direction": "down"
    },
    "approved_count": {
      "series": [
        1.0,
        1.0
      ],
      "delta_pct": 0.0,
      "direction": "flat"
    },
    "in_outreach_count": {
      "series": [
        0.0,
        0.0
      ],
      "delta_pct": null,
      "direction": "flat"
    }
  }
}
```

### segments — `GET /api/segments`

```json
{
  "code": "equity",
  "name": "Home Equity Candidate",
  "count": 3141667,
  "delta": "+0%",
  "avg_score": 40,
  "description": "Strong equity and prior cash-out/HELOC propensity.",
  "color": "#66C5FF"
}
```

### leads.all — `GET /api/leads`

```json
{
  "borrower_id": "B-102FL7THC6Q3L",
  "display_name": "Owner 3b3ba2e0",
  "city": "CALUMET CITY",
  "state": "IL",
  "zip": "604092222",
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
    "ev-b4fba688be13",
    "ev-ae027341e9f1",
    "ev-d3356f99ea2e"
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
  "zip": "604092222",
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
    "ev-b4fba688be13",
    "ev-ae027341e9f1",
    "ev-d3356f99ea2e"
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
  "zip": "604092222",
  "clip": ""
}
```

### borrower.evidence — `GET /api/borrowers/B-102FL7THC6Q3L/evidence`

```json
{
  "evidence_id": "ev-b4fba688be13",
  "source_product": "Voluntary Lien",
  "source_table": "mip.silver.lien_current",
  "signal_type": "rate_spread",
  "signal_value": "+397 bps",
  "display_text": "Current lien rate is 397 bps vs. par.",
  "confidence": 0.92,
  "timestamp": "2026-04-21 20:37:48.869"
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
  "approval_id": "5f7b372d-57ce-494c-9c51-95edfd5a2eb3",
  "audit_event_id": "b07e3372-885b-4eba-9ce2-79ed6ef2e588"
}
```

### outreach.reject.synthetic — `POST /api/outreach/reject`

```json
{
  "rejected": true,
  "approval_id": "79b91ff0-207f-43ed-b69e-7758e32647ae",
  "audit_event_id": "af848960-c6d0-4711-a144-73e9b358601c"
}
```

### audit.events — `GET /api/audit/events?limit=10`

```json
{
  "event_id": "af848960-c6d0-4711-a144-73e9b358601c",
  "actor": "skyler@entrada.ai",
  "action": "outreach.reject",
  "entity_type": "approval",
  "entity_id": "79b91ff0-207f-43ed-b69e-7758e32647ae",
  "payload_json": {
    "offer_code": "refi",
    "approval_id": "79b91ff0-207f-43ed-b69e-7758e32647ae",
    "borrower_id": "B-TEST-1A53AE8F"
  },
  "evidence_ids": [],
  "created_at": "2026-04-23T07:33:29.549978+00:00",
  "event_type": "OUTREACH_REJECT",
  "subject_clip": null,
  "subject_segment": null,
  "request_id": null
}
```

### genie.message — `POST /api/genie/message`

```json
{
  "conversation_id": "fallback-conv",
  "question": "Which zips have the most in-the-money refi candidates?",
  "answer": "The top in-the-money ZIPs are 60611 Chicago (~1,420 borrowers), 78704 Austin (~1,180), 94110 San Francisco (~960), 98103 Seattle (~720), and 33132 Miami (~640). Together they cover about 38% of the 6-state ITM book.",
  "source": "fallback",
  "trusted_assets": [
    "mip.gold.lead_population",
    "mip.semantics.lead_generation_metric_view"
  ],
  "metric_value": null
}
```

### admin.rules — `GET /api/admin/rules`

```json
{
  "offer_rules_version": "itm_77eddaa7d767",
  "rules_edited_at": "2026-04-23 07:09:52.711438",
  "thresholds": [
    {
      "key": "mip_min_spread_bps",
      "value": 75.0,
      "unit": "bps",
      "label": "Min spread (bps)",
      "description": "Minimum rate spread vs. market before a borrower is considered in the money.",
      "sort_order": 1,
      "last_updated": "2026-04-23 07:09:52.711438"
    },
    {
      "key": "mip_min_equity_pct",
      "value": 15.0,
      "unit": "pct",
      "label": "Min equity (%)",
      "description": "Minimum equity percentage required to qualify as in the money.",
      "sort_order": 2,
      "last_updated": "2026-04-23 07:09:52.711438"
    },
    {
      "key": "mip_heloc_equity_min_pct",
      "value": 35.0,
      "unit": "pct",
      "label": "HELOC equity floor (%)",
      "description": "Equity floor required for HELOC eligibility and refi+HELOC cross-sell.",
      "sort_order": 3,
      "last_updated": "2026-04-23 07:09:52.711438"
    },
    {
      "key": "mip_cashout_equity_min_pct",
      "value": 25.0,
      "unit": "pct",
      "label": "Cash-out equity floor (%)",
      "description": "Equity floor required for cash-out refi eligibility when rate economics are absent.",
      "sort_order": 4,
      "last_updated": "2026-04-23 07:09:52.711438"
    },
    {
      "key": "mip_retention_min_spread_bps",
      "value": 50.0,
      "unit": "bps",
      "label": "Retention min spread (bps)",
      "description": "Lowered sp
```

### geo.state_rollups — `GET /api/geo/state-rollups`

```json
{
  "rollups": [
    {
      "state": "IL",
      "addressable": 1851040,
      "in_the_money": 70939,
      "top_tier_opportunities": 1163,
      "avg_score": 35,
      "top_segment_code": "equity"
    },
    {
      "state": "CA",
      "addressable": 900371,
      "in_the_money": 18724,
      "top_tier_opportunities": 308,
      "avg_score": 38,
      "top_segment_code": "equity"
    },
    {
      "state": "FL",
      "addressable": 752572,
      "in_the_money": 21528,
      "top_tier_opportunities": 283,
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
      "top_tier_opportunities": 977,
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
  "snapshot_date": "2026-04-23"
}
```

## Red flags

- admin.sources: status=503 error={"detail":"warehouse dependency is down: DatabricksSqlError: Databricks SQL statement did not succeed (state='FAILED' statement_id='01f13ee6-bd80-1760-ba9a-ac8d

## Teardown

Synthetic approvals/rejections written with `B-TEST-*` IDs. Clean up via:

```sql
DELETE FROM mip_app.approvals WHERE borrower_id LIKE 'B-TEST-%';
DELETE FROM mip_app.audit_events WHERE borrower_id LIKE 'B-TEST-%';
```
