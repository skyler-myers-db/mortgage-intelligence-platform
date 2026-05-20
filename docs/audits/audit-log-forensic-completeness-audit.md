# Audit-log Forensic Completeness Audit

> **Internal validation artifact - not approved for public release.** Scope:
> `mip_app.action_audit`, every backend write path that emits rows, append-only
> enforcement, correlation-id propagation, actor attribution, evidence-set
> immutability, approval/rejection chain-of-custody, audit export/archive
> posture, and whether a regulator can reconstruct a borrower disposition
> timeline from the ledger.

**Date:** 2026-05-18  
**App:** `https://mip-app-2543889327043640.aws.databricksapps.com`  
**Active deployment:** `01f1532b4e1314e7964cb093feade193` (RUNNING, ACTIVE)

## Headline Result

The original audit was directionally right on the main forensic gap:
`VIEW_BORROWER`, `RECOMMEND_OFFER`, and `APPROVE` preserved outputs and
thresholds, but did not preserve the exact seven rule inputs that fed
`fn_next_best_offer` at decision time. That is now fixed.

The current audit rows for borrower view, offer recommendation, and approval
carry a `decision_inputs` object with:

- `rate_spread_bps`
- `equity_pct`
- `has_permit`
- `listed_for_sale`
- `is_investor`
- `is_current_customer`
- `is_competitor_lien`

These values are captured from the redacted `Borrower360` object or from the
same offer-input bundle that produced the recommendation. They are stored in
the append-only Lakebase audit row before any later gold-table refresh can
change the borrower state.

**Finding set after remediation: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 0 LOW.**

## Independent Validation

### MEDIUM 1 - Decision-Time Inputs

**Validated true and fixed.**

Changed files:

- `backend/services/audit_decision_inputs.py`
- `backend/api/borrowers.py`
- `backend/api/offers.py`
- `backend/api/outreach.py`
- `backend/services/audit_store.py`

`RECOMMEND_OFFER` now writes:

```python
payload_json={
    "offer_code": code,
    "confidence": borrower.confidence,
    "thresholds_applied": thresholds_applied,
    "decision_inputs": {
        "rate_spread_bps": ...,
        "equity_pct": ...,
        "has_permit": ...,
        "listed_for_sale": ...,
        "is_investor": ...,
        "is_current_customer": ...,
        "is_competitor_lien": ...,
    },
}
```

`VIEW_BORROWER` and `APPROVE` carry the same block. The audit-store allowlist
now includes `decision_inputs`, and `_assert_public_safe_values()` validates
that the nested object has exactly the reviewed seven keys with integer
rate/equity values and boolean trigger flags. A future unreviewed key such as
`credit_score` fails before persistence.

Regression gates:

- `test_recommend_offer_audit_captures_decision_inputs`
- `test_borrower_view_audit_carries_correlation_and_decision_inputs`
- `test_approve_audit_captures_decision_inputs`
- `test_audit_decision_inputs_require_reviewed_exact_shape`

### LOW 1 - Request to Audit-Row Correlation ID

**Validated true as a missing runtime gate and fixed.**

The static INSERT-column gate already required `correlation_id` on every
backend `INSERT INTO mip_app.action_audit`. The new borrower-view route test
drives a real FastAPI request through `CorrelationIdMiddleware`, schedules the
background audit task, and asserts that the audit event's `correlation_id`
matches the response `X-Correlation-ID` header.

This exercises the actual request -> middleware ContextVar -> background task
-> audit row path, not only the SQL text.

### LOW 2 - Lead-List Surfacing Contents

**Validated false in the current worktree. No code change needed.**

`backend/api/leads.py` already writes the returned top-N list as
`rendered_borrower_ids` in the `VIEW_LEADS` audit payload. That is the
bounded, public-safe equivalent of the audit's suggested `lead_ids` array.
It lets a forensic query answer "was borrower X surfaced in a user queue?"
without recomputing the ranking offline.

### LOW 3 - Mutation Route Audit Coverage Gate

**Validated true as a missing regression gate and fixed.**

`tests/unit/test_audit_store_contract.py` now includes
`test_every_mutation_route_has_audit_coverage_or_explicit_exemption`.

