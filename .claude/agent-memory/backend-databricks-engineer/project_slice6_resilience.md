---
name: Slice 6 resilience primitives
description: Resilience stack (breaker, retry, TTL cache, degraded UI, PII denylist) wired into the live-data Module 0 app
type: project
---

Slice 6 landed 2026-04-21 on branch feature/module0-real-data. Resilience is the booth-survival story now that the app is 100% on live Unity Catalog + Lakebase with no mock runtime fallback.

Key surfaces to know:
- `backend/services/resilience.py` — `CircuitBreaker` (CLOSED/OPEN/HALF_OPEN), `with_retry` (exp backoff + decorrelated jitter), `TTLCache` (per-key TTL), `Resilient[T]` wrapper, `DependencyDownError` (typed exception that translates to HTTP 503 via app-level handler in `backend/main.py`). Registry pattern via `get_breaker(name)` / `all_breakers()` so `/api/health` and the repositories share one breaker per dependency.
- `ResilientSqlClient` and `ResilientLakebaseClient` wrap the bare clients and are returned by `get_sql_client()` / `get_lakebase_client()` — call sites unchanged.
- `/api/health` returns `{status, mode, app_env, warehouse_id, dependencies, circuit_breakers}`; degraded state stays HTTP 200 so load balancers don't yank the container.
- Lifespan warm-starts warehouse + Lakebase via `SELECT 1`; failures log and continue (breaker + degraded banner absorb the gap).
- Cache keys in `Databricks{Segment,Portfolio}Repository`: `segments.list.{pid|_ALL}` and `portfolio.preview.all`. Fresh-only endpoints (audit, outreach, borrower dossier) are NOT cached. TTL configurable via `MIP_CACHE_TTL_S` (default 30, 0 disables).
- `AuditPIIError` in `backend/services/audit_store.py` with denylist `{owner_name, owner_full_name, display_name, street_address, mailing_street, borrower_name, email, phone}`; enforced at write time on both `InMemoryAuditStore` and `LakebaseAuditStore`.

**Why:** The app is now fully on real data with no silent mock substitution. Visible degraded state is the only honest UI behavior when a dependency is down. Governance also wanted PII blocked at write time, not read time, because the audit ledger is append-only.

**How to apply:** Future slices touching dependency calls should funnel through `get_sql_client()` / `get_lakebase_client()` so they pick up breaker+retry automatically. Do not bypass with a direct `DatabricksSqlClient(...)` constructor. Do not add mock-fallback branches on 503 — the contract is `retryable: true` + DegradedBanner.
