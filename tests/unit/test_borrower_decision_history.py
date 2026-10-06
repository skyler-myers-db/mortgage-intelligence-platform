"""The borrower decision history (audit flow-04 phase 2 / tables-10, D-audit-reads-c2).

Pinned here:

1. Server ownership: the legacy decision codes (HOLD, REJECT, OUTREACH_HOLD)
   and SUPPRESS_CONTACT are server-owned, so POST /audit/event refuses them
   and no client-authored row can pose as a governed decision.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_event_types import is_server_owned_audit_event_type

client = TestClient(app)

_FORGEABLE_BEFORE_C2 = ("HOLD", "REJECT", "OUTREACH_HOLD", "SUPPRESS_CONTACT")


# -- 1. server ownership ----------------------------------------------------------------


@pytest.mark.parametrize("event_type", _FORGEABLE_BEFORE_C2)
def test_the_legacy_decision_codes_and_the_contact_block_are_server_owned(event_type: str) -> None:
    assert is_server_owned_audit_event_type(event_type)


@pytest.mark.parametrize("event_type", _FORGEABLE_BEFORE_C2)
def test_post_audit_event_refuses_a_forged_decision_or_contact_block(event_type: str) -> None:
    response = client.post(
        "/api/audit/event",
        json={
            "actor": "attacker@example.com",
            "action": "outreach.custom",
            "entity_type": "borrower",
            "entity_id": "B-0123456789ABC",
            "event_type": event_type,
        },
    )

    assert response.status_code == 400, event_type
    assert response.json()["detail"] == "event type is owned by a governed server route"
