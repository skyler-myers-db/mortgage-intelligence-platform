# Work order: AI Gateway — full exact-proof implementation & integration

> **For the build agent.** This replaces the BORDERLINE-ACCEPTABLE async standard with a full
> exact-row proof. Product owner directive: no borderline. AI Gateway becomes claimable ONLY on
> verified exact-row proof, and the gateway must govern real product traffic, not just probes.
> The honesty guardrails in §7 are hard requirements — a "green" achieved by weakening any gate
> is a failed deliverable.

## 0. Problem statement (established fact, do not re-litigate)

Databricks AI Gateway inference tables materialize **asynchronously**; measured delivery lag in
this workspace is >90s and ≤2h. Therefore a synchronous request-scoped probe can never observe
its own row. The current standard (commit `3256faf`/`7f5e7e0`) accepts a trailing-2h
deployment-scoped row as fallback — ruled BORDERLINE. This work order removes that fallback and
replaces it with a **two-phase exact-row proof** that is stronger than the fallback and immune
to the lag.

## 1. Architecture: two-phase exact-row verification

**Principle: separate *sending* the probe from *verifying* its row. Persist verified proofs.
The capability claim reads verified proofs, never raw row counts.**

### 1a. Proof ledger (Lakebase)

New table `mip_app.ai_gateway_proof_ledger` (idempotent migration in `lakebase/schema.sql`):

```sql
CREATE TABLE IF NOT EXISTS mip_app.ai_gateway_proof_ledger (
  proof_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  git_sha             TEXT NOT NULL,                  -- full 40-char deployment SHA
  client_request_id   TEXT NOT NULL UNIQUE,           -- mip-capability-{full-sha}-{uuid16}
  endpoint_name       TEXT NOT NULL,
  inference_table     TEXT NOT NULL,                  -- three-part UC name
  sent_at             TIMESTAMPTZ NOT NULL,
  verified_at         TIMESTAMPTZ,                    -- NULL until the exact row is observed
  verify_latency_s    DOUBLE PRECISION,               -- measured delivery lag (evidence)
  status              TEXT NOT NULL CHECK (status IN ('pending','verified','failed','expired'))
);
CREATE INDEX IF NOT EXISTS idx_ai_gateway_proof_sha_status
  ON mip_app.ai_gateway_proof_ledger (git_sha, status, verified_at DESC);
```

**Write-path security (hard requirement):** the ledger is written ONLY by the deploy/scheduled
verifier (service-principal path) and never by any public/user-facing API route. No FastAPI
endpoint may INSERT/UPDATE this table. A runtime user must have no way to mint a fake proof.
Reads from the app are fine (probe + admin surfaces).

### 1b. Verifier tool — `tools/databricks/verify_ai_gateway_exact_proof.py`

One tool, two modes, "send-now / verify-previous" pattern so proof stays fresh regardless of lag:

1. **verify-pending:** for every `pending` ledger row for the current SHA (and optionally prior
   SHAs), run `count_inference_log_rows(exact client_request_id)` (reuse the existing exact-id
   helper — it already binds the id as a parameter). If and only if the count is exactly 1: set `verified`,
   `verified_at=now()`, compute `verify_latency_s`. If older than a hard ceiling (default 6h,
   `MIP_AI_GATEWAY_VERIFY_EXPIRY_S`): set `expired`.
2. **send:** warm scale-to-zero separately with non-proof `mip-warmup-*` request ids, then mint
   `mip-capability-{full-sha}-{uuid16}` (full deployment SHA), insert its `pending` ledger row,
   and send that exact bounded request once. Never retry the proof id: timeout/503 after submission
   is unresolved, so leave the row `pending` for later exact-row verification. A Responses API
   result is accepted as serving proof only when its terminal status is `completed` and it has output.
