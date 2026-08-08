---
name: no-pydantic-validation-on-pii-fields
description: Never validate PII-bearing request fields with a Pydantic validator/constraint — the 422 echoes the raw input; validate in the route instead
metadata:
  type: feedback
---

For any request field that may contain PII (free-text comments, notes, names),
do NOT enforce format/length/PII rules with a Pydantic `field_validator` or a
`Field(max_length=...)` constraint.

**Why:** FastAPI's default `RequestValidationError` handler serializes each
Pydantic error including its `input` field — the raw offending value — into the
422 response body. So a validator that raises on PII causes the 422 to reflect
that PII straight back to the caller/logs. The app's `RequestValidationError`
handler in `backend/main.py` only strips `input` for `/growth-agent` paths, not
globally. Confirmed on `POST /api/genie/feedback`: a `field_validator` on
`comment` made the 422 echo `"call me at 415-555-0199"` / `"Jane Doe"`.

**How to apply:** Keep the Pydantic field permissive (e.g. `comment: str | None
= None`, no `max_length`). Validate inside the route via the public-safe
validators in `backend/schemas/common.py` (`validate_public_free_comment`,
`contains_pii_marker`, etc.) and raise `HTTPException(status_code=422,
detail=<fixed message>)`. Those validators' error strings are fixed and never
include the offending text, so `str(exc)` is safe to surface. See
[[project_r5_governance_fixes]] for the no-body-in-logs posture.
