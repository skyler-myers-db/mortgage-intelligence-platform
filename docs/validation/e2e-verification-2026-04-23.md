# E2E live verification — 2026-04-23

Base URL: `https://mip-app-2543889327043640.aws.databricksapps.com`
Auth: `databricks auth token -p DEFAULT` (OAuth bearer, `skyler@entrada.ai`)
Verifier: `python3 tools/verify_live.py`
Synthetic test-id prefix used for approvals / rejections: `B-TEST-*`

## Headline verdict

**BLOCKER — `/api/leads` is broken in production.** The deployed repo selects
seven new secondary-filter columns (`is_owner_occupied`, `is_investor`,
`related_property_count`, `current_lien_balance`, `second_pos_amount`,
`has_permit`, `listed_for_sale`) from `mip.gold.lead_population`, but those
columns do **not** exist in the live table. Every `/api/leads` request returns
`UNRESOLVED_COLUMN.WITH_SUGGESTION … is_owner_occupied`, which trips the
warehouse circuit breaker. Once the breaker opens, it cascades 503s across the
**entire warehouse read surface**: borrower detail, borrower evidence, offer
recommend, outreach draft, admin rules, admin sources, and geo state rollups
all fail with `circuit breaker is open`. The only warehouse-backed routes that
survive are `/api/portfolio/preview` (served from a separate metric-view path)
and `/api/segments` (served from a different table). Lakebase writes (approve,
reject, audit) are unaffected.

## Endpoint results

| Endpoint | Method+Path | Status | Latency (ms) | Payload OK? | Notes |
| --- | --- | ---: | ---: | :---: | --- |
| health | `GET /api/health` | 200 | 2851 | yes | app_env, circuit_breakers, dependencies, recent_errors_count |
| portfolio.unfiltered | `POST /api/portfolio/preview` | 200 | 6483 | yes | marketable=5,156,184 high_intent=147,742 top_tier=3,081 offers=4,468,007 |
| portfolio.chicago | `POST /api/portfolio/preview` | 200 | 2878 | yes | marketable=1,851,040 high_intent=70,939 top_tier=1,163 offers=1,504,616 |
| portfolio.chicago+owner+25pct | `POST /api/portfolio/preview` | 200 | 2371 | yes | marketable=924,898 high_intent=48,342 top_tier=876 offers=924,898 |
| segments | `GET /api/segments` | 200 | 1480 | yes | array len=4 |
| leads.all | `GET /api/leads` | 503 | 7103 | **NO** | `UNRESOLVED_COLUMN … is_owner_occupied cannot be resolved. Did you mean [borrower_id, confidence, rank_overall, segment_codes, clip]` |
| leads.itm | `GET /api/leads?segment=itm` | 503 | 4201 | **NO** | circuit breaker open (cascade) |
| borrower.detail | `GET /api/borrowers/B-00001` | 503 | 506 | **NO** | circuit breaker open (cascade) |
| borrower.evidence | `GET /api/borrowers/B-00001/evidence` | 503 | 451 | **NO** | circuit breaker open (cascade) |
| offers.recommend | `POST /api/offers/recommend` | 503 | 457 | **NO** | circuit breaker open (cascade) |
| outreach.draft | `POST /api/outreach/draft` | 503 | 442 | **NO** | circuit breaker open (cascade) |
| outreach.approve.synthetic | `POST /api/outreach/approve` | 200 | 1071 | yes | `approval_id`, `audit_event_id` returned; Lakebase write succeeded |
| outreach.reject.synthetic | `POST /api/outreach/reject` | 200 | 767 | yes | `approval_id`, `audit_event_id` returned; Lakebase write succeeded |
| audit.events | `GET /api/audit/events?limit=10` | 200 | 806 | yes | array len=10; includes the two B-TEST synthetic rows I just wrote plus real view events |
| genie.message | `POST /api/genie/message` | 200 | 1225 | yes | `answer`, `conversation_id`, `metric_value`, `table_rows`, `trusted_assets`, `source` |
| admin.rules | `GET /api/admin/rules` | 503 | 451 | **NO** | circuit breaker open (cascade) |
| admin.sources | `GET /api/admin/sources` | 503 | 450 | **NO** | circuit breaker open (cascade) |
| geo.state_rollups | `GET /api/geo/state-rollups` | 503 | 412 | **NO** | circuit breaker open (cascade) |

A follow-up probe after the initial round returned the same `circuit breaker
is open` on `/api/leads`, confirming this is not a transient upstream hiccup —
the schema drift reliably reproduces on every request.

## Red flags

1. **`/api/leads` UNRESOLVED_COLUMN.** Root cause:
   `backend/services/repositories/databricks_repo.py:112` adds seven columns to
   `_LEAD_POPULATION_COLUMNS` that are not present in the live
   `mip.gold.lead_population` table. Live `DESCRIBE TABLE mip.gold.lead_population`
   returns 20 columns: `clip, borrower_id, display_name, city, state, zip,
   segment_codes, equity_estimate, equity_pct, rate_spread_bps,
   opportunity_score, confidence, recommended_offer, why_now, evidence_ids,
   approval_status, rank_overall, rank_within_state, population_version,
   refreshed_at`. None of the new secondary-filter columns are there. The DDL
   file (`sql/ddl/gold_lead_population.sql`) and CTAS
   (`sql/transformations/gold_lead_population.sql`) both declare the new
   columns, but the materialized table in UC was not rebuilt.

2. **Circuit breaker cascade.** One bad SQL wiped out every other warehouse
   read in the app. The resilience layer is doing what it's supposed to
   (failing closed to protect the warehouse) but a single schema drift grounds
   roughly 60% of the API surface. Consider either (a) shipping the table
   migration with the code change (bundle-level gate), or (b) splitting the
   breaker so `/api/leads` failures don't black out unrelated routes.