3. **--wait mode (deploy-time):** after send, poll the exact id every 30–60s up to
   `MIP_AI_GATEWAY_VERIFY_TIMEOUT_S` (default 1200s, configurable up to 3600). On hit → mark
   verified inline and print measured latency. On timeout → leave `pending` (the next scheduled
   run verifies it) and exit per §1d gating.

Print measured `verify_latency_s` in all modes — this number is signoff evidence and informs
window tuning. No secrets in output. Idempotent: re-running verify-pending is a no-op for
already-verified rows.

### 1c. Runtime probe — `backend/services/ai_gateway_capability_probe.py`

Claimable = ALL of the following (single path, no alternatives):

1. All existing pre-checks (endpoint READY, `inference_table_config.enabled`, table name match,
   table visible to SQL, `MIP_GIT_SHA` present) — unchanged, all fail-closed.
2. Current bounded query accepted with payload (endpoint-alive-now check) — unchanged. Also
   insert nothing; the runtime probe does NOT write the ledger (see 1a). Optionally keep the
   existing synchronous exact-wait as an opportunistic fast path; if it hits, that is also a
   valid proof for this run (and strictly stronger), but it must NOT be required.
3. **A `verified` ledger row exists for the CURRENT deployment SHA with
   `verified_at >= now() - FRESHNESS`.** `FRESHNESS` = `MIP_AI_GATEWAY_PROOF_FRESHNESS_S`,
   default and hard maximum 26 hours (covers a nightly re-verify cadence + lag; see §1d). Settings
   validation rejects larger values, and ledger/probe callers defensively cap mutated or direct
   values. A verified proof from a different SHA, a `pending`/`failed`/`expired` row, or a stale
   `verified_at` → NOT claimable.

