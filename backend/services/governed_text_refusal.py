"""Audited governed-text refusals on the outreach decision routes (ruling O1).

Owner ruling O1 (12.4 #11, W5b): a rationale or justification the governed
text screen refuses on approve, reject, approval-request create or revoke is
a fair-lending oversight signal, so the attempt is recorded as ONE
GOVERNED_TEXT_REFUSED audit row. The row carries only the surface, the
screen's category and a count: never the text, a field value, a borrower id,
a request id or the pattern that matched. The HTTP answer is unchanged: the
same 422 ``<field> failed the governed text policy`` as
``outreach_text_policy.refuse_ungoverned_text``.

What stays unaudited (O1): the approver 409, every schema 422 (including the
pydantic name-shape refusal that runs before a route), the size cap, the
write-time ``AuditMetadataValueViolation`` branches and the NB-1 fixed 422 for
an ``AuditPIIError``. If the refusal row itself cannot be written, the route
answers 503 (fail-closed): a refusal that was not recorded never looks
recorded, and nothing else is read or written.

The verdict is the screen's own (``validate_free_text_metadata_value``); this
module only reads the violation's closed reason to name the screen.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException

from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.audit_metadata_validation import validate_free_text_metadata_value
from backend.services.audit_store import AuditMetadataValueViolation
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.observability import emit
from backend.services.outreach_text_policy import text_policy_refusal

GovernedTextSurface = Literal["approve", "reject", "request", "revoke"]
GovernedTextScreen = Literal["pii", "fair_lending", "other"]

GOVERNED_TEXT_SURFACES: tuple[GovernedTextSurface, ...] = ("approve", "reject", "request", "revoke")
GOVERNED_TEXT_SCREENS: tuple[GovernedTextScreen, ...] = ("pii", "fair_lending", "other")
GOVERNED_TEXT_REFUSED_EVENT = "GOVERNED_TEXT_REFUSED"
GOVERNED_TEXT_REFUSED_ACTION = "outreach.governed_text_refused"
GOVERNED_TEXT_REFUSAL_ENTITY = "governed_text_refusal"

_REASON_MARKER = "failed value policy: "
# The validator's closed reasons (audit_metadata_validation
# .validate_free_text_metadata_value) mapped to the screen that refused.
# Anything not listed is 'other', so a new reason never fails open into a
# finer category than it earned.
_SCREEN_BY_REASON: dict[str, GovernedTextScreen] = {
    "must not contain protected-class targeting language": "fair_lending",
    "must not contain human-name-shaped text or unresolved placeholders": "pii",
    "must not contain unsupported borrower-facing claims": "other",
    "must not contain a contact action that contradicts consent or response handling": "other",
}

_LOG = logging.getLogger("mip.governed_text_refusal")


def screen_for(exc: AuditMetadataValueViolation) -> GovernedTextScreen:
    """The screen category for one refusal: a closed map over its reason."""

    message = str(exc)
    _, marker, reason = message.partition(_REASON_MARKER)
    if not marker:
        return "other"
    return _SCREEN_BY_REASON.get(reason, "other")


def refuse_ungoverned_text_audited(
    lakebase: LakebaseClient,
    *,
    actor: str,
    surface: GovernedTextSurface,
    values: dict[str, str | None],
    item_count: int,
) -> None:
    """Refuse governed text BEFORE any read, recording the refusal (O1).

    Runs the screen over the non-None values in order, exactly as
    ``refuse_ungoverned_text`` does. Allowed text writes nothing. On the first
    refusal: one GOVERNED_TEXT_REFUSED row in its own transaction, then the
    unchanged 422. A Lakebase failure on that write answers 503.
    """

    for key, value in values.items():
        if value is None:
            continue
        try:
            validate_free_text_metadata_value(key, value)
        except AuditMetadataValueViolation as exc:
            screen = screen_for(exc)
            _record_refusal(
                lakebase, actor=actor, surface=surface, screen=screen, item_count=item_count
            )
            emit(_LOG, "governed_text_refused", outcome="refused", surface=surface, screen=screen)
            raise text_policy_refusal(exc) from exc


def _record_refusal(
    lakebase: LakebaseClient,
    *,
    actor: str,
    surface: GovernedTextSurface,
    screen: GovernedTextScreen,
    item_count: int,
) -> None:
    try:
        with lakebase.transaction() as conn:
            write_audit_event_in_transaction(
                conn,
                actor=actor,
                action=GOVERNED_TEXT_REFUSED_ACTION,
                entity_type=GOVERNED_TEXT_REFUSAL_ENTITY,
                entity_id=str(uuid4()),
                payload_json={"surface": surface, "screen": screen, "item_count": item_count},
                event_type=GOVERNED_TEXT_REFUSED_EVENT,
            )
    except LakebaseError as exc:
        # Fail closed: the text is still refused, and a refusal that was not
        # recorded must never answer as though it were.
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
