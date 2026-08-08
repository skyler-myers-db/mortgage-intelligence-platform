---
name: growth-agent-noecho-handler
description: The growth-agent prompt no-echo PII control is a path-prefix-scoped 422 handler, not the schema validator
metadata:
  type: project
---

The "rejected PII/name prompts no longer echo rejected input" guarantee is enforced in TWO layers, and the load-bearing one is easy to miss:
1. `backend/schemas/growth_agent.py` prompt validator raises a STATIC message (no input interpolation) — necessary but not sufficient.
2. Pydantic v2 still places the raw rejected prompt in `ValidationError.errors()[].input`, which FastAPI serializes into the 422 body. The actual suppression is the custom `RequestValidationError` handler at `backend/main.py:~534` which strips the `input` key ONLY for request paths starting with `/api/v1/growth-agent` or `/api/growth-agent`.

**Why:** The scope is path-prefix based. Any future growth-agent endpoint registered off that prefix (or a rename of the prefix constants) would silently start leaking the raw rejected prompt — including PII/names — back to the client in the 422 body. Non-growth-agent routes intentionally still echo `input`.

**How to apply:** When reviewing new growth-agent routes or changes to `CANONICAL_API_PREFIX`/`COMPAT_API_PREFIX`/the validation handler, verify the no-echo path-prefix still covers the route. Recommend a pinned regression test asserting no `"input"` key and no raw prompt substring in any growth-agent 422 body. Verified holding as of HEAD 6c3d0f4. Related: [[mypy-responses-regression]] (same hardening slice).
