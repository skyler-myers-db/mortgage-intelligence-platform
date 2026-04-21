# Validation — Credential-Kill Drill

**Validates:** the Module 0 "no silent mock fallback" posture —
specifically that every upstream dependency (SQL warehouse, Lakebase,
Genie, Databricks token) can fail and the app shows a visible
degraded state with real HTTP failure codes and a banner, never fake
rows.

**Canonical procedure:** [`docs/credential-kill-drill.md`](../credential-kill-drill.md).
**Runbook entry:** [`docs/runbook.md`](../runbook.md) §9.
**Drill tools:** [`tools/kill_drill/run_drill.sh`](../../tools/kill_drill/run_drill.sh),
[`tools/kill_drill/verify_degraded_ui.py`](../../tools/kill_drill/verify_degraded_ui.py).

---

## What the drill covers

| Dependency | Failure injection | Drill flag |
|---|---|---|
| SQL warehouse | Operator stops the serverless warehouse in the target workspace. | `--target warehouse` |
| Lakebase | Operator stops the database instance OR rotates the password. | `--target lakebase` |
| Genie | Script forks a private backend on port 8001 with `GENIE_SPACE_ID=0000…` | `--target genie` |
| Databricks token | Script forks a private backend with `DATABRICKS_TOKEN=""`. | `--target token` |

Warehouse + Lakebase require a human hand on the wheel because they
touch real workspace infrastructure. Genie + token are self-contained
simulations; they never alter the operator's running backend.

## Pass criteria

A PASS for any target requires **every** item below:

1. `/api/health` transitions to `status: "degraded"` within 20 s of
   the failure injection, and the targeted `dependencies.<name>`
   reports `down` (or `circuit_breakers.<name>` reports `open`).
2. Every data endpoint that backs the affected feature returns an
   explicit failure — 503 (ideally with `retryable: true`), 5xx, or a
   200 self-declaring `degraded: true` / `source: fallback`.
3. No endpoint returns a non-empty 200 payload carrying real-looking
   rows during the outage. This is the regression signal: a green
   response in a broken state = silent mock fallback.
4. The `verify_degraded_ui.py` companion script, run mid-drill,
   confirms every route's HTML shell still responds and every backing
   endpoint is in an acceptable degraded shape.
5. Recovery: after the dependency is restored, `/api/health` returns
   to `status: "ok"` within ~30 s (the circuit-breaker cool-down).

## Fail criteria

A FAIL on any target blocks release. Canonical fail modes:

| Symptom | Root cause (typical) | Action |
|---|---|---|
| `/api/health` stays `ok` after warehouse stop | Health probe isn't exercising the real SQL path | Fix the probe in `backend/api/health.py` |
| `/api/leads` returns 200 with populated rows during outage | A repository silently falls back to a fixture / mock | Remove the fallback; route must raise `DependencyDownError` |
| Banner never renders despite degraded health | `DegradedBanner` poll loop broken, or CSS hidden | Fix `frontend/src/components/mortgage/DegradedBanner.tsx` |
| Breaker never opens | Failure counter or threshold misconfigured | Tune `get_breaker()` call at the repository layer |
| Recovery never happens | Half-open probe logic broken | Add a unit test, fix `CircuitBreaker.record_success` |

## Frequency

- Before every release rehearsal (4 targets × ~5 min = 20 min total).
- After any diff that touches the files listed in
  `docs/credential-kill-drill.md` § "Cadence".
- After any production incident involving a dependency outage, to
  re-prove the posture hasn't regressed.

## Evidence

Each drill writes `tools/kill_drill/evidence/drill_<target>_<ts>.log`.
Attach the four logs to the release PR. The governance-security-reviewer
verifies the PASS lines and checks for the phrase
`DRILL PASS: <target>` on the last line.

## Known limitations

- The drill assumes the operator is on a non-production workspace.
  There is no in-script guard against running against prod; that
  discipline lives in the pre-flight checklist.
- The UI verifier does not open a real browser. The banner rendering
  is validated indirectly (health shape + endpoint shape). If we
  later suspect a rendering-only regression, add a Playwright-based
  mid-drill check that asserts `[role="status"][data-degraded-dependency]`
  is visible on every route.
- The Lakebase "rotate password" path proves auth failure but not
  network failure. For a full network-path drill, use Option 1 (stop
  the instance).

---

*Owner: governance-security-reviewer. Last validated: 2026-04-21.*
