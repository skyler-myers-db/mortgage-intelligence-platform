---
name: fake-lakebase-sql-substring-dispatch
description: tests/conftest.py's fake Lakebase routes by SQL substring — a new CTE reusing an existing name silently hits the wrong handler and returns plausible garbage
metadata:
  type: project
---

`_FakeLakebaseClient.fetchone`/`fetchall` in `tests/conftest.py` dispatch on
ordered `if <substring> in sql` checks. A new statement whose text happens to
contain an earlier handler's marker is silently answered by that handler — no
error, just a wrong-shaped dict, so the test fails on the *assertion* and looks
like a logic bug in the code under test.

**Why:** Cost real debugging time on 2026-08-08. A new approval-funnel query
opened with `WITH latest_approval AS (` and was swallowed by the
`if "WITH latest_approval" in sql:` lifecycle handler at the top of the chain.
The funnel read 0 while the disposition write had clearly succeeded (HTTP 200 in
the captured log). Renaming the CTE to `latest_decision` fixed it instantly.

**How to apply:**
- Before adding SQL to a Lakebase-backed service, grep `tests/conftest.py` for
  every distinctive token in your statement (CTE names, table names, `DISTINCT
  ON`, `ORDER BY …`) and confirm none is an existing dispatch marker.
- Give new CTEs names that are unlikely to collide, then add your own handler
  keyed on a marker unique to your statement (e.g. `FROM actioned_borrowers`).
- Symptom to recognize: the write endpoint returns 200, the read returns a
  stale/zero value, and no handler raised. That is a dispatch collision, not a
  cache or transaction bug — check ordering before debugging the service.
- Fake state is session-scoped but reset per test by
  `_reset_fake_dependency_state_for_tests` (via the autouse
  `_isolate_fastapi_dependency_state` fixture), so per-test zero-state
  assertions are safe regardless of ordering.

Related: [[shared-worktree-git-add]], [[dependency-override-pop]]
