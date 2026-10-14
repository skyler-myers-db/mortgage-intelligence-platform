"""NB-1: a masked borrower id that reads as a phone number inside an id list.

A masked id is ``B-`` plus 13 upper-case base-36 characters, so a few in a
thousand carry a ten-digit run (``B-5551234567XYZ``). The deep PII scan of
``_metadata_pii_value_paths`` read that run as a phone number and refused the
whole audit write with ``AuditPIIError``, which surfaced as a 500 on approval
requests, exports, distribution and the Lead Queue's VIEW_LEADS row.

The fix is a CLOSED-SHAPE, path-scoped exemption: only a raw string that
fullmatches ``B-[0-9A-Z]{13}``, as a list element directly under
``borrower_ids``, ``rendered_borrower_ids`` or ``skipped_by_reason.<one of the
four closed skip reasons>``, skips the free-text scan. Every lookalike at those
paths, and the same id anywhere else, is still refused with ``AuditPIIError``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from backend.schemas.approval_request import SKIP_REASONS
from backend.schemas.common import contains_pii_marker
from backend.services.audit_store import (
    AuditPIIError,
    _assert_no_pii,
    build_safe_audit_metadata,
)
from backend.services.pii_redaction import scrub_free_text

BATCH = "0d2c0f4e-6b6a-4b8e-9a51-3c0f7b1d2e4f"
# Phone-shaped masked ids the old scan refused, and a control it never did.
PHONE_SHAPED_IDS = ("B-5551234567XYZ", "B-1555123456789", "B-05551234567XY")
CONTROL_ID = "B-0A1B2C3D4E5F6"
LOOKALIKES = (
    "555-123-4567",
    "B-555-123-4567",
    "B-555 123 4567",
    "b-5551234567xyz",
    "B-5551234567XYZ0",
    "B-5551234567XY",
    "B-5551234567XYZ\n",
    "B-5551234567XYZ call me back",
    "jane.doe@lender.example",
)

# (event type, audit action, payload builder): the real writer's shape for
# each event that carries a masked-id list.
PayloadBuilder = Callable[[str], dict[str, Any]]
EVENTS: tuple[tuple[str, str, PayloadBuilder], ...] = (
    (
        "APPROVAL_REQUESTED",
        "outreach.approval_request",
        lambda bid: {
            "approval_request_batch_id": BATCH,
            "borrower_ids": [bid],
            "requested_count": 1,
            "skipped_count": 0,
            "rationale": "Rate-drop refinance wave",
        },
    ),
    ("LEAD_EXPORT", "lead.export", lambda bid: {"borrower_ids": [bid]}),
    (
        "LEAD_DISTRIBUTE",
        "lead.distribute",
        lambda bid: {"borrower_ids": [bid], "assigned_count": 1, "strategy": "manual"},
    ),
    ("VIEW_LEADS", "view_leads", lambda bid: {"rendered_borrower_ids": [bid], "limit": 50}),
    *(
        (
            "APPROVAL_REQUEST_REFUSED",
            "outreach.approval_request_refused",
            lambda bid, reason=reason: {
                "borrower_ids": [CONTROL_ID],
                "requested_count": 0,
                "skipped_count": 1,
                "skipped_by_reason": {reason: [bid]},
                "rationale": "Rate-drop refinance wave",
            },
        )
        for reason in SKIP_REASONS
    ),
    (
        "APPROVAL_REQUEST_WITHDRAWN",
        "outreach.approval_request_withdraw",
        lambda bid: {
            "approval_request_batch_id": BATCH,
            "withdrawn_count": 1,
            "borrower_ids": [bid],
        },
    ),
)
EVENT_IDS = [f"{event}-{index}" for index, (event, _action, _build) in enumerate(EVENTS)]


def test_the_probe_ids_really_are_phone_shaped_to_the_free_text_scan() -> None:
    # Non-vacuity: each accepted probe trips the scan the exemption skips,
    # and the control trips nothing.
    for masked_id in PHONE_SHAPED_IDS:
        assert contains_pii_marker(masked_id) or scrub_free_text(masked_id) != masked_id
    assert not contains_pii_marker(CONTROL_ID)
    assert scrub_free_text(CONTROL_ID) == CONTROL_ID


@pytest.mark.parametrize(("event", "action", "build"), EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize("masked_id", (*PHONE_SHAPED_IDS, CONTROL_ID))
def test_a_masked_id_in_its_reviewed_list_is_accepted(
    event: str, action: str, build: PayloadBuilder, masked_id: str
) -> None:
    metadata = build_safe_audit_metadata(build(masked_id), action=action)
    assert masked_id in str(metadata), event


@pytest.mark.parametrize(("event", "action", "build"), EVENTS, ids=EVENT_IDS)
@pytest.mark.parametrize("lookalike", LOOKALIKES)
def test_a_lookalike_in_the_same_list_is_still_refused(
    event: str, action: str, build: PayloadBuilder, lookalike: str
) -> None:
    with pytest.raises(AuditPIIError):
        build_safe_audit_metadata(build(lookalike), action=action)


@pytest.mark.parametrize("masked_id", PHONE_SHAPED_IDS)
@pytest.mark.parametrize(
    "shape",
    [
        lambda bid: {"source": {"note": bid}},
        lambda bid: {"source": {"borrower_ids": [bid]}},
        lambda bid: {"skipped_by_reason": {"not_found": bid}},
        lambda bid: {"skipped_by_reason": {"unreviewed_reason": [bid]}},
        lambda bid: {"borrower_ids": [[bid]]},
        lambda bid: {"Borrower_IDs": [bid]},
        lambda bid: {"segment_codes": [bid]},
    ],
    ids=[
        "inside-source",
        "nested-borrower-ids",
        "non-list-skip-reason",
        "unreviewed-skip-reason",
        "nested-list",
        "other-spelling",
        "other-list-key",
    ],
)
def test_the_same_id_anywhere_else_is_still_refused(
    masked_id: str, shape: Callable[[str], dict[str, Any]]
) -> None:
    with pytest.raises(AuditPIIError):
        _assert_no_pii(shape(masked_id))


def test_a_free_text_key_still_scrubs_the_probe_id() -> None:
    metadata = build_safe_audit_metadata(
        {"borrower_ids": [CONTROL_ID], "rationale": f"see {PHONE_SHAPED_IDS[0]}"},
        action="lead.export",
    )
    assert PHONE_SHAPED_IDS[0] not in str(metadata["rationale"])
