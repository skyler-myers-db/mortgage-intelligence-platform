"""Round 16 consent structure and lowercase borrower-name regressions."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_CONSENT_CONTRADICTIONS = (
    "The borrower opted out. Contact us to review options.",
    "The borrower no longer wants emails. Email us to review options.",
    "The borrower said no more texts. Text us to review options.",
    "They texted STOP. Text us to review options.",
    "A STOP response was received from the borrower. Text us to review options.",
    "No human will read replies. Reply YES to review options.",
    "Every inbound reply is filtered by the provider. Reply YES to review options.",
)

_SAFE_CONSENT_CONTROLS = (
    "The borrower no longer wants emails. Replacement channel is phone; call us.",
    "The borrower no longer wants emails. Instead, call us to review options.",
    "The borrower no longer wants emails. But call us instead.",
    "Call us instead; the borrower no longer wants emails.",
    "The borrower said no more texts. Replacement channel is email; email us.",
    "Our provider filters inbound replies for malware then delivers them to a staffed team. Reply YES to review options.",
    "Our provider filters inbound replies for malware, and staff reads the replies. Reply YES to review options.",
)

_PII_COPY = (
    "Prepared for xavier patel. Call us to review options.",
    "This offer belongs to xavier patel. Call us to review options.",
)

_SAFE_NAME_CONTROLS = (
    "Prepared for human review. Call us to review options.",
    "Prepared for branch review. Call us to review options.",
    "This offer belongs to the refinance portfolio. Call us to review options.",
    "This offer belongs to the campaign queue. Call us to review options.",
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("body", _CONSENT_CONTRADICTIONS)
def test_round16_consent_contradictions_fail_campaign_and_final_boundaries(
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


@pytest.mark.parametrize("body", _SAFE_CONSENT_CONTROLS)
def test_round16_replacements_and_staffed_delivery_pass_campaign_and_final(
    body: str,
) -> None:
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)

@pytest.mark.parametrize("body", _PII_COPY)
def test_round16_lowercase_names_fail_campaign_and_final_boundaries(body: str) -> None:
    assert contains_borrower_copy_contextual_name(body)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body)
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("body", _SAFE_NAME_CONTROLS)
def test_round16_contextual_name_detector_preserves_review_and_product_copy(
    body: str,
) -> None:
    assert not contains_borrower_copy_contextual_name(body)
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)
