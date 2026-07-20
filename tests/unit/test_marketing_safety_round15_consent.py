"""Round 15 structural consent and response-sink regressions."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_STRUCTURAL_CONTRADICTIONS = (
    "A standing email opt-out remains active. Email us to review options.",
    "The borrower sent STOP yesterday. Text us to review options.",
    "Nobody checks this mailbox. Reply YES to review options.",
    "Replies vanish before anyone reads. Reply YES to review options.",
    "Replies are routed into a void. Reply YES to review options.",
    "The email opt-out, which is on file. Email us to review options.",
    "This inbox accepts replies, but no employee reads it. Reply YES to review options.",
    "This inbox accepts replies, but none of the employees reads it. Reply YES to review options.",
    "The borrower, who said STOP, and text them.",
    "The recipient opted out of email. Email that recipient.",
    "Our provider, which suppresses inbound reply, invites reply YES.",
    "The borrower sent STOP yesterday. Send her a text.",
    "The borrower sent STOP yesterday. Send those borrowers a text.",
    "The recipient opted out of email. That recipient should be emailed.",
    "The recipient opted out of email. That recipient should receive an email.",
    "The recipient opted out of calls. That recipient should receive a call.",
)

_SAFE_REPLACEMENTS_AND_OPERATIONS = (
    "A standing email opt-out remains active. Instead, call us to review options.",
    "A standing email opt-out remains active. But call us instead.",
    "The email opt-out, which is on file. But call us instead.",
    "Call us instead; a standing email opt-out remains active.",
    "The borrower sent STOP yesterday. But call us instead.",
    "Nobody checks this mailbox. But call us instead.",
    "Replies are routed into a void. Instead, call us to review options.",
    "The recipient opted out of email. But call us instead.",
    "The recipient opted out of calls. But email us instead.",
    "Our compliance team audits email opt-out records. Call us to review options.",
    "Employees check this mailbox daily. Reply YES to review options.",
    "Replies route to a staffed team before an employee reads them. Reply YES to review options.",
    "Our provider, which delivers inbound replies, is operational. Reply YES to review options.",
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("body", _STRUCTURAL_CONTRADICTIONS)
def test_round15_structural_contradictions_fail_campaign_and_final_boundaries(
    body: str,
) -> None:
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("body", _SAFE_REPLACEMENTS_AND_OPERATIONS)
def test_round15_safe_replacements_and_operations_pass_campaign_and_final_boundaries(
    body: str,
) -> None:
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)


def test_round15_campaign_and_final_boundaries_still_reject_human_name_pii() -> None:
    body = "A standing email opt-out remains active. Email Maria Garcia."
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body)
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
