from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.genie import _record_genie_session
from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_message_policy import GenieMessageRequest


class _Lakebase:
    def __init__(self) -> None:
        self.executes: list[tuple[str, dict[str, Any]]] = []

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.executes.append((sql, params))


class _AtomicLakebase:
    _supports_atomic_transactions = True

    def __init__(self, *, fail_second: bool = False) -> None:
        self.committed: list[tuple[str, dict[str, Any]]] = []
        self.fail_second = fail_second

    @contextmanager
    def transaction(self):
        staged: list[tuple[str, dict[str, Any]]] = []
        owner = self

        class _Connection:
            def execute(self, sql: str, params: dict[str, Any]) -> None:
                if owner.fail_second and len(staged) == 1:
                    raise RuntimeError("message insert failed")
                staged.append((sql, params))

        yield _Connection()
        self.committed.extend(staged)


def test_verified_sql_overlay_persists_native_genie_feedback_ownership() -> None:
    lakebase = _Lakebase()
    response = GenieMessageResponse(
        conversation_id="conv-live",
        message_id="msg-live",
        question="How many borrowers are in the money?",
        answer="117,404",
        source="trusted_sql",
        genie_status="COMPLETED",
        trusted_assets=["mip.gold.borrower_360"],
    )

    _record_genie_session(  # type: ignore[arg-type]
        lakebase,
        actor="analyst@example.com",
        response=response,
    )

    assert len(lakebase.executes) == 2
    assert all(params["source"] == "genie" for _, params in lakebase.executes)


def test_deterministic_sql_answer_does_not_gain_native_feedback_ownership() -> None:
    lakebase = _Lakebase()
    response = GenieMessageResponse(
        conversation_id="conv-fallback",
        message_id="msg-fallback",
        question="How many borrowers are in the money?",
        answer="117,404",
        source="trusted_sql",
        genie_status=None,
        trusted_assets=["mip.gold.borrower_360"],
    )

    _record_genie_session(  # type: ignore[arg-type]
        lakebase,
        actor="analyst@example.com",
        response=response,
    )

    assert len(lakebase.executes) == 2
    assert all(params["source"] == "trusted_sql" for _, params in lakebase.executes)


def test_session_and_message_provenance_commit_atomically() -> None:
    lakebase = _AtomicLakebase()
    response = GenieMessageResponse(
        conversation_id="conv-live",
        message_id="msg-live",
        question="How many borrowers are in the money?",
        answer="117,404",
        source="genie",
        genie_status="COMPLETED",
        trusted_assets=["mip.gold.borrower_360"],
    )

    _record_genie_session(lakebase, actor="analyst@example.com", response=response)  # type: ignore[arg-type]

    assert len(lakebase.committed) == 2


def test_session_and_message_provenance_roll_back_together() -> None:
    lakebase = _AtomicLakebase(fail_second=True)
    response = GenieMessageResponse(
        conversation_id="conv-live",
        message_id="msg-live",
        question="How many borrowers are in the money?",
        answer="117,404",
        source="genie",
        genie_status="COMPLETED",
        trusted_assets=["mip.gold.borrower_360"],
    )

    with pytest.raises(HTTPException) as exc_info:
        _record_genie_session(lakebase, actor="analyst@example.com", response=response)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 503
    assert lakebase.committed == []


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "x" * 4_001},
        {"question": "valid", "conversation_id": "c" * 257},
    ],
)
def test_genie_request_bounds_user_controlled_text(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        GenieMessageRequest.model_validate(payload)
