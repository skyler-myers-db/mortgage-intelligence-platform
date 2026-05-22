# Credential-Kill Drill — Module 0

**Purpose.** Prove on demand that every Module 0 upstream dependency
can fail and the app will show a visible degraded state — never fake
data. This is the governance evidence behind the "no silent mock
fallback" posture documented in `CLAUDE.md` and `backend/services/resilience.py`.

**Audience.** The operator running the drill, the governance reviewer
signing off on the evidence log, and the on-call engineer who needs a
canonical recovery procedure.

**Target matrix.** Eight drills across three tiers:

| Target            | Tier       | Touches real infra? | CI-safe? | Gated behind                              |
| ----------------- | ---------- | ------------------- | -------- | ----------------------------------------- |
| `warehouse`       | human      | Yes (operator stops) | No       | interactive ack                           |
| `lakebase`        | human      | Yes (operator stops) | No       | interactive ack                           |
| `genie`           | simulated  | No (env poisoning)   | Yes      | —                                         |
| `token`           | simulated  | No (env poisoning)   | Yes      | —                                         |
| `warehouse-sim`   | simulated  | No (env poisoning)   | Yes      | —                                         |
| `lakebase-sim`    | simulated  | No (env poisoning)   | Yes      | —                                         |
| `warehouse-real`  | real-infra | Yes (SDK-driven)     | No       | `--i-really-mean-it` OR `MIP_KILL_DRILL_ALLOW_REAL=1` |
| `lakebase-real`   | real-infra | Yes (SDK-driven)     | No       | `--i-really-mean-it` OR `MIP_KILL_DRILL_ALLOW_REAL=1` |

The **simulated** tier runs in the nightly `kill-drill-simulated`
GitHub Actions job and is a release gate. The **real-infra** tier is
opt-in only: either the manual `workflow_dispatch` path with
`run_real_drills=true`, or a deliberate local dry-run. It is **never**
on the nightly cron.

**Cadence.** Run the full four-target simulated sweep before every
major release dry-run. Run the real-infra pair before major release
dry-runs **only** — stopping real infra in production hours is a
user-visible outage. Run immediately after any change to:
- `backend/services/resilience.py`
- `backend/api/v1/health.py`
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
- [ ] `/api/v1/health` currently returns `status: "ok"` with every
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

1. **Pre-state probe.** Capture `/api/v1/health` while everything is
   green. This row anchors the evidence log.
2. **Induce failure.** Either the operator stops real infrastructure
   (warehouse, Lakebase) or the drill script forks a private backend
   on port 8001 with a poisoned env (Genie, token).
3. **Assert degraded signal.** `/api/v1/health` transitions to
   `status: degraded` with the targeted dependency reporting `down`
   and/or its circuit breaker `open`.
4. **Assert data endpoints fail visibly.** The canonical routes backing
   the UI return 503 (or an explicit degraded-shape 200), never
   suspicious "looks real" rows.
5. **Recovery.** Restore the dependency, wait for `/api/v1/health` to
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
| `/api/v1/health` `status` | `degraded` (within ~20 s) |
| `/api/v1/health` `dependencies.warehouse` | `down` |
| `/api/v1/health` `circuit_breakers.warehouse` | `open` after 5 failures |
| `/api/v1/leads?limit=5` | HTTP 503 with `retryable: true` |
| `/api/v1/portfolio/kpis` / `/preview` | HTTP 503 |
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
| `/api/v1/health` `status` | `degraded` |
| `/api/v1/health` `dependencies.lakebase` | `down` |
| `/api/v1/audit/events` | HTTP 503 |
| `/api/approvals` (POST) | HTTP 503, audit row NOT written |
| UI Approvals panel | Degraded state, "Recovering" copy, no approve button |

**Recovery**

Start the database instance, or restore the password. `/api/v1/health`
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
3. POSTs a question to `/api/v1/genie/message` and verifies the response is
   either a 503 or a 200 with `source: "degraded"`.

**Expected signals**

| Signal | Expected value |
|---|---|
| `/api/v1/health` (port 8001) `dependencies.genie` | `down` after first probe |
| `/api/v1/genie/message` | HTTP 503 or HTTP 200 with `source: "degraded"` |
| Never | HTTP 200 with `source: "genie"` and hallucinated metrics |

The explicit `source: "degraded"` response is the contract: no fake rows, no
fake provenance, and no fabricated analytics while Genie is unavailable.

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
   `/api/v1/leads` returns 503 and the warehouse breaker trips. That
   outcome is a PASS, but flag it for review — we prefer outcome 1.

**Never acceptable:** backend boots and serves 200s with real-looking
rows. That is a mock-fallback regression and must be fixed before
merge.

---

## Drill E — warehouse-real (SDK-driven, opt-in)

