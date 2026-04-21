---
name: Slice 7 Genie wiring
description: Live Databricks Genie client, resilience gating, and safe-corpus fallback boundaries
type: project
---

Slice 7 flipped `/api/genie` from a deterministic in-process catalog to the real Mortgage Lead Intelligence Genie Space (default workspace, space id `01f13d4968af1b249dc388fd5b18b195`, repo-committed at `genie/space_id.txt`).

**Why:** DAIS booth demo must show real Genie-grounded conversational Q&A, but the booth network can't be trusted so we need a visible degraded path that never fabricates data.

**How to apply:**

- Happy path: `DatabricksGenieRepository.respond` -> `ResilientGenieClient.ask` -> real Databricks Genie Conversation API (stdlib urllib, no new wheel). `source="genie"`.
- Safe corpus from `backend.services.genie_answers` ONLY fires when the `"genie"` circuit breaker state is OPEN (or a mid-call `DependencyDownError`). `source="fallback"`.
- Unknown question + breaker open -> honest `"The Genie service is warming up..."` message with `source="degraded"` and empty `trusted_assets`. NEVER fabricate data.
- `GenieClientError` (non-2xx, malformed JSON, terminal failed state) from a CLOSED breaker always propagates -- the router's `DependencyDownError` handler translates it to 503. No silent fallback.
- Breaker key is `"genie"`; failure threshold 3, cooldown 20s; `get_breaker("genie")` registers it so `/api/health` reports `genie: up/down` as the third dependency alongside `warehouse` and `lakebase`.
- `GenieClient.ping` is a GET on `/spaces/{id}` -- used by the health probe, doesn't burn a conversation slot, doesn't go through the breaker (so a healing Genie can be detected even while the breaker is open).
- `genie_space_id` resolves in this order: `GENIE_SPACE_ID` env var -> committed `genie/space_id.txt` -> fail-fast `RuntimeError` at first `get_genie_client()` call (lazy; does not gate pytest startup).
