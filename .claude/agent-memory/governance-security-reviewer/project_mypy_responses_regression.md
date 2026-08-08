---
name: mypy-responses-regression
description: Recurring mypy arg-type break on FastAPI responses= dict when the shared 415 constant loses its type annotation
metadata:
  type: project
---

The FastAPI `responses=` 415 constant (`JSON_CONTENT_TYPE_RESPONSE` in `backend/services/http_content.py`) must be annotated `dict[int | str, dict[str, Any]]`, else mypy infers `dict[int, dict[str, Collection[str]]]` and the `mypy (ratcheted type gate)` CI step fails at every `@router.post(..., responses=...)` call site (audit.py, growth_agent.py). A failing mypy step short-circuits CI so pytest never runs.

**Why:** This has broken twice. Commit `d3891d9 "Satisfy Growth Agent CI gates"` first fixed it by annotating `_JSON_CONTENT_TYPE_RESPONSE` inside `backend/api/growth_agent.py`. The next commit `15ebef2 "Harden growth agent governance boundaries"` extracted that constant into the shared `http_content.py` module and dropped the annotation in the move, re-introducing all 5 errors (now also consumed by audit.py).

**How to apply:** When reviewing any refactor that moves or re-uses the 415 `responses=` constant, confirm the explicit `dict[int | str, dict[str, Any]]` annotation survives the move. The fix is one line at the constant's definition; do not annotate per-call-site. Validate with `.venv/bin/mypy backend` (expect "Success: no issues found"). Related: the prompt no-echo control depends on the path-prefix filter in [[growth-agent-noecho-handler]].
