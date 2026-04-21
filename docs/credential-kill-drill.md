# Credential-Kill Drill — Module 0

**Purpose.** Prove on demand that every Module 0 upstream dependency
can fail and the app will show a visible degraded state — never fake
data. This is the governance evidence behind the "no silent mock
fallback" posture documented in `CLAUDE.md` and `backend/services/resilience.py`.

**Audience.** The operator running the drill, the governance reviewer
signing off on the evidence log, and the on-call engineer who needs a
canonical recovery procedure.

**Cadence.** Run the full four-target sweep before every major release
rehearsal and immediately after any change to:
- `backend/services/resilience.py`
- `backend/api/health.py`
- `backend/services/databricks_sql.py` / `backend/services/lakebase.py` / `backend/services/genie_client.py`
- `frontend/src/components/mortgage/DegradedBanner.tsx`

---

## Pre-flight checklist

Before kicking off any drill:

- [ ] `.env.local` points at a **non-production** workspace. The drill
      stops real warehouses + database instances. Running it against
      prod is a governance violation.
- [ ] The backend is running locally (`uvicorn backend.main:app ...`)
      or the `MIP_APP_URL` target is reachable.
- [ ] `/api/health` currently returns `status: "ok"` with every
      dependency `up`. Starting from a degraded state means you can't
      observe the transition — the very thing the drill is proving.
- [ ] You have the Databricks CLI authenticated for the target
      workspace (`databricks auth describe` returns the right profile).
- [ ] `curl` and `jq` are installed.
- [ ] An evidence log directory exists: `tools/kill_drill/evidence/`.
      The script creates it if absent.

---

## Common shape of a drill run

Every target follows the same five beats:

1. **Pre-state probe.** Capture `/api/health` while everything is
   green. This row anchors the evidence log.
2. **Induce failure.** Either the operator stops real infrastructure
   (warehouse, Lakebase) or the drill script forks a private backend
   on port 8001 with a poisoned env (Genie, token).
3. **Assert degraded signal.** `/api/health` transitions to
   `status: degraded` with the targeted dependency reporting `down`
   and/or its circuit breaker `open`.
4. **Assert data endpoints fail visibly.** The canonical routes backing
   the UI return 503 (or an explicit degraded-shape 200), never
   suspicious "looks real" rows.
5. **Recovery.** Restore the dependency, wait for `/api/health` to
   close the breaker and flip back to `ok`.

All four drills write to `tools/kill_drill/evidence/drill_<target>_<timestamp>.log`.
That file is the governance artifact — attach it to the release review.

---

## Drill A — SQL warehouse

Requires a human in the loop because the destructive action stops real
infrastructure.

```bash
./tools/kill_drill/run_drill.sh --target warehouse
```

The script will print:

```
databricks warehouses stop "$DATABRICKS_WAREHOUSE_ID"
```

Run that in a separate terminal. Confirm the state in the Databricks
UI or:

```bash
databricks warehouses get "$DATABRICKS_WAREHOUSE_ID" | jq .state
```

Expect `STOPPED` or `STOPPING`. Type `done` in the drill prompt.

**Expected signals**

| Signal | Expected value |
|---|---|
| `/api/health` `status` | `degraded` (within ~20 s) |
| `/api/health` `dependencies.warehouse` | `down` |
| `/api/health` `circuit_breakers.warehouse` | `open` after 5 failures |
| `/api/leads?limit=5` | HTTP 503 with `retryable: true` |
| `/api/portfolio/kpis` / `/preview` | HTTP 503 |
| UI (while drill is in flight) | `DegradedBanner` visible on every route, no borrower rows rendered |

**Recovery**

```bash
databricks warehouses start "$DATABRICKS_WAREHOUSE_ID"
# expect state=RUNNING within ~30s
```

Type `done` again. The breaker will close within 30 s of the next
successful probe (half-open probe succeeds → CLOSED).

---

## Drill B — Lakebase

```bash
./tools/kill_drill/run_drill.sh --target lakebase
```

**Option 1 (recommended for a clean drill):** stop the Lakebase
database instance via the workspace CLI or UI.

