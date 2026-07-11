# Changelog

All notable API-facing changes for the Mortgage Intelligence Platform are tracked here.

This project follows an additive-first API contract: new optional fields and new
versioned endpoints may be added in a minor release, while removals require a
deprecation window first.

## Unreleased

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
