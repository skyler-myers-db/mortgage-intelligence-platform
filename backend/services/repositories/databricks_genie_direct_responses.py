"""Response builders shared by the direct-answer dispatcher and its branch modules."""

from __future__ import annotations

from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.repositories.databricks_genie_trust import _genie_question_hash

_SEGMENT_DISPLAY_LABELS = {
    "itm": "Prime Refi Candidates",
    "equity": "Home Equity Candidate",
    "investor": "Investor / Multi-Property",
    "retention": "Retention Risk",
    "listed": "Listed for Sale",
    "permit": "HELOC Intent",
}


def _segment_display_label(value: object) -> str:
    raw = str(value or "").strip()
    return _SEGMENT_DISPLAY_LABELS.get(raw, raw or "all segments")


def _data_gap_response(
    *,
    question: str,
    answer: str,
    trusted_assets: list[str],
    known_data_gaps: list[str],
) -> GenieMessageResponse:
    question_hash = _genie_question_hash(question)
    message_id = f"data-gap-{question_hash}"
    return GenieMessageResponse(
        conversation_id="",
        message_id=message_id,
        elapsed_ms=0,
        question_hash=question_hash,
        question=question,
        answer=answer,
        source="data_gap",
        trusted_assets=trusted_assets,
        row_count=0,
        proof=GenieProof(
            source_assets=trusted_assets,
            row_count=0,
            trusted=False,
            known_data_gaps=known_data_gaps,
            conversation_id=None,
            message_id=message_id,
        ),
        table_rows=[],
    )
