---
name: ai-gateway-probe-proof
description: AI Gateway capability is claimable only after live endpoint query plus fresh Lakebase ledger-verified exact inference-row proof for the current deployment SHA.
metadata:
  type: project
---

**Location:** the runtime claim body lives in
`backend/services/ai_gateway_capability_probe.py::probe_ai_gateway`;
`capabilities.py::_probe_ai_gateway` delegates to it. The verifier/writer is
`tools/databricks/verify_ai_gateway_exact_proof.py`; runtime app requests read
the ledger but never write it.

**Claimable path:**
1. Verify the configured serving endpoint is READY, AI Gateway inference
   logging is enabled, the configured table prefix matches, SQL can see the
   prefixed inference-log table(s), and `MIP_GIT_SHA` is valid.
2. Send a bounded live endpoint query with a fresh
   `client_request_id = mip-capability-{full-40-char-sha}-{uuid16}`.
3. Read `mip_app.ai_gateway_proof_ledger` for a fresh `status='verified'`
   row matching current deployment SHA AND `endpoint_name` AND
   `inference_table` (three-way bind in the SELECT WHERE, ledger.py:47-58),
   `verified_at IS NOT NULL AND verified_at >= now()-FRESHNESS`. The verified
   row must have been written by `verify_ai_gateway_exact_proof.py` after
   observing an exact inference-log row for that client request id.
4. Return `available=True` only when both the live endpoint query and the
   fresh verified ledger row exist (`proof is not None` is the SOLE gate,
   probe.py:103). Endpoint configuration, queryable inference tables,
   historical rows, or SHA-scoped prefix counts are non-claimable. The prefix
   row COUNT (`count_inference_log_rows_by_prefixes`) is detail-enrichment
   only ("Current deployment inference rows visible: N") and never gates.

**Why it cannot be gamed (verified 2026-07-02 @ 17c1a8b):**
- Runtime public/admin traffic cannot mint ledger rows; only the deployment
  verifier + nightly write `mip_app.ai_gateway_proof_ledger`. No `backend/api/`
  router imports `insert_pending_proof`/`mark_proof_verified`/
  `mark_expired_pending_proofs`; route-reachable code imports only
  `normalize_gateway_sha` (pure) and `latest_verified_proof` (read).
- `normalize_gateway_sha` requires EXACTLY 40 hex chars (ledger.py:28); no
  sha12-prefix collision is possible. DB CHECK constraints enforce
  `git_sha ~ '^[0-9a-f]{40}$'` and `client_request_id ~
  '^mip-capability-[0-9a-f]{40}-[0-9a-f]{16}$'`.
- NULL-`verified_at`-with-`status=verified` is impossible in 3 layers: DB
  constraint `ck_ai_gateway_proof_verified_fields`, SQL WHERE
  `verified_at IS NOT NULL`, and `_proof_from_row` raises ValueError.
- Ledger unavailable (`lakebase is None`) fails closed (probe.py:39-40).
- Async inference-table delivery is handled by the verifier's pending ledger
  rows and `send --wait`/`verify-pending`, not by weakening runtime claims.
  Deploy runs `send --wait` (strict-gated by MIP_REQUIRE_AI_GATEWAY_CLAIMABLE);
  nightly runs `verify-pending --require-verified` pinned to `github.sha`.
- `scripts/smoke_live.sh` requires available AI Gateway capability rows to
  disclose "exact inference-row round-trip verified"; async recent-row wording
  is no longer acceptable. Smoke available/configured branches are mutually
  exclusive on status; drift vs. probe detail is zero.
- §2 run-card AI Gateway chip binds to `gateway_client_request_id` presence
  (synchronous routing signal), renders `review_required` never `passed`, and
  the request id is asserted absent from the public response body
  (`test_growth_agent_orchestrator.py:155`) — persisted only to internal
  Lakebase `agent_evidence`. `_run_response_from_row` reads a fixed evidence
  allowlist that excludes `gateway_client_request_id`.

**Platform-eligibility degrade (verified 2026-07-07 @ da0a397 + 0613585):**
Databricks tightened AI Gateway endpoint-type eligibility and wiped the prior
per-endpoint gateway config on the managed Supervisor Agent endpoint.
`ensure_ai_gateway_on_endpoint` (tools/databricks/provision_agentic_resources.py:250)
now swallows ONLY the exact marker `"AI Gateway is currently only supported for"`
(warn + return None); every other RuntimeError (e.g. PERMISSION_DENIED) still
raises. With no gateway env emitted, `capabilities.py` reports `ai_gateway` as
`not_provisioned` / `claimable=False` (the unchanged `_CLAIMABLE` gate). The
smoke always-on guard (scripts/smoke_live.sh:335-347) added a third accepted
branch: `not_provisioned` requires `claimable==false` AND detail matching
`Disabled|not provisioned|missing`; STRICT mode (`REQUIRE_AI_GATEWAY_CLAIMABLE=1`,
lines 353-362) still fails any non-`available` row. No claimable gate was
touched by either commit (git-confirmed: da0a397 = provisioning + its test only;
0613585 = smoke only).

**History (do not regress):** prior builds accepted an async recent
deployment-scoped fallback because AI Gateway inference rows can arrive after a
bounded wait. The product-owner directive now rejects that fallback: no
prefix-count or recent-row evidence may make AI Gateway claimable. Preserve the
fresh live endpoint precheck, the current-SHA ledger lookup, and the strict
smoke wording.

**Tests that pin it:** `tests/unit/test_capabilities.py` covers current-SHA
verified-ledger success and rejects missing, stale, or wrong-SHA ledger rows.
`tests/unit/test_ai_gateway_exact_proof.py` covers ledger freshness, pending
verification, verifier `send --wait`, and `--require-verified` on an existing
current-SHA proof. `tests/unit/test_growth_agent_api.py` pins public redaction
while disclosing fresh exact proof through the deployment ledger.