Stops the real SQL warehouse via `w.warehouses.stop(id)`, asserts the
degraded contract on the already-running backend, then restarts via
`w.warehouses.start_and_wait(id)` and waits for `/api/v1/health` to close
the breaker. This is the strongest real-world evidence the degraded
path works end-to-end — but it causes a 30–90 s user-visible outage
during the drill window.

```bash
# Local dry-run (operator already has DATABRICKS_WAREHOUSE_ID set):
./tools/kill_drill/run_drill.sh --target warehouse-real --i-really-mean-it
```

Without the `--i-really-mean-it` flag (or `MIP_KILL_DRILL_ALLOW_REAL=1`
in the environment) the script **hard-errors with exit 2** before any
SDK call. This is the safety rail that keeps an accidental invocation
from taking production down.

**Expected signals**

| Signal                                               | Expected value |
| ---------------------------------------------------- | -------------- |
| Pre-probe `/api/v1/health`                              | `status: "ok"` |
| During drill `/api/v1/health`                           | `status: "degraded"`, `dependencies.warehouse: "down"` |
| During drill `/api/v1/leads?limit=5`                    | HTTP 503 with `retryable: true` |
| After SDK `start_and_wait(...)` returns              | warehouse state `RUNNING` |
| Post-probe `/api/v1/health` (within 60 s)               | `status: "ok"`, `dependencies.warehouse: "up"` |
| Evidence log                                         | `tools/kill_drill/evidence/drill_warehouse-real_<ts>.log` |

**Failure modes that exit 1 (real regression)**

- Warehouse stopped but `/api/v1/health` never reported degraded → the
  resilience contract is broken.
- Warehouse restart timed out (default 300 s) → **real infra may still
  be stopped**. The operator must investigate immediately; the script
  logs the last known state.

**Running from GitHub Actions**

The `kill-drill-real-infra` job on the nightly workflow runs this drill
when and only when an operator triggers `workflow_dispatch` with
`run_real_drills=true`. The job env sets `MIP_KILL_DRILL_ALLOW_REAL=1`
scoped to that job only; the scheduled cron path never satisfies the
`if:` guard.

---

## Drill F — lakebase-real (SDK-driven, opt-in)

Stops the Lakebase database instance via PATCH `stopped=true` on the
`mip-app-state` instance (see `tools/kill_drill/real_infra.py` for the
exact SDK surface — the SDK does not expose dedicated stop/start verbs
for database instances; `update_database_instance` with an explicit
`update_mask="stopped"` is the canonical pattern).

```bash
./tools/kill_drill/run_drill.sh --target lakebase-real --i-really-mean-it
```

Same safety rail as warehouse-real. Same exit-2 refusal without the
flag.

**Expected signals**

| Signal                                               | Expected value |
| ---------------------------------------------------- | -------------- |
| During drill `/api/v1/health`                           | `dependencies.lakebase: "down"` |
| During drill `/api/v1/audit/events?limit=5`             | HTTP 503 |
| Post-probe Lakebase state                            | `DatabaseInstanceState.AVAILABLE` |
| Post-probe `/api/v1/health`                             | `status: "ok"`, `dependencies.lakebase: "up"` |
| Evidence log                                         | `tools/kill_drill/evidence/drill_lakebase-real_<ts>.log` |

**Recovery SLA**

The drill script waits up to 300 s (override with `--await-seconds`)
for the instance to return to `AVAILABLE`. If recovery exceeds that
window the drill exits 1 with a loud alert. In practice Lakebase
restarts in 60–180 s.

---

## Safety rails summary

Every real-infra invocation has to clear all of these gates:

1. **Flag gate** at the CLI layer (`run_drill.sh` refuses without
   `--i-really-mean-it` or `MIP_KILL_DRILL_ALLOW_REAL=1`). Exit 2.
2. **Impact notice** logged to stderr + the evidence log before any
   SDK call, stating the expected user-visible impact window.
3. **Idempotent stop** — a warehouse already STOPPED / a Lakebase
   already `stopped=true` skips the stop API call (no double-stop).
4. **Guaranteed restart** — on every failure path (degraded signal
   missing, data endpoint 200, operator interrupt), the recovery
   `start` call runs before the drill exits. If that restart fails the
   operator gets a loud alert and a non-zero exit.
5. **Never on cron** — the `kill-drill-real-infra` job is
   `if: github.event_name == 'workflow_dispatch' && inputs.run_real_drills == true`.
   A scheduled nightly can never trigger it.

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
- `/api/v1/health` is degraded (sanity check that the drill is active).
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
  <raw /api/v1/health body>
[drill/<target>] OPERATOR CONFIRMATION REQUIRED
  Stop the SQL warehouse, then confirm.
[drill/<target>] health attempt 1: status=degraded warehouse=down breaker=open
[drill/<target>] GET /api/v1/leads?limit=5 -> HTTP 503
[drill/<target>] PASS: /api/v1/leads?limit=5 returned 503 with retryable=true
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

1. Pin the symptom: `/api/v1/health` stayed green? 200 with real-looking
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