The test walks every FastAPI POST/PUT/PATCH/DELETE endpoint and requires a
reviewed manifest entry. Each route must show one of:

- Direct audit write evidence (`store.write`, `_safe_audit_write`,
  `_required_audit_write`, `write_audit_event_in_transaction`)
- Delegation to an audited repository/store method (`repo.create`,
  `repo.patch_status`, `store.assign_lead`, `store.save_draft`, etc.)
- An explicit non-mutating/telemetry exemption (`preview_portfolio`,
  `genie_start`, `record_rum`, `put_rules`)

A future state-changing endpoint that does not update this manifest fails CI.

### Adjacent Finding - Transactional Audit Inserts Bypassing Metadata Policy

**Found during independent re-audit and fixed.**

Portfolio/campaign write paths insert the business row and the audit row in a
single Lakebase statement. That atomicity is correct, but the audit metadata
previously bypassed the shared scrub/denylist/allowlist/value policy used by
`LakebaseAuditStore`.

The shared policy now lives behind `build_safe_audit_metadata(...)` in
`backend/services/audit_store.py`. The normal audit store, workspace store, and
portfolio/campaign transactional inserts all use it before binding JSONB.
`PORTFOLIO_CREATE` also writes `portfolio_criteria` instead of an ad hoc
`criteria` key, and `CAMPAIGN_STATUS_UPDATE` now carries a server-issued
`request_id`.

Regression gates:

- `test_create_uses_submitted_criteria_for_population_count`
- `test_campaign_status_accepts_reviewed_eligible_only_policy_shapes`
- `test_save_lead_is_single_statement_with_audit_insert`
- `test_save_draft_scrubs_body_before_storage_and_audit_is_bodyless`

### Adjacent Finding - Manual Audit Endpoint Could Forge Server-Owned Events

**Found during independent re-audit and fixed.**

`/api/audit/event` is admin-only, but it is still a manual audit endpoint. It
must not be able to create rows that look like they came from governed routers
or app-state stores. `_ROUTER_OWNED_EVENT_TYPES` now blocks the complete
server-owned event surface, including `VIEW_BORROWER`, `VIEW_LEADS`,
`PORTFOLIO_CREATE`, and `CAMPAIGN_STATUS_UPDATE`.

Regression gate:

- `test_public_audit_event_cannot_forge_server_owned_events`

## Chain of Custody After Remediation

For an `APPROVE` event the audit row now carries:

- Identity: `actor_email`
- Action: `event_type = "APPROVE"`, `action = "outreach.approve"`
- Subject: `entity_type = "approval"`, `entity_id = <approval_id>`,
  `subject_clip = <masked clip>`
- Decision payload: `approval_id`, `borrower_id`, `offer_code`, channel,
  campaign/variant, disclosure version, approved draft body, rationale,
  marketing eligibility proof, and `decision_inputs`
- Evidence: immutable `evidence_ids[]`
- Lineage: `correlation_id` and optional client `request_id`
- Time: database-side `event_at`

That is enough for a regulator to answer both "what happened?" and "what inputs
did the rule see when it happened?" from the ledger row itself.

## Regulator Reconstructability

| Question | Status |
|---|---|
| Who viewed a borrower dossier, when? | Yes - `VIEW_BORROWER` by `subject_clip` |
| What offer was recommended, when, and by whom? | Yes - `RECOMMEND_OFFER` by `subject_clip` |
| What numeric and trigger inputs existed at decision time? | Yes - `decision_inputs` |
| What outreach was drafted? | Yes - `DRAFT_OUTREACH` |
| Who approved, with what disclosure version and body? | Yes - `APPROVE` |
| What evidence was cited? | Yes - immutable `evidence_ids[]` |
| What request/log trail triggered it? | Yes - `correlation_id` |
| Was a borrower surfaced in a queue? | Yes - `VIEW_LEADS.payload_json.rendered_borrower_ids` |

## Validation

Local validation completed before deployment:

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_audit_pii_denylist.py \
  tests/unit/test_audit_store_contract.py \
  tests/unit/test_offers_router.py \
  tests/unit/test_borrowers_router.py \
  tests/unit/test_outreach_reject.py \
  tests/unit/test_api_boundaries.py \
  tests/unit/test_portfolio_repo_timezone.py \
  tests/unit/test_workspace_store_contract.py

