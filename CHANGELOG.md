# Changelog

All notable API-facing changes for the Mortgage Intelligence Platform are tracked here.

This project follows an additive-first API contract: new optional fields and new
versioned endpoints may be added in a minor release, while removals require a
deprecation window first.

## Unreleased

### 2026-08-07 growth co-pilot refusal families + refusal audit trail

- **Additive:** a refused co-pilot prompt on `POST /api/v1/growth-agent/agent/run`
  and `/agent/compose` now returns two new top-level 422 fields:
  `refusal_reason` (a stable machine code — `protected_class`,
  `instruction_override`, `cross_lender_targeting`, `unavailable_source`,
  `pii_request`, `unreviewed_criterion`) and `audit_event_id`. Clients should
  key off `refusal_reason`; the human-readable sentence in `detail[].msg` is
  copy and may be re-worded. Ordinary malformed-body 422s are unchanged and
  carry neither field. The 2026-07-07 PII posture stands: pydantic's
  `input`/`ctx`/`url` reflection is still stripped, and the refusal path never
  echoes the prompt.
- **Behavioral:** refusal messages now name the guard family that fired
  instead of one catch-all sentence, so a fair-lending targeting attempt is
  reported as a fair-lending refusal on the co-pilot exactly as it already was
  on Ask Genie. `unreviewed_criterion` keeps the historical sentence verbatim.
- **Behavioral:** every co-pilot refusal writes `growth_agent.refused_prompt`
  to the Lakebase audit ledger (actor, guard family, question digest — never
  prompt text). If that write fails the request returns 503 rather than a
  clean 422, so a refusal is never silently unrecorded.
- **Behavioral:** `genie.refused_prompt` records the true guard family instead
  of a hardcoded `protected_class`. New reason codes `protected_class_proxy`
  and `unreviewed_criterion` are governed values; the latter is the
  fail-closed unknown-criterion state that previously filed as a false
  fair-lending finding. Fair-lending review must read all three.
- No detector changed: the set of prompts refused by the protected-class
  scanner and the Genie prompt matcher is byte-for-byte identical
  (23,403-string differential check, zero refuse/allow flips).

### 2026-07-11 S7 economics scatter

- **Breaking (deliberate, single-consumer app API):**
  `GET /api/v1/analytics/economics` replaces the raw-row
  `equity_vs_spread` list with an `equity_spread` density-bin overview
  served from the new precomputed `mip.gold.equity_spread_points` gold
  table. The overview never ships borrower rows; real points moved to the
  new `GET /api/v1/analytics/economics/points` endpoint, which caps at
  5,000 rows server-side and returns an honest
  `showing`/`total_matching`/`truncated` payload for the zoomed viewport.
  `tests/fixtures/openapi_baseline.json` regenerated in the same commit.

### 2026-06-11 full-stack audit remediation

- **Behavioral:** `lead_score` now computes in exact decimal arithmetic to
  match `fn_lead_score`'s Spark DECIMAL semantics — Python scores shift by
  ±1 on ~0.67% of inputs (exact-.5 boundaries), eliminating false
  "integrity gap" warnings in the borrower proof drawer.
- Added optional `MIP_APPROVER_EMAILS` allowlist; when set,
  `/api/outreach/approve|reject` return 403 for non-approver, non-admin
  callers (empty default preserves the permissive Module 0 demo posture).
- Added `MIP_LEADS_WARM_INTERVAL_S` (default 240): startup +
  refresh-ahead warming of the default `/api/leads` page so hero-route
  loads hit cache instead of a 3.6-6.6s cold warehouse query.
- Fair-lending prompt guard now exempts loan-attribute vocabulary
  ("average loan age") and geographic proper nouns ("White Plains");
  protected-class usage still refuses.
- Lakebase narrative seed approvals reference real `gold.borrower_360`
  IDs; `mip_app.approvals.borrower_id` gains a `B-[0-9A-Z]{13}` CHECK.
- `deploy.sh` now applies UC + Lakebase grants and provisions the
  `mip/pii-salt-v1` secret scope itself (fresh-workspace zero-click).
- Gold CTAS refreshes re-declare clustering, column comments, and
  TBLPROPERTIES; `borrower_360.ltv` now prefers Cotality CLTV exactly
  like `equity_pct`.

### Earlier unreleased

- Added admin-only `/api/v1/admin/operations` and
  `/api/v1/admin/operations/run` for audited Databricks refresh job status and
  launch.
- Moved live Databricks validation from a daily schedule to manual
  `workflow_dispatch` so expensive refresh, Genie, and Playwright gates run
  only for release/signoff events.

## 0.1.0 - 2026-05-17

- Added canonical `/api/v1/*` API routes.
- Kept unversioned `/api/*` routes as deprecated compatibility aliases.
- Added `X-API-Version: v1` on API responses.
- Added an OpenAPI compatibility baseline gate for removed paths, removed
  methods, removed fields, optional-to-required field flips, and enum narrowing.
- Wired OpenAPI `info.version` to the package version.