3. **Portfolio KPI trend sparklines are not filter-aware.** The `trends.*`
   series in filtered responses show the exact same historical values as the
   unfiltered response (e.g. `marketable_population.series = [5156184.0,
   5156184.0]` for all three variants). The current-period numbers DO filter
   correctly (5.1M → 1.85M → 925K), but the 7-day history in the trend
   sparkline is always the national rollup row
   (`state='_ALL' AND segment_code='_ALL'` in `mip.gold.funnel_snapshot_daily`).
   Users will see "Chicago owner-occupied ≥25% = 924,898 (flat vs 7 days ago:
   5,156,184)" which is misleading. Not a new regression from this cycle, but
   worth flagging.

4. **Static top-tier baseline.** `top_tier_opportunities.series` shows
   `[0.0, 3081.0]` with `delta_pct: null` — the earlier day of the 7-day
   snapshot history has no value, so the sparkline reads as "brand new metric."
   Cosmetic.

5. **Portfolio latency.** Unfiltered preview took **6.5 s** on a cold first
   hit; subsequent filtered hits were ~2.4–2.9 s. The 6 s first-hit is above
   the user-visible "instant" threshold for a landing-page KPI tile; consider
   priming the cache during warehouse warm-start.

## Clean payload samples

### `POST /api/portfolio/preview` — unfiltered

```json
{
  "marketable_population": 5156184,
  "high_intent_leads": 147742,
  "top_tier_opportunities": 3081,
  "offers_recommended": 4468007,
  "avg_score": 36,
  "data_refreshed_at": "2026-04-23T06:08:23.409000Z",
  "approved_count": 1,
  "in_outreach_count": 0,
  "projected_contact_to_app": null,
  "cost_per_contact": null
}
```

### `POST /api/portfolio/preview` — Chicago MSA + Owner-occupied + ≥ 25% equity

```json
{
  "marketable_population": 924898,
  "high_intent_leads": 48342,
  "top_tier_opportunities": 876,
  "offers_recommended": 924898,
  "avg_score": 39
}
```

Filter predicate IS being applied (5,156,184 → 1,851,040 → 924,898) — the
portfolio-preview filter bug fixed in the prior cycle is confirmed fixed in
prod.

### `GET /api/audit/events?limit=10` — first row

```json
{
  "event_id": "b6ee3f1c-df45-485b-993c-fc194f7b0ff6",
  "actor": "skyler@entrada.ai",
  "action": "outreach.reject",
  "entity_type": "approval",
  "entity_id": "8dde10a1-bdf0-4388-a981-dc618e638ad3",
  "payload_json": {
    "offer_code": "refi",
    "approval_id": "8dde10a1-bdf0-4388-a981-dc618e638ad3",
    "borrower_id": "B-TEST-A5BD2F99"
  },
  "evidence_ids": [],
  "created_at": "2026-04-23T06:45:12.348588+00:00",
  "event_type": "OUTREACH_REJECT"
}
```

Real borrower view events also land in the audit log with real Cotality CLIPs
(e.g. `subject_clip: "9154364327"`, `entity_id: "B-102FL7THC6Q3L"`), and the
synthetic approve/reject writes from this run show up at the top. Audit write
path is healthy.

### `GET /api/leads` — error payload

```json
{
  "detail": "warehouse dependency is down: DatabricksSqlError: Databricks SQL statement did not succeed (state='FAILED' statement_id='01f13edf-f569-1216-8ecb-ad97766de1f4'): [UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function parameter with name `is_owner_occupied` cannot be resolved. Did you mean one of the following? [`borrower_id`, `confidence`, `rank_overall`, `segment_codes`, `clip`]. SQLSTATE: 42703; line 1 pos 197",
  "retryable": true,
  "dependency": "warehouse"
}
```

## Remediation

Run the gold.lead_population DDL + CTAS against the dev catalog so the
secondary-filter columns exist, then let the breaker half-open probe re-close
it. Commands:

```bash
# Verify drift locally before anything else.
databricks sql query --warehouse-id 81d08d4fa2d799e9 \
  "DESCRIBE TABLE mip.gold.lead_population"

# Re-create the table with the new DDL and CTAS. Pick whichever path your
# bundle wires to — the refresh_silver job should cascade gold, but you can
# also run the DDL + CTAS directly if time-critical.
databricks bundle run refresh_silver -t dev
# OR, explicit:
databricks sql query --warehouse-id 81d08d4fa2d799e9 --file sql/ddl/gold_lead_population.sql
databricks sql query --warehouse-id 81d08d4fa2d799e9 --file sql/transformations/gold_lead_population.sql
```

Then close the breaker (or just wait for the half-open window):

```bash
TOKEN=$(databricks auth token -p DEFAULT | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -H "Authorization: Bearer $TOKEN" -X POST \
  "https://mip-app-2543889327043640.aws.databricksapps.com/api/admin/resilience/reset"
```

After the table is migrated, re-run `python3 tools/verify_live.py` — the 503
cascade should collapse and all seven cascaded endpoints should flip green.

## Teardown

Synthetic approvals/rejections were written with `B-TEST-*` IDs. Clean up via:

```sql
DELETE FROM mip_app.approvals    WHERE borrower_id LIKE 'B-TEST-%';
DELETE FROM mip_app.audit_events WHERE payload_json:borrower_id LIKE 'B-TEST-%';
```

Synthetic IDs written this run (for easy grep):
`B-TEST-A5BD2F99` (reject), `B-TEST-46E0A905` (approve). Both visible at the
top of `/api/audit/events?limit=10`.
