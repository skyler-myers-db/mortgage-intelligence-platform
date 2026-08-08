---
name: lakebase-breaker-conflict-invariant
description: How Lakebase idempotent writes must avoid tripping the circuit breaker, and the CI gap that doesn't test it
metadata:
  type: project
---

Lakebase idempotent writes (lead_assignments, call_dispositions, lead_outcomes) use `ON CONFLICT ... DO NOTHING` + select-then-replay. A duplicate request_id must NOT trip the Lakebase circuit breaker.

**Why:** A prior incident (B2) had a duplicate disposition request_id raise a UniqueViolation -> surfaced as a `psycopg.Error` -> `ResilientLakebaseClient.transaction` (`backend/services/lakebase.py:649-674`) called `breaker.record_failure()` -> ~10s app-wide 503 cascade including reads. `ON CONFLICT DO NOTHING` commits zero rows with no exception, so control hits the `else: breaker.record_success()` branch. Payload-mismatch on replay raises plain `ValueError` (mapped to 409 in `backend/api/sales.py`), which is neither `psycopg.Error` nor `LakebaseError`, so it also bypasses `record_failure()`.

**How to apply:** Any new idempotent Lakebase write must use `ON CONFLICT DO NOTHING` (not try/except UniqueViolation). The breaker only counts `psycopg.Error`/`LakebaseError`/`DependencyDownError` as failures. CI GAP as of commit 55995b9: the conftest fake Lakebase (`tests/conftest.py` ~591-617) has a `raise RuntimeError("duplicate ...")` branch that is DEAD because production SQL always contains `ON CONFLICT` -> fake always takes the DO NOTHING branch. So tests verify replay works but do NOT guard the breaker-accounting invariant. If asked to harden, make the fake raise a `psycopg.Error`-shaped conflict and assert `get_breaker("lakebase")` failure count is unchanged across a duplicate POST. See [[feedback_dependency_override_pop]] for breaker test hygiene.