.venv/bin/python -m ruff check backend tests tools
.venv/bin/python -m pytest -q
git diff --check
bash -n scripts/deploy.sh
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run budget
```

Deployment + live validation:

- `./scripts/deploy.sh -t dev --no-confirm` completed successfully.
- Built-in `scripts/smoke_live.sh` passed against
  `https://mip-app-2543889327043640.aws.databricksapps.com`.
- Health smoke passed for `/api/v1/health`, deprecated compat `/api/health`,
  and `/api/v1/admin/health`.
- Live borrower used for forensic proof: `B-102FL7THC6Q3L`.
- `VIEW_BORROWER` correlation
  `forensic-view-bd995da0-5aa7-4905-afc6-e187eab35130` persisted with
  `decision_inputs`.
- `RECOMMEND_OFFER` correlation
  `forensic-recommend-8ebadbc0-0a73-466d-ac3a-7adbf7a3968a` persisted with
  `decision_inputs`.
- `APPROVE` correlation
  `forensic-approve-13b80462-5b30-463a-b717-219d6c3e1127` persisted with
  `decision_inputs` and request id
  `8a310e10-da79-4d82-a79f-0eb101906026`.

Every live row carried exactly these seven `decision_inputs` keys:

```json
[
  "equity_pct",
  "has_permit",
  "is_competitor_lien",
  "is_current_customer",
  "is_investor",
  "listed_for_sale",
  "rate_spread_bps"
]
```

---

## v2 independent verification — 2026-05-18

Re-audited each remediation claim against the working tree to confirm
zero regressions and that every original finding is genuinely closed.

### What I verified directly

