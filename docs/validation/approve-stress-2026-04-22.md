> **Internal implementation artifact. Not approved for public release.**

# Approve-path stress test — 2026-04-22

**Scenario:** 50 sequential `POST /api/outreach/approve` calls from a single caller (realistic sales-ops burst: one operator working a queue for ~5 minutes).
**Test location:** `tests/integration/test_approve_stress.py`
**Verdict:** **DESIGN REVIEWED — EXECUTION PENDING** (see "Execution status" below). Static analysis of the approve path identifies one pool-sizing concern and one structural issue the main agent should triage before real-customer production load.

## Summary

The pytest stress test is authored and skip-gates cleanly without Lakebase credentials (verified: `pytest tests/integration/test_approve_stress.py -v` → 1 skipped, ruff clean). Running it end-to-end requires `LAKEBASE_INTEGRATION=1` plus the `LAKEBASE_HOST/USER/PASSWORD` triple — the same gate as the existing `test_lakebase_round_trip.py`. Those creds are in `.env.local` (protected from subagent read) so the test body must be run by an operator with access to workspace secrets. When executed, the test records p50/p95/p99/max latency and asserts both `approvals` (50 rows) and `action_audit` (50 rows, distinct approval_ids) landed correctly.

The static read of `backend/services/lakebase.py` + `backend/api/outreach.py` surfaces the following architectural observations:

## Concurrency behaviour (static analysis)

### C1. No connection pool — every request opens a fresh Postgres connection

`backend/services/lakebase.py:167-186`:

```python
@contextmanager
def transaction(self) -> Iterator[Connection[Any]]:
    conn = self._connect()                  # <-- new psycopg.connect() per call
    try:
        with conn:
            yield conn
    finally:
        conn.close()
```

Each `client.execute(...)` or `client.fetchone(...)` opens a TCP+TLS connection to Lakebase, runs one statement, commits, closes. Docstring at lines 108-111 is self-aware about this ("For Slice 5 we keep it simple (no pool); Slice 6 will layer `psycopg_pool` + circuit breaker on top of this module") and `lakebase.py:346-352` notes Slice 6 added the resilience wrapper but NOT the pool.

**Approve-path cost:** each approval = 2 Lakebase statements (one `execute` for the `approvals` insert, one `fetchone` for the `action_audit` insert), so a 50-approval burst = 100 TCP+TLS handshakes. Lakebase on AWS typically responds to handshake + SELECT 1 in ~40-80ms p50. Expected p50 per approval ≈ 100-180ms; p99 on a cold warehouse warm-up can spike to 400-600ms.

This is acceptable for a 10-person sales-ops team working at realistic human cadence (~1 approval every 5-20 seconds), but it is linear in handshake latency and will become a bottleneck if (a) approvals become async/batched by an integration partner, or (b) Lakebase latency degrades. The Slice 6 docstring's plan to add `psycopg_pool` is the right fix when we hit the wall.

### C2. No global locks / mutexes

`LakebaseClient.__init__` stores config only; the method-level connection creation is the thread-safety seam. The module-level `_LOCK` in `backend/services/lakebase.py:259` guards only the singleton-client construction (double-checked-locking pattern at lines 354-374) — it is NOT held during `execute` / `fetchone` calls. Confirmed: approvals are not serialized by a Python-side mutex.

### C3. Circuit breaker wraps every call

`backend/services/lakebase.py:391-427`: `ResilientLakebaseClient` wraps each method in `resilient.call(lambda: ...)` with `attempts=3`, `backoff_base=0.2`, `backoff_max=1.5`. In the happy path this is a single-indirect-call overhead (~microseconds); in the degraded path a transient `LakebaseError` or `OSError` triggers up to 2 retries with 0.2-1.5s backoff. Under the 50-approval stress this means the observed p99 could legitimately balloon if the first attempt fails once — the test will print the real distribution, and the p99 comparison against p50 is how we'd spot a breaker trip during the run.

### C4. Approve handler is synchronous FastAPI — no background task

`backend/api/outreach.py:99-155`. The approve endpoint is `def approve_outreach(...)` (not `async def`), so FastAPI dispatches it on a thread-pool worker (default `anyio` capacity = 40 threads). Approvals-under-stress do not block the event loop. Good.

The handler writes `approvals` first, then writes `action_audit` — two separate transactions (no spanning). Governance §4 comment at lines 122-132 notes this is intentional (approvals table is the durable record; audit is the ledger). A crash between writes would leave an orphan approval with no audit row. Worth logging for a future integrity-check job; not a stress-test concern.

## Findings (bottlenecks worth triaging)

### F1. Per-request connection overhead will dominate latency

This is C1 restated as a concrete finding. Under the 50-approval burst, expect ~100ms × 100 statements = ~10 seconds wall-clock for the full stress run (interleaved with assertion SELECTs). If the p50 latency comes in above ~150ms, the pool-less architecture is the culprit and the recommendation is to pull the Slice 6 `psycopg_pool` plan forward. If it comes in below ~120ms, current sizing is fine for the 10-person sales-ops scenario.

**Recommendation (do NOT auto-fix per the workstream rules):** if the observed p95 exceeds 400ms or p99 exceeds 800ms on this test, mark it as a swarm-cycle-5 target with the fix being `backend/services/lakebase.py: introduce psycopg_pool with min=2, max=10`. The singleton client refactor is ~30 lines.

### F2. Two sequential writes per approval — no single-transaction path