**Remove the trailing-2h `count_recent_inference_log_rows` fallback from the claimable path
entirely.** It may remain only as detail enrichment on the `configured` branch (e.g. "recent
deployment rows visible; exact verification pending"), never as a path to `available=True`.

Detail strings (update all surfaces consistently, §4):
- AVAILABLE: `"Live AI Gateway endpoint accepted a bounded query now; exact inference-row
  round-trip verified for this deployment at {verified_at} (delivery {verify_latency_s}s)."`
- CONFIGURED: honest pending/failed/expired/stale wording; keep the existing not-claimable phrases the
  smoke gate recognizes.

### 1d. Wiring: deploy + nightly

- **deploy.sh:** after agentic provisioning, run the verifier `send --wait`. Under
  `MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=1` (strict): exit non-zero if no verified proof exists for
  the deployed SHA after the wait (a prior-SHA verified proof does NOT count). Non-strict:
  warn, leave `pending`, capability honestly reports `configured` until the nightly verifies.
- **Nightly (extend `nightly.yml` or the monitors-job pattern):** run `verify-pending` then
  `send` — this keeps a rolling verified proof fresher than `FRESHNESS` forever, regardless of
  lag. If delivery breaks, verification stops, the newest proof ages past FRESHNESS, and the
  capability self-heals to `configured` — preserve this property.
- Document both in `docs/deployment.md` (including the strict-mode admin-token note already
  there).

## 2. Integration: the gateway must govern real product traffic

Probe traffic alone is not "integration." Required:

1. **Route the Supervisor/orchestrator bounded queries through the AI-Gateway-enabled endpoint.**
   The provisioner deploys an MLflow `ResponsesAgent` as the product boundary and delegates its
   bounded input to the managed Supervisor endpoint through Model Serving automatic
   authentication. All config stays in the provisioner/bundle; zero click-ops. After this, real
   `agent_framework` runs produce inference rows (request ids should carry a distinguishable
   prefix, e.g. `mip-agent-run-{full-sha}-…`).
   Deployment/export postflight must independently read the exact Unity Catalog model version
   receiving endpoint traffic and require its `mip.proxy_source_hash` and
   `mip.upstream_supervisor_endpoint` tags to match the reviewed proxy source and Supervisor.
   Endpoint tags alone are not authoritative because they can be changed independently of the
   served registered-model version.
2. **Run-card governance chip binds to a real, synchronously-true signal:** for
   `agent_framework` runs, record the gateway `client_request_id` used for the Supervisor call
   in the run evidence, and render the AI Gateway chip as "routed through governed endpoint"
   (structurally true at call time — routing is provable synchronously; row landing is async and
   must NOT be claimed per-run). Do not claim per-run row delivery.
3. **Admin visibility (admin-gated only):** on the admin capability surface, show last verified
   proof time + measured latency + count of gateway inference rows for the current SHA (probe +
   product prefixes) read from the inference table. No identifiers on public surfaces (existing
   leak-guard tests stay green).
4. **Request budgets:** Databricks Agent Model endpoints support Gateway inference tables but do
   not currently support Gateway rate limits or usage tracking. Keep those unsupported fields off
   the endpoint, assert that the provisioning payload contains only the inference-table config,
   and retain authenticated application-level backpressure as the request budget.

## 3. Test requirements (all must exist; enumerate every added/removed assertion in the report)

Backend:
- Ledger migration idempotency (re-run safe), status CHECK, unique client_request_id.
- Verifier: pending→verified on row landing (id-aware SQL fake); timeout leaves pending;
  timeout/503 never retries the exact proof id; duplicate exact rows remain unverified;
  expiry→expired; latency recorded; SHA scoping; exit codes for strict/non-strict; verify-previous
  works across invocations; re-run idempotency.
- Probe: claimable ONLY with fresh verified ledger row for current SHA. Rejection tests:
  wrong-SHA verified row; pending; failed/expired; stale verified_at (boundary ±1s); ledger row present
  but endpoint not READY now; ledger empty. Positive: fresh verified row + all pre-checks →
  available with the exact-proof detail string.
- **Re-invert the async-acceptance test:** `..._accepts_async_deployment_scoped_rows` must be
  replaced by a rejection (recent prefix rows WITHOUT a verified ledger row → NOT claimable).
  This is a deliberate standard change back to strict — say so in the commit message.
- No public API writes the ledger (route-table scan test or explicit negative test).
- Public wording updated to exact-only + assertions updated; identifier leak-guards intact.
- Supervisor-run gateway routing: evidence carries the gateway request id; chip copy test.

Smoke (`scripts/smoke_live.sh`): claimable branch regex accepts ONLY the exact-verified detail;
configured branch unchanged; strict mode requires claimable. Keep the two branches mutually
exclusive on status. Update the header docs.

Frontend: capability chip test for the new available detail; run-card chip test for "routed
through governed endpoint" on agent_framework runs (per-surface, chip-variant assertions — same
standard as the divergence tests).

Live (E2E_LIVE-gated Playwright): assert `/admin/capabilities?live=1` shows ai_gateway
`available` + exact-verified detail; assert an agent_framework run's card shows the routed chip.

## 4. Consistency sweep (all surfaces, one standard)

Update together, and add/keep a drift test where feasible: probe detail strings; public canned
string in `growth_agent_api_helpers.py` (+ its assertions); smoke regexes; `docs/deployment.md`;
`docs/runbook.md` if it mentions gateway proof; the governance memory
(`project_ai_gateway_probe_proof.md`) — record the new single-standard: *claimable only via
ledger-verified exact row, freshness-bounded, ledger writable only by deploy/nightly*.

## 5. Signoff requirements (PASS bar — every item verifiable)

1. **Single claimable path:** code + tests prove `available=True` is reachable ONLY via
   fresh verified ledger proof for the deployed SHA + live pre-checks. No fallback paths.
2. **Live demonstration:** deploy log shows verifier sent id X and observed row X (or nightly
   verified it), with measured latency; `/admin/capabilities?live=1` shows available with the
   exact-verified detail; strict smoke (`MIP_REQUIRE_AI_GATEWAY_CLAIMABLE=1`) green on the
   deployed SHA.