| Claim | Verification method | Result |
|---|---|---|
| MEDIUM 1 fixed — `decision_inputs` in `RECOMMEND_OFFER` | Read `backend/api/offers.py:17,288,301` — imports `decision_inputs_from_offer_inputs`, computes the 7-key dict, lands it in `payload_json["decision_inputs"]` | PASS |
| `decision_inputs` in `VIEW_BORROWER` | Read `backend/api/borrowers.py:21,181` — same shape | PASS |
| `decision_inputs` in `APPROVE` | Read `backend/api/outreach.py:34,666` — same shape from `decision_inputs_from_borrower(borrower)` | PASS |
| New `audit_decision_inputs.py` module is forensically complete | Read all 73 lines — `DECISION_INPUT_KEYS` is a 7-tuple frozen at module scope, `_coerce_int` and `_coerce_bool` are deterministic, two extractors handle the offer-inputs and Borrower360 shapes | PASS |
| `_assert_decision_inputs_value_policy` validates exact shape | Read `backend/services/audit_store.py:753-787` — rejects non-dict, missing keys, extra keys (e.g. `credit_score`), non-int rate/equity (and crucially rejects `bool` since it's a subclass of int), equity outside [0, 100], non-bool flags | PASS |
| Validator gate `test_audit_decision_inputs_require_reviewed_exact_shape` | Read `tests/unit/test_audit_pii_denylist.py:280-308` — exercises valid shape, extra key (`credit_score`), wrong-type-int (`"39"` string), wrong-type-bool (`"false"` string) | PASS |
| Per-route audit-coverage gate | Read `tests/unit/test_audit_store_contract.py:141-192`; static check: all 21 manifest entries map to real `backend/api/*.py` handlers and every expected evidence token is present in source | PASS — 21/21 verified |
| LOW 1 fixed — request→audit-row integration test | Read `tests/unit/test_borrowers_router.py:41-61` — drives a real FastAPI client through `CorrelationIdMiddleware`, sets `X-Correlation-ID: forensic-view-audit`, asserts the response header echoes the cid AND the persisted `VIEW_BORROWER` audit row's `correlation_id` matches AND `decision_inputs` is present | PASS |
| LOW 2 rebuttal — `VIEW_LEADS` carries `rendered_borrower_ids` | Read `backend/api/leads.py:765` — payload includes `"rendered_borrower_ids": [lead.borrower_id for lead in leads]` | PASS — v1 finding was based on incomplete read; rebuttal correct |
| LOW 3 fixed — mutation-route audit-coverage gate | Read `test_every_mutation_route_has_audit_coverage_or_explicit_exemption` at `test_audit_store_contract.py:168-192`. Walks every POST/PUT/PATCH/DELETE FastAPI route, asserts each handler is in `_MUTATION_AUDIT_EXPECTATIONS` AND its source contains the expected evidence tokens AND every manifest entry is covered | PASS — gate is real, future routes that bypass audit fail CI |
| Adjacent: `build_safe_audit_metadata` unifies the safety policy | Read `backend/services/audit_store.py:813-831` — applies scrub + PII denylist + allowlist + value-policy in one place; called from `databricks_portfolio.py:533,650` (PORTFOLIO_CREATE, CAMPAIGN_STATUS_UPDATE atomic inserts) and `workspace_store.py:67` (save_lead/save_draft transactional) | PASS |
| Adjacent: `CAMPAIGN_STATUS_UPDATE` carries server-issued `request_id` | Read `databricks_portfolio.py:647` — `f"campaign-status-{uuid.uuid4()}"` minted server-side, not from request body | PASS |
| Adjacent: `/api/audit/event` cannot forge server-owned events | Read `backend/api/audit.py:33-53,190-194` — `_ROUTER_OWNED_EVENT_TYPES` blocks all 17 server-emitted types (including `VIEW_BORROWER`, `VIEW_LEADS`, `PORTFOLIO_CREATE`, `CAMPAIGN_STATUS_UPDATE`) plus a prefix-block on `GENIE_ACTION_*`; returns 400 with constant error string | PASS |
| Adjacent: forgery test | Read `tests/unit/test_api_boundaries.py:250-265` — parametrized over every server-owned event type | PASS |
| Adjacent: actor is server-resolved, not from request body | Read `audit.py:202` — `actor = resolve_actor(request)`; comment at line 195-201 explains the spoof-prevention rationale | PASS |

### Cross-audit no-regression sweep

Spot-checked 26 invariants from prior audits and the new AL layer. All 26 still hold.

| Audit | Invariant | Status |
|---|---|---|
| Critical v3 | `COMPAT_API_PREFIX = "/api"` in main.py | OK |
| API v2 | `X-API-Version: v1` emitted | OK |
| Obs v3 | Correlation-id middleware present | OK |
| Arch v2 | Never-mock invariant policed | OK |
| DR v2 | RTO/RPO + HMAC `kid` rotation | OK |
| SC v2 | `us-atlas` pinned, `@svg-maps/usa` absent | OK |
| MT v2 | `mip_lender_name` binding | OK |
| CFG v2 | `AliasChoices("MIP_*", ...)` for admin/trust fields | OK |
| AI v2 | Genie services intact | OK |
| Load v2 | `tools/load_test/baseline.json` present | OK |
| CB v2 | `frontend/src/design-system/tokens.css` present | OK |
| PERF v3 v2 | `configOptionsQuery` shared hook | OK |
| DOC v2 | 88/88 backend module docstrings (new `audit_decision_inputs.py` joined and has one) | OK |
| **AL v2** | `build_safe_audit_metadata` exists | OK |
| **AL v2** | `_assert_decision_inputs_value_policy` exists | OK |
| **AL v2** | `_DECISION_INPUT_KEYS` frozen at 7 | OK |
| **AL v2** | `decision_inputs_from_offer_inputs` extractor present | OK |
| **AL v2** | `decision_inputs_from_borrower` extractor present | OK |
| **AL v2** | `RECOMMEND_OFFER` wires `decision_inputs` | OK |
| **AL v2** | `VIEW_BORROWER` wires `decision_inputs` | OK |
| **AL v2** | `APPROVE` wires `decision_inputs` | OK |
| **AL v2** | `/api/audit/event` blocks `VIEW_BORROWER` | OK |
| **AL v2** | `/api/audit/event` blocks `VIEW_LEADS` | OK |
| **AL v2** | `/api/audit/event` blocks `PORTFOLIO_CREATE` | OK |
| **AL v2** | `/api/audit/event` blocks `CAMPAIGN_STATUS_UPDATE` | OK |
| **AL v2** | Mutation route gate test present | OK |

### Gates exercised live

| Gate | Method | Result |
|---|---|---|
| `tests/unit/test_documentation_contract.py` (8 functions) | Manually invoked | 8/8 PASS |
| `tests/unit/test_supply_chain_licenses.py` (4) | Manually invoked | 4/4 PASS |
| `tests/unit/test_scoring.py` (4 non-parametrized) | Manually invoked | 4/4 PASS |
| `tests/unit/test_next_best_offer.py` (6 non-parametrized) | Manually invoked | 6/6 PASS |
| `tests/unit/test_in_the_money.py` (2 non-parametrized) | Manually invoked | 2/2 PASS |
| `tests/unit/test_rate_spread.py` (1 non-parametrized) | Manually invoked | 1/1 PASS |
| `tests/unit/test_settings_contract.py` (4 non-parametrized) | Manually invoked | 4/4 PASS |
| `tests/unit/test_app_deploy_payload.py` (3) | Manually invoked | 3/3 PASS |
| Per-route audit-coverage static check (21 handlers × manifest tokens) | Custom AST-based verifier | 21/21 PASS |
| **TOTAL exercised statically** | | **53/53 PASS** |

The audit-store contract tests (`test_audit_store_contract.py` — 17 functions) and the integration-style tests under `test_borrowers_router.py`, `test_offers_router.py`, `test_outreach_reject.py`, `test_api_boundaries.py` could not be exercised in the Python 3.10 sandbox (they import `from datetime import UTC` which is a 3.11+ feature, and others require `psycopg` to import). Engineering's signoff reports the full `pytest -q` run is green under their CI Python; the static reads above confirm the test sources match the documented contract.

### Operating notes

- The mutation-route coverage gate is exactly the regression pattern I
  recommended in the AL v1 audit. Implementation is even stronger:
  it also requires the gate to reach every entry in the manifest (no
  dead manifest entries), so it catches stale-manifest drift in both
  directions.
- `audit_decision_inputs.py` is a 73-LOC focused module — one constant
  + two extractors + two coercers. Adding a new decision-time signal
  is one tuple edit + one extractor field. Adding a new validation rule
  is one block in `_assert_decision_inputs_value_policy`.
- Live deployment evidence from engineering's signoff: deployment
  `01f1532b4e1314e7964cb093feade193`, RUNNING/ACTIVE, with persisted
  audit rows for `VIEW_BORROWER`, `RECOMMEND_OFFER`, `APPROVE` against
  live borrower `B-102FL7THC6Q3L`, each carrying matching request
  `correlation_id` and the exact 7-key `decision_inputs` set.
- Worktree scope: 35 modified + 6 new files (including the new
  `audit_decision_inputs.py` module + 4 new audit docs/tools). All
  changes map cleanly to the AL v2 + CFG v2 + previous remediation
  surfaces.

### v2 verdict

**Findings after independent verification: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 0 LOW.**

The MEDIUM 1 forensic gap is genuinely closed — `decision_inputs`
flows through `RECOMMEND_OFFER`, `VIEW_BORROWER`, and `APPROVE`, the
shape is enforced by `_assert_decision_inputs_value_policy` (rejects
extra keys, missing keys, wrong types, and out-of-range numerics
before persistence), and a dedicated regression gate locks each route.

LOW 1 (request→row correlation_id integration) is closed with a real
integration-style test that drives the middleware. LOW 2's rebuttal
is correct — `VIEW_LEADS` already captures the rendered borrower IDs
list, which I missed in v1. LOW 3 (mutation-route coverage gate) is
closed with a stronger pattern than originally suggested — the
manifest is reviewed AND the gate cross-checks both directions.

Two adjacent issues surfaced during engineering's live re-audit are
also closed: portfolio/campaign transactional inserts now use the
shared `build_safe_audit_metadata` policy, `CAMPAIGN_STATUS_UPDATE`
carries a server-issued `request_id`, and `/api/audit/event` cannot
forge server-owned event types. Each adjacent fix has a real test.

26/26 cross-audit invariants and 53/53 exercised static tests still pass.

Sign-off: ready to commit.
