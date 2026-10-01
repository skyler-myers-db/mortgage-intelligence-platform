"""The governed free-text refusal shared by the outreach decision routers.

Moved verbatim out of ``backend/api/outreach.py`` (W5b, the approval ledger)
so the approve/reject router, the revoke router and the approval-request
router answer the same 422 for text the audit ledger would refuse, without a
router importing a router.
"""

from __future__ import annotations

from fastapi import HTTPException

from backend.services.audit_metadata_validation import validate_free_text_metadata_value
from backend.services.audit_store import AuditMetadataValueViolation


def text_policy_refusal(exc: AuditMetadataValueViolation) -> HTTPException:
    """422 naming the refused field; the refused text is never echoed."""

    return HTTPException(status_code=422, detail=f"{exc.field} failed the governed text policy")


def refuse_ungoverned_text(values: dict[str, str | None]) -> None:
    """Refuse free text the audit ledger would refuse, BEFORE any read or write.

    The verdict only: the caller keeps its own scrubbed value, so the decision
    intent's bytes never change. The same check runs again at the write.
    """

    for key, value in values.items():
        if value is None:
            continue
        try:
            validate_free_text_metadata_value(key, value)
        except AuditMetadataValueViolation as exc:
            raise text_policy_refusal(exc) from exc