**Option 2 (if you can't easily stop the instance):** rotate the
Lakebase password temporarily — the authentication failure trips the
breaker identically and is easier to reverse.

**Expected signals**

| Signal | Expected value |
|---|---|
| `/api/health` `status` | `degraded` |
| `/api/health` `dependencies.lakebase` | `down` |
| `/api/audit/events` | HTTP 503 |
| `/api/approvals` (POST) | HTTP 503, audit row NOT written |
| UI Approvals panel | Degraded state, "Recovering" copy, no approve button |

**Recovery**

Start the database instance, or restore the password. `/api/health`
flips back to `ok` within ~30 s.

---

## Drill C — Genie (simulated)

Genie doesn't have a single-command "stop" — the safest way to
simulate an invalid space id is to boot a drill backend on port 8001
with a bogus `GENIE_SPACE_ID`. The script does this for you:

```bash
./tools/kill_drill/run_drill.sh --target genie
```

It:

1. Exports `GENIE_SPACE_ID=00000000-0000-0000-0000-000000000000`.
2. Starts a private uvicorn on port 8001.
3. POSTs a question to `/api/genie/ask` and verifies the response is
   either a 503 or a 200 with `source: fallback` / `source: corpus`.

**Expected signals**

| Signal | Expected value |
|---|---|
| `/api/health` (port 8001) `dependencies.genie` | `down` after first probe |
| `/api/genie/ask` | HTTP 503 or HTTP 200 with `source: "fallback"` |
| Never | HTTP 200 with `source: "genie"` and hallucinated metrics |

The safe-corpus fallback with an explicit `source` field **is not** a
silent mock; the chip surfaces provenance to the user, which is the
contract.

**Recovery**

Kill the drill backend (Ctrl-C); the operator's main backend on port
8000 is untouched.

---

## Drill D — Databricks token (simulated)

```bash
./tools/kill_drill/run_drill.sh --target token
```

The drill unsets `DATABRICKS_TOKEN` in a subshell and tries to boot a
private backend.

Two outcomes — both are PASSES:

1. **Backend refuses to boot** (current posture per
   `backend/runtime.py::_preflight_credentials`). The drill records
   "preflight gate tripped" and exits 0. This is the strongest
   governance signal: the app *cannot* serve a request without real
   credentials.
2. **Backend boots and degrades.** If a future refactor allows boot
   with a partial cred set, the script still verifies that
   `/api/leads` returns 503 and the warehouse breaker trips. That
   outcome is a PASS, but flag it for review — we prefer outcome 1.

**Never acceptable:** backend boots and serves 200s with real-looking
rows. That is a mock-fallback regression and must be fixed before
merge.

---

## Verifying the UI during a drill

While one of the drills above is in flight (between "induce failure"
and "recovery"), run the verifier in another terminal:

```bash
./tools/kill_drill/verify_degraded_ui.py \
  --api-url http://127.0.0.1:8000 \
  --frontend-url http://127.0.0.1:5173
```

For simulated drills (genie, token), point both URLs at the drill
backend's port:

```bash
./tools/kill_drill/verify_degraded_ui.py \
  --api-url http://127.0.0.1:8001 \
  --skip-frontend
```

The verifier hits every route and asserts:

- The frontend HTML shell responds 200 (the app is still reachable).
- `/api/health` is degraded (sanity check that the drill is active).
- Every data endpoint behind every route returns 503, or a
  self-declared degraded shape, or an empty collection — **never** a
  non-empty 200 payload that looks like real data.

---

## Evidence log template

Every drill writes to `tools/kill_drill/evidence/drill_<target>_<timestamp>.log`.
Attach that file to the governance record. The log contains:

```
[drill/<target>] drill started at <UTC ts> against app_url=<url>
[drill/<target>] Pre-state probe (expecting all 'up' before the drill)
  <raw /api/health body>
[drill/<target>] OPERATOR CONFIRMATION REQUIRED
  Stop the SQL warehouse, then confirm.
[drill/<target>] health attempt 1: status=degraded warehouse=down breaker=open
[drill/<target>] GET /api/leads?limit=5 -> HTTP 503
[drill/<target>] PASS: /api/leads?limit=5 returned 503 with retryable=true
[drill/<target>] OPERATOR CONFIRMATION REQUIRED
  Restart the warehouse, then confirm.
[drill/<target>] RECOVERED: warehouse=up, status=ok after 14s
[drill/<target>] DRILL PASS: warehouse
```

Governance sign-off recipe (pull-request description copy-paste):

```
Drill evidence attached:
  - tools/kill_drill/evidence/drill_warehouse_<ts>.log  (PASS)
  - tools/kill_drill/evidence/drill_lakebase_<ts>.log   (PASS)
  - tools/kill_drill/evidence/drill_genie_<ts>.log      (PASS)
  - tools/kill_drill/evidence/drill_token_<ts>.log      (PASS)

Verifier run against each drill:
  ./tools/kill_drill/verify_degraded_ui.py --api-url http://127.0.0.1:8000
  -> PASS -- every route surfaced a degraded signal and no fake data was detected
```

---

## What a FAIL means

A drill FAIL (exit code 1) means **resilience is broken and the app
is serving fake data during a dependency outage.** Do not merge. Do
not ship. Open an incident using the post-mortem template in
`docs/runbook.md` and:

1. Pin the symptom: `/api/health` stayed green? 200 with real-looking
   rows? Banner didn't render?
2. Trace from the failing endpoint back through the service — which
   repository, which breaker, which fallback path.
3. Write a regression test that re-creates the specific false-200
   before fixing the code.
4. Re-run the drill to green.

The drill is the final gate. If it's red, the posture is a lie.

---

*Owner: governance-security-reviewer + principal-architect.
Last revised: 2026-04-21.*
