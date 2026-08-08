---
name: ai-gateway-probe-cost
description: ai_gateway live probe fires a real billable gateway query + 90s SQL poll on every ?live=1 admin capabilities load, uncached
metadata:
  type: project
---

`_probe_ai_gateway` in `backend/services/capabilities.py` (~line 746) sends a real query to the gateway serving endpoint to generate a logged inference row, then calls `wait_for_inference_log_increment` which polls SQL every 5s up to 90s.

`collect_request_live_capability_statuses` (`backend/services/capability_request.py`) runs ALL live probes with NO caching and NO rate-limit. It is gated only by the `?live=1` query param on `GET /api/v1/admin/capabilities` (admin-auth via AdminDep). Default `probe_capabilities()` (no `?live`) does NOT run probes — that part is sound.

**Why:** each `?live=1` load = one billable LLM call (max_tokens=64, temp=0 — bounded) + up to 90s of warehouse SQL polling. Repeated admin loads spam the endpoint and pollute the inference table with `mip-capability-*` rows.

**How to apply:** Recommend a short-TTL cache or per-process cooldown on the live probe set (the codebase already has `backend/services/resilience.py` short-TTL cache primitives). The bounded token budget is good; the missing throttle is the gap.

Related: [[audit-schema-grant]]