3. **Integration proof:** at least one real `agent_framework` product run's request id is
   visible in the inference table (may verify next-day via nightly given lag), and the run card
   shows the routed-through-gateway chip.
4. **Measured latency reported** (the number, not "passed").
5. **No weakening anywhere else:** enumerate every assertion added/changed/removed; the async
   acceptance test is replaced by a rejection; nothing else loosened.
6. **Suites:** junit-XML counts reported as `X passed / Y skipped / 0 failed (Z total)` — do NOT
   report the junit total as "passed" (this error occurred twice); vitest run isolated (not
   concurrent with pytest — worker-timeout artifacts); changed test files 2× flake check; CI
   green on the exact deployed SHA; ruff/mypy/lint/build/file-size/scaffold green.
7. **Reviewer swarm PASS** per §6 with zero blockers.
8. **Honest failure mode:** if the workspace genuinely cannot deliver rows within the ceiling,
   the deliverable is still complete when: implementation + tests are merged, the capability
   honestly reports `configured` with pending/failed/expired evidence, strict deploy fails closed, and
   the report states the infra blocker with the ledger evidence attached. Under NO circumstance
   relax any gate to force green (§7).

## 6. Reviewer subagent instructions (dispatch all three; PASS requires zero blockers each)

**Governance/security reviewer — adversarial mandate:** try to game the new gate. Attempts must
include: stale/wrong-SHA verified rows; pending/failed/expired rows; freshness boundary off-by-one;
forging a proof via any public API (must be impossible — enumerate every route that touches the
ledger); SQL injection via ledger fields; cross-deployment contamination; a broken pipeline
surviving past FRESHNESS; PII in ledger/evidence; disclosure consistency across all §4 surfaces;
confirm ledger writes are deploy/SP-only. Verdict must state: "the only path to available is X"
with file:line enumeration.

**QA reviewer:** junit-XML authoritative counts (passed vs total explicitly); isolated vitest;
2× flake on changed files; full no-weakening diff scan (quote every removed/changed assertion);
static gates; smoke jq truth-table against crafted payloads (overclaim row, malformed row,
missing row, duplicate rows); verify the drift between backend detail strings and smoke regexes
is zero.

**Live validation reviewer:** run the verifier against the real workspace; capture measured
latency and the ledger row; confirm `?live=1` exact-proof detail; run strict smoke; run the new
Playwright spec; count inference rows for probe AND product prefixes for the current SHA;
confirm a Supervisor product call's id lands (allow next-day verification given lag). Report raw
numbers/ids (redact per the existing evidence-redaction caveat before external sharing).

## 7. Honesty guardrails (hard constraints — violating any one fails the deliverable)

- The ONLY claimable path is the ledger-verified exact row (§1c). No prefix-count fallback, no
  "enabled/queryable" path, no recent-window path may yield `available=True`.
- `FRESHNESS` must not exceed the code-enforced 26h maximum. Changing that ceiling or the 3600s
  verify ceiling requires product-owner signoff recorded with the change.
- No test may be inverted/renamed to accept weaker proof. Replacing the async-acceptance test
  with a rejection is required and must be called out explicitly in the commit message.
- If a gate fails, report the failure. A summary claiming PASS while any gate is red, or
  reporting junit totals as passed counts, is a defective report.
- Keep all changes bundle/provisioner-based (zero click-ops), consistent with CLAUDE.md.

## 8. Report format (final summary must include)

Commit SHA(s); CI run id; deployment id; MLflow eval run id; verifier ledger `proof_id`,
`client_request_id`, `verified_at`, `verify_latency_s`; junit counts in the exact format from
§5.6; frontend counts (isolated); enumerated assertion changes; every surface updated in §4;
any deviations from this work order with justification; the standing caveats (creds boundary,
evidence redaction) preserved.
