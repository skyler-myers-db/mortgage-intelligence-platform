# E2E live verification — 2026-04-23

Base URL: `https://mip-app-2543889327043640.aws.databricksapps.com`  
Auth: `databricks auth token -p DEFAULT` (OAuth bearer, skyler@entrada.ai)  
Synthetic test-id prefix: `B-TEST-*`

## Endpoint results

| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |
| --- | --- | ---: | ---: | :---: | --- |
| health | `GET /api/health` | 200 | 687 | yes | keys/len: app_env, breaker_state_changes_last_hour, circuit_breakers, counters_persistence, dependencies, fallback_identity_fallbacks_total, log_export, mode |
| portfolio.unfiltered | `POST /api/portfolio/preview` | 200 | 415 | yes | keys/len: approved_count, avg_score, cost_per_contact, data_refreshed_at, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.chicago | `POST /api/portfolio/preview` | 200 | 412 | yes | keys/len: approved_count, avg_score, cost_per_contact, data_refreshed_at, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| portfolio.chicago.owner.25pct | `POST /api/portfolio/preview` | 200 | 426 | yes | keys/len: approved_count, avg_score, cost_per_contact, data_refreshed_at, high_intent_leads, in_outreach_count, marketable_population, offers_recommended |
| segments | `GET /api/segments` | 503 | 460 | NO | ERROR: {"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"8ee572c329b1437983726f08d4c30e55"} |
| leads.all | `GET /api/leads` | 503 | 487 | NO | ERROR: {"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"34721e2c71974c24a0318d90138b706c"} |
| leads.itm | `GET /api/leads?segment=itm` | 503 | 438 | NO | ERROR: {"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"51fc62adfb244417850056301b804d62"} |
| borrower.pick | `INFO /api/leads` | 0 | 0 | yes | ERROR: no real borrower_id available from /api/leads; skipping borrower-dependent probes |
| outreach.approve.synthetic | `POST /api/outreach/approve` | 200 | 657 | yes | keys/len: approval_id, approved, audit_event_id |
| outreach.reject.synthetic | `POST /api/outreach/reject` | 200 | 661 | yes | keys/len: approval_id, audit_event_id, rejected |
| audit.events | `GET /api/audit/events?limit=10` | 200 | 711 | yes | keys/len: [array len=10] |
| genie.message | `POST /api/genie/message` | 200 | 866 | yes | keys/len: answer, conversation_id, follow_up_questions, metric_value, question, source, table_rows, trusted_assets |
| admin.rules | `GET /api/admin/rules` | 200 | 391 | yes | keys/len: legacy_override, offer_rules_version, rules_edited_at, thresholds |
| admin.sources | `GET /api/admin/sources` | 200 | 399 | yes | keys/len: [array len=8] |
| admin.rules.no_admin_header | `GET /api/admin/rules` | 200 | 424 | yes | keys/len: legacy_override, offer_rules_version, rules_edited_at, thresholds |
| geo.state_rollups | `GET /api/geo/state-rollups` | 503 | 452 | NO | ERROR: {"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"c6ec92562c2649e3b70ca46c49423654"} |

## Clean payload samples

### health — `GET /api/health`

```json
{
  "status": "degraded",
  "mode": "live",
  "app_env": "sandbox",
  "warehouse_id": "81d08d4fa2d799e9",
  "dependencies": {
    "warehouse": "down",
    "lakebase": "down",
    "genie": "up"
  },
  "circuit_breakers": {
    "warehouse": "open",
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

### outreach.approve.synthetic — `POST /api/outreach/approve`

```json
{
  "approved": true,
  "approval_id": "7049fafa-08da-40fd-807f-401b31be30c7",
  "audit_event_id": "258e2ec6-6b0f-404c-a5b4-13c2ad29a3e2"
}
```

### outreach.reject.synthetic — `POST /api/outreach/reject`

```json
{
  "rejected": true,
  "approval_id": "95deafdd-2841-4942-94ca-4e0047849e35",
  "audit_event_id": "e813da33-5deb-426b-a581-a698b1fadda5"
}
```

### audit.events — `GET /api/audit/events?limit=10`

```json
{
  "event_id": "e813da33-5deb-426b-a581-a698b1fadda5",
  "actor": "skyler@entrada.ai",
  "action": "outreach.reject",
  "entity_type": "approval",
  "entity_id": "95deafdd-2841-4942-94ca-4e0047849e35",
  "payload_json": {
    "offer_code": "refi",
    "approval_id": "95deafdd-2841-4942-94ca-4e0047849e35",
    "borrower_id": "B-TEST-C5C984A5"
  },
  "evidence_ids": [],
  "created_at": "2026-04-23T14:36:30.630954+00:00",
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
  "rules_edited_at": "2026-04-23 14:27:52.06284",
  "thresholds": [
    {
      "key": "mip_min_spread_bps",
      "value": 75.0,
      "unit": "bps",
      "label": "Min spread (bps)",
      "description": "Minimum rate spread vs. market before a borrower is considered in the money.",
      "sort_order": 1,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_min_equity_pct",
      "value": 15.0,
      "unit": "pct",
      "label": "Min equity (%)",
      "description": "Minimum equity percentage required to qualify as in the money.",
      "sort_order": 2,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_heloc_equity_min_pct",
      "value": 35.0,
      "unit": "pct",
      "label": "HELOC equity floor (%)",
      "description": "Equity floor required for HELOC eligibility and refi+HELOC cross-sell.",
      "sort_order": 3,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_cashout_equity_min_pct",
      "value": 25.0,
      "unit": "pct",
      "label": "Cash-out equity floor (%)",
      "description": "Equity floor required for cash-out refi eligibility when rate economics are absent.",
      "sort_order": 4,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_retention_min_spread_bps",
      "value": 50.0,
      "unit": "bps",
      "label": "Retention min spread (bps)",
      "description": "Lowered spread 
```

### admin.sources — `GET /api/admin/sources`

```json
{
  "name": "Cotality Public Records",
  "status": "permission_denied",
  "rows": null,
  "last_updated": null,
  "note": "App identity lacks USE SCHEMA/SELECT on mip.silver.property_master"
}
```

### admin.rules.no_admin_header — `GET /api/admin/rules`

```json
{
  "offer_rules_version": "itm_77eddaa7d767",
  "rules_edited_at": "2026-04-23 14:27:52.06284",
  "thresholds": [
    {
      "key": "mip_min_spread_bps",
      "value": 75.0,
      "unit": "bps",
      "label": "Min spread (bps)",
      "description": "Minimum rate spread vs. market before a borrower is considered in the money.",
      "sort_order": 1,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_min_equity_pct",
      "value": 15.0,
      "unit": "pct",
      "label": "Min equity (%)",
      "description": "Minimum equity percentage required to qualify as in the money.",
      "sort_order": 2,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_heloc_equity_min_pct",
      "value": 35.0,
      "unit": "pct",
      "label": "HELOC equity floor (%)",
      "description": "Equity floor required for HELOC eligibility and refi+HELOC cross-sell.",
      "sort_order": 3,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_cashout_equity_min_pct",
      "value": 25.0,
      "unit": "pct",
      "label": "Cash-out equity floor (%)",
      "description": "Equity floor required for cash-out refi eligibility when rate economics are absent.",
      "sort_order": 4,
      "last_updated": "2026-04-23 14:27:52.06284"
    },
    {
      "key": "mip_retention_min_spread_bps",
      "value": 50.0,
      "unit": "bps",
      "label": "Retention min spread (bps)",
      "description": "Lowered spread 
```

## Red flags

- segments: status=503 error={"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"8ee572c329b1437983726f08d4c30e55"}
- leads.all: status=503 error={"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"34721e2c71974c24a0318d90138b706c"}
- leads.itm: status=503 error={"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"51fc62adfb244417850056301b804d62"}
- geo.state_rollups: status=503 error={"detail":"warehouse dependency is down: circuit breaker is open","retryable":true,"dependency":"warehouse","correlation_id":"c6ec92562c2649e3b70ca46c49423654"}

## Teardown

Synthetic approvals/rejections written with `B-TEST-*` IDs. Clean up via:

```sql
DELETE FROM mip_app.approvals WHERE borrower_id LIKE 'B-TEST-%';
DELETE FROM mip_app.audit_events WHERE borrower_id LIKE 'B-TEST-%';
```
