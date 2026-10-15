"""Owner ruling O1: a governed-text refusal on a decision route is audited.

``backend/services/governed_text_refusal.py`` runs the governed text screen
(the unchanged ``validate_free_text_metadata_value`` verdict) over a decision
rationale before any read. On a refusal it writes ONE GOVERNED_TEXT_REFUSED
row whose metadata is the surface, the screen's closed category and a count,
then answers the same 422 as before. The refused text, a borrower id or the
matched pattern never land; a failed write answers 503 (fail-closed).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from backend.services import governed_text_refusal as gtr
from backend.services.audit_event_types import SERVER_OWNED_AUDIT_EVENT_TYPES
from backend.services.audit_metadata_policy import (
    GOVERNED_TEXT_SCREEN_VALUES,
    GOVERNED_TEXT_SURFACE_VALUES,
    AuditMetadataValueViolation,
)
from backend.services.audit_store import build_safe_audit_metadata
from backend.services.borrower_decision_history import DECISION_HISTORY_EVENT_TYPES
from tests.fixtures.approval_ledger_fake import FakeApprovalLedger

ROOT = Path(__file__).resolve().parents[2]
ACTOR = "decider.one@summit.example"

# One refused sample per screen branch, each taken from an existing corpus:
# test_approval_requests_api.py (protected class), test_outreach_reject.py
# (disability), the human-identity directive (a contact verb + two lower-case
# words; it passes the schema's capitalized name-shape check), the
# unsupported-claim screen and test_round26_consent_response.py (a contact
# action that contradicts a recorded withdrawal).
SCREEN_SAMPLES: list[tuple[str, str]] = [
    ("Prioritize borrowers who are not Hispanic", "fair_lending"),
    ("Focus on borrowers without disabilities.", "fair_lending"),
    ("Call them even if they opted out.", "pii"),
    ("You are pre-approved for a lower rate.", "other"),
    ("The borrower cancels all future calls. Call us to review options.", "other"),
]
ALLOWED = "Rate-sensitive refinance candidates in our footprint."


def _refuse(ledger: FakeApprovalLedger, text: str, **kwargs: Any) -> HTTPException:
    params: dict[str, Any] = {
        "actor": ACTOR,
        "surface": "request",
        "values": {"rationale": text},
        "item_count": 3,
    }
    params.update(kwargs)
    with pytest.raises(HTTPException) as caught:
        gtr.refuse_ungoverned_text_audited(ledger, **params)
    return caught.value


def _stored_strings(row: dict[str, Any]) -> list[str]:
    """Every stored value of an audit row as text, the metadata JSON included."""

    return [json.dumps(value, default=str) if not isinstance(value, str) else value for value in row.values()]


def _windows(text: str, width: int = 6) -> set[str]:
    return {text[i : i + width] for i in range(len(text) - width + 1)}


@pytest.mark.parametrize(("text", "screen"), SCREEN_SAMPLES)
def test_each_screen_branch_records_one_row_with_its_category(text: str, screen: str) -> None:
    ledger = FakeApprovalLedger()

    exc = _refuse(ledger, text)

    assert exc.status_code == 422
    assert exc.detail == "rationale failed the governed text policy"
    [row] = ledger.audits
    assert ledger.statements == ["INSERT_AUDIT"]
    assert row["event_type"] == "GOVERNED_TEXT_REFUSED"
    assert row["entity_type"] == "governed_text_refusal"
    assert row["actor_email"] == ACTOR
    metadata = row["metadata"]
    assert set(metadata) - {"action"} == {"surface", "screen", "item_count"}
    assert (metadata["surface"], metadata["screen"], metadata["item_count"]) == ("request", screen, 3)
    # Never the text: no stored value holds any 6-character window of it.
    stored = "\n".join(_stored_strings(row))
    leaked = sorted(window for window in _windows(text) if window in stored)
    assert leaked == []
    assert "B-" not in stored


def test_the_first_refused_value_names_the_field_and_only_one_row_lands() -> None:
    ledger = FakeApprovalLedger()

    exc = _refuse(
        ledger,
        ALLOWED,
        surface="approve",
        values={"rationale": ALLOWED, "bulk_rationale": "Prioritize borrowers who are not Hispanic"},
        item_count=1,
    )

    assert exc.detail == "bulk_rationale failed the governed text policy"
    assert ledger.audit_types() == ["GOVERNED_TEXT_REFUSED"]
    assert ledger.audits[0]["metadata"]["surface"] == "approve"


def test_allowed_text_and_none_values_write_nothing() -> None:
    ledger = FakeApprovalLedger()

    gtr.refuse_ungoverned_text_audited(
        ledger,
        actor=ACTOR,
        surface="approve",
        values={"rationale": ALLOWED, "bulk_rationale": None},
        item_count=1,
    )

    assert ledger.statements == [] and ledger.audits == []


def test_a_failed_refusal_write_is_a_503_and_nothing_else_is_written() -> None:
    ledger = FakeApprovalLedger()
    ledger.down = True

    exc = _refuse(ledger, "Prioritize borrowers who are not Hispanic")

    assert exc.status_code == 503
    assert "governed text policy" not in str(exc.detail)
    assert ledger.audits == [] and ledger.writes() == 0


def test_an_unknown_or_unmarked_reason_reads_other() -> None:
    assert gtr.screen_for(AuditMetadataValueViolation("rationale", "a reason added later")) == "other"
    assert gtr.screen_for(AuditMetadataValueViolation("rationale", "")) == "other"


def test_every_mapped_reason_is_a_reason_the_screen_can_raise() -> None:
    """A renamed validator reason must fail here, not silently read 'other'."""

    source = (ROOT / "backend" / "services" / "audit_metadata_validation.py").read_text(encoding="utf-8")
    for reason in gtr._SCREEN_BY_REASON:
        assert f'"{reason}"' in source, reason


def test_the_vocabularies_are_closed_and_match_the_metadata_policy() -> None:
    assert set(gtr.GOVERNED_TEXT_SURFACES) == GOVERNED_TEXT_SURFACE_VALUES
    assert set(gtr.GOVERNED_TEXT_SCREENS) == GOVERNED_TEXT_SCREEN_VALUES
    assert set(gtr._SCREEN_BY_REASON.values()) <= GOVERNED_TEXT_SCREEN_VALUES
    assert "GOVERNED_TEXT_REFUSED" in SERVER_OWNED_AUDIT_EVENT_TYPES


@pytest.mark.parametrize(
    "payload",
    [
        {"surface": "draft", "screen": "pii", "item_count": 1},
        {"surface": "approve", "screen": "name", "item_count": 1},
        {"surface": "approve", "screen": "pii", "item_count": -1},
        {"surface": "approve", "screen": "pii", "item_count": True},
    ],
    ids=["open-surface", "open-screen", "negative-count", "boolean-count"],
)
def test_the_ledger_refuses_values_outside_the_closed_sets(payload: dict[str, Any]) -> None:
    with pytest.raises(AuditMetadataValueViolation):
        build_safe_audit_metadata(payload, action=gtr.GOVERNED_TEXT_REFUSED_ACTION)


def test_a_refusal_never_joins_the_borrower_decision_history() -> None:
    # W5c correction 51 / C8: the refusal is not a borrower decision.
    assert "GOVERNED_TEXT_REFUSED" not in DECISION_HISTORY_EVENT_TYPES


def test_the_one_log_line_carries_the_surface_and_screen_only(caplog: pytest.LogCaptureFixture) -> None:
    text = "Prioritize borrowers who are not Hispanic"
    with caplog.at_level(logging.INFO, logger="mip.governed_text_refusal"):
        _refuse(FakeApprovalLedger(), text, surface="revoke", item_count=1)

    records = [record for record in caplog.records if record.name == "mip.governed_text_refusal"]
    assert len(records) == 1
    extras = records[0].__dict__.get("mip_extras")
    assert extras == {"surface": "revoke", "screen": "fair_lending"}
    rendered = json.dumps(records[0].__dict__, default=str)
    assert not [window for window in _windows(text) if window in rendered]
