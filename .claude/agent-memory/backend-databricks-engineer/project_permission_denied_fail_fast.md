---
name: permission-denied-fail-fast
description: Warehouse permission refusals are DependencyDownError kind permission_denied (retryable False, breaker success); other definitive analysis errors are still retried
metadata:
  type: project
---

Since 2026-09-25 (branch fix/rate-lever-gold-only) the warehouse client raises `DatabricksSqlPermissionError` for INSUFFICIENT_PERMISSIONS / SQLSTATE 42501 / a FAILED statement's PERMISSION_DENIED code. `Resilient(permission_denied_on=...)` never retries it, records a breaker SUCCESS, and raises `DependencyDownError(kind="permission_denied")`; the 503 body says `retryable: false`. An HTTP-level 403 without those markers (expired token) stays retryable on purpose.

**Why:** before, a missing grant was retried 3x, counted as a breaker failure on the shared "warehouse" breaker (one misgranted table could open it for every surface) and shown as "temporarily unavailable". Recording nothing instead of success would strand a HALF_OPEN probe slot (allow() then refuses forever).

**How to apply:** do not special-case permission errors elsewhere; catch `DependencyDownError` as before. Known residual: TABLE_OR_VIEW_NOT_FOUND and other definitive analysis errors are still retried and still count as breaker failures (the Rate Lever's built=False path walks `last_error`, so it keeps working either way). Related: [[slice6-resilience]], [[app-sql-gold-only]].
