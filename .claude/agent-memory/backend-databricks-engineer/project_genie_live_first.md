---
name: genie-live-first
description: Genie answer routing is live-first by default; canonical interceptors are a disclosed degraded fallback, gated by mip_genie_live_first
metadata:
  type: project
---

Live Genie is the PRIMARY answer path in `DatabricksGenieRepository.respond`
(backend/services/repositories/databricks_genie.py). The ~25 `direct_canonical_response`
interceptors (databricks_genie_direct.py) are demoted to an honest **degraded-mode
fallback**, consulted only inside `_degraded` when the breaker is OPEN or a live
turn raises `DependencyDownError`. `GenieClientError` still re-raises (no fallback).

**Why:** user directive — "nothing in this App is scripted/canned; dynamic genuine
intelligence throughout." Interceptor-first ordering made most demo questions
answer from hand-authored SQL before Genie was ever consulted.

**How to apply:**
- Routing knob: `settings.mip_genie_live_first: bool = True` (env `MIP_GENIE_LIVE_FIRST`).
  `False` = legacy/emergency booth posture (interceptor-first, zero LLM latency).
- Degraded fallback answers keep `source="trusted_sql"` (reviewed, executed SQL)
  but are stamped by `_annotate_degraded_fallback` with the disclosure
  "Live Genie is temporarily unavailable..." in `proof.known_data_gaps` AND the
  answer text. Only `trusted_sql` responses are stamped (guide/data_gap make no
  live-data claim). Never present fallback as live.
- There is NO genie answer cache today (checked genie_client / resilience /
  repository — none exists). Do not add one unless asked.
- Genie throttle: backpressure.py classifies `/api/genie*` as the `genie` lane
  (`mip_rate_limit_genie_per_minute`, default 30) + a genie semaphore
  (`mip_genie_concurrency_limit`, default 6). Workspace Genie limit is ~5
  conversation-starts/min; the lane already backstops this. Don't add throttling.

**Voice preservation (second canned layer, fixed 2026-07-08):** the recognized-shape
handlers in `_canonical_genie_answer` (~13) used to REPLACE Genie's narrative with
hand-authored templates AND drop live fields. Fix = one post-processor
`_restore_live_voice(canonical, result)` at the single `_adapt_genie_response` call
site: governance (re-executed counts, proof, viz, actions, scrubbing) kept, but the
ANSWER becomes Genie's own narrative with a short `_default_verification_note`
("Verified against <asset>: <metric> at the trusted gold grain.") APPENDED not
replacing; genie_status / reasoning_trace / native_visualization / follow_up_questions
carried through like the generic path; empty narrative after repair keeps the template
but adds gap "Genie returned no narrative; presenting the verified deterministic
summary." The degraded fallback path (direct_canonical_response) still uses templates
verbatim — correct, no live narrative exists in degraded mode. Coordinator-accepted
tradeoff: rich template explanations are no longer forced into the answer; Genie voice
leads. Pins: test_recognized_shape_* in test_genie_repository.py.

**Tests:** `_StubClient`/`_GenieClient` breaker state drives the path. Tests that
formerly pinned "canonical without genie call" now use breaker `open` to exercise
the degraded fallback + disclosure. Dedicated routing pins live at the end of
test_genie_repository.py. Guardrail battery: tests/unit/test_genie_guardrail_battery.py
(110 legit → zero matches, 23 must-refuse → correct class).

**Guardrail narrowing:** scope_bypass DDL regex in genie_prompt_guardrails.py was
narrowed (2026-07-08) — bare verbs (use/set/update/create/merge) false-positived on
ordinary analytics English; each verb now requires its SQL object/syntax. PII /
protected-class / instruction-override kept strict.
