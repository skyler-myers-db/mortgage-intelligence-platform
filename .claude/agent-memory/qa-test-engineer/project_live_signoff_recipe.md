---
name: live-signoff-recipe
description: How to run a live signoff of the deployed mip-app -- headless browser driving via bearer header (CORRECTED 2026-08-07), plus the API-layer contracts (Genie enhancements, feedback PII, capabilities honesty, growth-agent reconciliation).
metadata:
  type: project
---

**CORRECTION 2026-08-07: the deployed UI IS drivable headlessly.** The earlier
note here ("OAuth-shell-gated, headless drivers can't log in") was wrong --
that only applies to an *interactive* browser session with no auth header.
A Playwright context created with
`extraHTTPHeaders: { Authorization: 'Bearer $TOK' }` gets the full SPA
(`GET /` → 200) and renders every route on live UC data. This is exactly what
the repo's own live specs do (`frontend/tests/e2e/*.spec.ts` read
`MIP_APP_URL` + `MIP_BEARER_TOKEN`/`DATABRICKS_TOKEN`).

**Driving it without touching the repo:** write a standalone `.mjs` in the
scratchpad and `import { chromium } from '<abs path>/frontend/node_modules/playwright-core/index.mjs'`
(absolute-path ESM import -- a scratchpad script can't resolve the bare
specifier, and `cd frontend` doesn't help since ESM resolves from the
importing file's dir). Node writes screenshots anywhere, so this sidesteps the
Playwright-MCP repo sandbox in [[browser-signoff-ops]] entirely. Budget ~9-12s
settle after `waitForSelector('.app-shell')` for react-query to resolve.

Run live signoffs at the API layer too -- it binds the same contract the UI uses.

**Why:** repeated live audits against https://mip-app-2543889327043640.aws.databricksapps.com
need the same auth mint + the same non-obvious contract facts each time.

**How to apply:**
- Auth mint (verified): `TOK=$(databricks auth token --host https://dbc-3aa503a9-4fa8.cloud.databricks.com | .venv/bin/python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")`. Send `Authorization: Bearer $TOK`. Always REDACT the token in output.
- API prefix is `/api/v1` (frontend `apiPath()` rewrites `/api/*`→`/api/v1/*`). Hit `/api/v1/...` directly.
- Genie space rate limit ~5 questions/min. Only `POST /api/v1/genie/message` costs a model turn; `/genie/start` (Lakebase read) does NOT. Budget <=4 message turns; put a hard counter in the harness.
- **Genie enhancement contract** ([[project_genie_regression_contract]]): the LIVE-Genie narrative path (`source=="genie"`) populates `follow_up_questions`, `reasoning_trace` ([{kind,content}], kinds are `THOUGHT_TYPE_*`), `native_visualization` (only when a viz attachment lands this turn), `genie_status` (e.g. `COMPLETED`). The deterministic `source=="trusted_sql"` canonical-overlay path leaves ALL FOUR empty/None (no fabrication). Chartable "...in each state?" → live genie; canonical equity/count questions → trusted_sql. This split is enforced in `backend/services/repositories/databricks_genie.py` (narrative build ~L417 vs `_canonical_genie_answer` ~L451+).
- **Feedback** (`POST /api/v1/genie/feedback`): helpful=true/no-comment → `{accepted:true, audit_event_id}`; comment with PII → 422 `"comment must not contain PII, raw identifiers, or placeholders"` and the raw text is NEVER echoed (the 422 detail is fixed); bogus/un-owned conversation → 403 `"conversation_id is not owned by the current actor"`; wrong content-type → 415. Audit row event_type `GENIE_FEEDBACK`, action `genie.feedback`, payload has `helpful`+`comment_present` booleans only, NO raw comment. Comment PII validation runs BEFORE the ownership check, so the phone-comment 422 test works even with any conversation id.
- **Capabilities** `GET /api/v1/admin/capabilities?live=1` returns 14 rows. Six live-provable rows reach `status=available`/`claimable=true`: genie_conversation_api, certified_metric_views, uc_function_tools, agent_eval, agent_orchestrator, lakebase_sync. `genie_native_visualization` stays `configured`/claimable=false (Beta download endpoint not rolled out on this workspace). `ai_gateway` is `not_provisioned`/claimable=false, detail `"Disabled (MIP_AI_GATEWAY off)."` (consistent with [[ai-gateway-proof-history]]).
- **Growth-agent reconciliation**: `POST /api/v1/growth-agent/workflows/daily_refi_brief/run` body `{"states":["IL"]}` → 200 with keys `broad_total`, `actionable_total` (NOT `broad`/`actionable`), `route` (e.g. `/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL`). Reconcile: GET `/api/v1/leads` with the route's querystring → `X-Total-Matching` header equals `actionable_total`. NOTE: `X-Total-Matching` is emitted when no `limit` param is passed; adding `&limit=N` suppressed it in one probe (low-sev header quirk, not a blocker).
- **Probing signed/edge values in the lead queue**: the DEFAULT `/api/v1/leads` view is all-positive `rate_spread_bps` (min 32 over 200 rows), so sign-handling bugs look clean there. `?segment=listed` is where the negatives live (56/200 negative, 11 zero, min -422 as of 2026-08-07). Generally: pick the segment, not the default view, when hunting for a value-range edge case. `?segment=heloc` and `?marketing_eligibility=All` return 0 rows -- not every filter value is valid.
- **False-positive to skip**: lead-queue rows carry `assigned_to_email` like `lo01@summit.example` (internal Summit LO identity for assignment) — that is not borrower PII. Don't flag it. Borrower `borrower_id` is masked `B-[0-9A-Z]{13}`.