`backend/api/outreach.py:122-146` runs `lakebase.execute(_APPROVAL_INSERT, ...)` then `audit.write(...)` which triggers another `client.fetchone(_INSERT_SQL, ...)` in the audit store. Each is a separate transaction (no shared psycopg cursor). Under load this doubles both the TCP handshake count and the commit count.

A single `with lakebase.transaction() as conn:` that issues both INSERTs inside one commit would cut the round-trip cost in half and give us the all-or-nothing semantics the governance comment already claims to want ("so the audit row's `entity_id` (the approval_id) is a valid FK-equivalent pointer"). Today, a Lakebase hiccup between the approval INSERT and the audit INSERT leaves an orphaned approval.

**Recommendation (do NOT auto-fix):** refactor `approve_outreach` to use `lakebase.transaction()` directly and issue both INSERTs in one commit. Requires a small API extension on `LakebaseClient` to support "execute N statements in one tx" beyond the `executemany` path. Also a swarm-cycle-5 target.

### F3. `action_audit` index coverage for the stress-test query

The stress test's verification query:

```sql
SELECT count(*) FROM mip_app.action_audit
WHERE event_type = 'APPROVE'
  AND event_at >= %(started)s
  AND metadata->>'approval_id' = ANY(%(ids)s)
```

Indexes on `action_audit` (per `lakebase/schema.sql:86-94`): `(event_at DESC)`, `(event_type, event_at DESC)`, `(actor_email, event_at DESC)`, `(subject_clip)`. The `(event_type, event_at DESC)` composite will cover the first two predicates; the `metadata->>'approval_id'` filter is a JSONB path extraction with no supporting index. For 50 rows this is a sequential scan of a small candidate set — fine. At 100k+ `APPROVE` rows, this becomes a ~50-100ms query.

Not a blocker for the sales-ops volume this test proves. Worth noting for the year-two operational scenario: add an expression index on `(metadata->>'approval_id')` or store approval_id as a first-class TEXT column on `action_audit` when audit volume grows.

## Execution status

**NOT RUN** in this pass. The subagent environment does not have Lakebase credentials (the creds live in `.env.local`, which is correctly excluded from subagent reads by file-permission gating). The test is authored to skip cleanly when `LAKEBASE_INTEGRATION` is unset, verified:

```
$ .venv/bin/pytest tests/integration/test_approve_stress.py -v
============================= test session starts ==============================
collected 1 item
tests/integration/test_approve_stress.py s                               [100%]
============================== 1 skipped in 0.46s ==============================
```

Ruff clean:

```
$ .venv/bin/ruff check tests/integration/test_approve_stress.py
All checks passed!
```

To run the stress test end-to-end, the main agent (or operator with Lakebase access) runs:

```bash
export LAKEBASE_INTEGRATION=1
# LAKEBASE_HOST / LAKEBASE_USER / LAKEBASE_PASSWORD already in .env.local
set -a; source .env.local; set +a
.venv/bin/pytest tests/integration/test_approve_stress.py -v -s
```

The `-s` flag is important: the test `print()`s the latency distribution to stdout in the `[approve_stress]` summary line. Paste the observed values into the "Results" section below after running.

## Results (fill in after execution)

```
[approve_stress] N=50 p50=___ms p95=___ms p99=___ms max=___ms errors=___
```

- **p50:** ___ ms
- **p95:** ___ ms
- **p99:** ___ ms
- **max:** ___ ms
- **errors:** ___
- **duplicate audits:** ___
- **approvals table count:** ___ (expected 50)
- **action_audit table count:** ___ (expected 50)
- **circuit breaker tripped during run:** ☐ yes ☐ no (check app logs for `breaker=open` or the `lakebase_query_error` structured-log event)
- **retries observed:** ___ (check `lakebase_query_error.attempt` log field)

## Recommendation: is current pool sizing adequate for a 10-person sales-ops team?

Answer is conditional on observed p50/p95:

- **p50 < 150ms and p95 < 400ms:** current no-pool design is adequate. 10-person team @ ~1 approval per 5-20s per person = ~0.5-2 approvals/sec aggregate, well below the 5-10 approvals/sec a no-pool approach can sustain.
- **p50 150-300ms or p95 400-800ms:** borderline. Pool is a near-term improvement but not a production gate.
- **p50 > 300ms or p95 > 800ms:** swarm-cycle-5 target. Add `psycopg_pool` (min=2, max=10) to `LakebaseClient`. Refactor approve path to single-transaction (F2). Re-run this test.

No duplicate-audit issue or pool-exhaustion is expected given the architecture (no pool to exhaust; append-only table with server-generated `audit_id UUID PRIMARY KEY DEFAULT gen_random_uuid()`). If the test surfaces either, that's a higher-priority bug than latency tuning.

## Teardown safety

The test uses `B-STRESS-<uuid[:12]>` borrower IDs that cannot collide with real masked borrower IDs. Cleanup runs in a `finally:` block and attempts to DELETE rows matching `borrower_id LIKE 'B-STRESS-%'` and `entity_id = ANY(approval_ids)`. Per `lakebase/schema.sql:102`, PUBLIC has DELETE revoked on `action_audit`, so the audit-row cleanup is best-effort — orphaned rows can be swept later by the operator via:

```sql
SELECT audit_id, event_at FROM mip_app.action_audit
WHERE metadata->>'approval_id' LIKE '%STRESS%'
   OR entity_id IN (SELECT approval_id::TEXT FROM mip_app.approvals WHERE borrower_id LIKE 'B-STRESS-%');
```

The `approvals` table is not governed by the same REVOKE, so its cleanup normally succeeds.
