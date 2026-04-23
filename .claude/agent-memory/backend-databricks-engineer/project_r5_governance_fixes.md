---
name: R5 governance + safety fixes
description: R5-01 idempotency key on approve/reject, R5-09 trust-forwarded-headers flag, R5-18 broadened audit-write except, R5-23 no-body-in-logs test
type: project
---

Landed 2026-04-23 in `backend/api/outreach.py`, `backend/services/lakebase_bootstrap.py`, `backend/config/settings.py`, `backend/services/rbac.py`, `backend/services/audit_store.py`, `backend/api/leads.py`, `backend/api/borrowers.py`, `lakebase/schema.sql`, `sql/ddl/lakebase_add_request_id.sql`, `docs/security/GRANTS.md` §10.

**Why:** governance-audit fixes for R5 round — idempotency on retries, trust boundary explicit, background-task audit failures visible but non-fatal, and a test guard that request bodies never leak to logs.

**How to apply:**
- `OutreachApproveRequest.request_id` / `OutreachRejectRequest.request_id` is the optional idempotency key. Clients should generate `crypto.randomUUID()` and pass across retries. The server uses a partial unique index on `mip_app.approvals(request_id) WHERE request_id IS NOT NULL` and a fast-path SELECT.
- `lakebase_bootstrap.ensure_approval_idempotency_column(client)` runs the R5-01 DDL once per process on first approve/reject. Per-process flag `_APPROVAL_REQUEST_ID_BOOTSTRAPPED`; tests must call `_reset_bootstrap_for_tests()` or flip the flag directly before asserting `execute.call_count`.
- `settings.trust_forwarded_headers: bool = True` gates both `rbac.require_admin` (group path disabled when False) and `audit_store.resolve_actor` (returns `"unknown-actor@untrusted-edge"` when False). Only flip for non-Apps deploys that don't strip inbound X-Forwarded-*.
- All three `_safe_audit_write` helpers (outreach/leads/borrowers) now catch `Exception` and emit structured `event=audit.dropped` with `exc_type` only — never `str(exc)`.
- Frontend `request_id` wiring in `frontend/src/lib/api.ts` was NOT done — out of scope for this cycle. TODO comment not left; master agent should follow up.
