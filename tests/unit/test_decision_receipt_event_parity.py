"""The admin explorer offers a Decision receipt exactly for decision rows.

``frontend/src/components/mortgage/DecisionReceipt.copy.ts`` lists the event
types and actions whose expanded explorer row reads a receipt back
(``isDecisionReceiptEvent``). ``GET /api/audit/receipt/{id}`` answers only rows
``audit_store_receipt.decision_outcome_for`` maps to a decision, so the two
lists must be the backend's ``_DECISION_BY_EVENT_TYPE`` / ``_DECISION_BY_ACTION``
keys exactly: a missing entry hides a real receipt, an extra one fires a read
the endpoint can only refuse. The TS side also normalizes the way the backend
does (event type upper-cased, action lower-cased, both trimmed).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import audit_store_receipt

COPY_TS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "mortgage"
    / "DecisionReceipt.copy.ts"
)


def _ts_list(source: str, name: str) -> list[str]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, f"{name} is not declared in DecisionReceipt.copy.ts"
    return re.findall(r"'([^']*)'", match.group(1))


def test_event_types_match_the_receipt_endpoint() -> None:
    source = COPY_TS.read_text(encoding="utf-8")
    event_types = _ts_list(source, "DECISION_RECEIPT_EVENT_TYPES")
    assert len(event_types) == len(set(event_types))
    assert set(event_types) == set(audit_store_receipt._DECISION_BY_EVENT_TYPE)


def test_actions_match_the_receipt_endpoint() -> None:
    source = COPY_TS.read_text(encoding="utf-8")
    actions = _ts_list(source, "DECISION_RECEIPT_ACTIONS")
    assert len(actions) == len(set(actions))
    assert set(actions) == set(audit_store_receipt._DECISION_BY_ACTION)


def test_the_ts_check_normalizes_like_decision_outcome_for() -> None:
    source = COPY_TS.read_text(encoding="utf-8")
    body = re.search(r"export function isDecisionReceiptEvent\b.*?\n}\n", source, re.DOTALL)
    assert body, "isDecisionReceiptEvent is not declared in DecisionReceipt.copy.ts"
    text = body.group(0)
    assert re.search(r"event_type[^\n]*\.trim\(\)\.toUpperCase\(\)", text)
    assert re.search(r"action[^\n]*\.trim\(\)\.toLowerCase\(\)", text)
