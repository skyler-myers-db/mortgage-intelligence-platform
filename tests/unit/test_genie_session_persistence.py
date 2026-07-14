from __future__ import annotations

from typing import Any

from backend.api.genie import _record_genie_session
from backend.services.genie_answers import GenieMessageResponse


class _Lakebase:
    def __init__(self) -> None:
        self.executes: list[tuple[str, dict[str, Any]]] = []

    def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.executes.append((sql, params))


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
