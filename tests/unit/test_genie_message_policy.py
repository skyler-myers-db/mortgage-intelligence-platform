"""Direct output-policy tests for every user-visible Genie response."""

import pytest

from backend.services.genie_answers import GenieMessageResponse
from backend.services.genie_message_policy import genie_response_has_unsafe_visible_text


def _response(**overrides: object) -> GenieMessageResponse:
    payload: dict[str, object] = {
        "conversation_id": "conv-policy",
        "question": "Show the ranked aggregate.",
        "answer": "The governed aggregate is ready.",
        "source": "genie",
        "trusted_assets": ["mip.gold.borrower_360"],
    }
    payload.update(overrides)
    return GenieMessageResponse.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("answer", "| Marcus | Chen | qualified borrowers |"),
        ("answer", "Contact **Marcus Chen** about the result."),
        ("follow_up_questions", ["Email borrower@example.com about this cohort."]),
        ("follow_up_questions", ["Target Muslim homeowners in Illinois."]),
        ("table_rows", [{"borrower_name": "Marcus Chen"}]),
        ("table_rows", [{"loan_officer": "person@example.com"}]),
    ],
)
def test_response_policy_rejects_unsafe_visible_text_shapes(
    field: str,
    value: object,
) -> None:
    assert genie_response_has_unsafe_visible_text(_response(**{field: value})) is True


def test_response_policy_keeps_safe_mortgage_language() -> None:
    response = _response(
        answer="New York has the largest reviewed refinance cohort.",
        follow_up_questions=["How does loan age vary across New York?"],
    )
    assert genie_response_has_unsafe_visible_text(response) is False


def test_response_policy_allows_only_explicit_governed_staff_labels() -> None:
    response = _response(table_rows=[{"loan_officer": "John Smith"}])
    assert genie_response_has_unsafe_visible_text(
        response,
        allowed_literals=["John Smith"],
    ) is False
    assert genie_response_has_unsafe_visible_text(response) is True
