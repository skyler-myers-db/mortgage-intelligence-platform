"""Round 17 action-bound consent, response-channel, and name regressions."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas._validators import contains_human_name_shape
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.borrower_cta_evidence import negative_borrower_cta_evidence
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_CONSENT_CONTRADICTIONS = (
    "A request to pause calls is on file. Call us to review options.",
    "A request to refrain from emails is recorded. Email us to review options.",
    "Incoming emails terminate before staff sees them. Email us to review options.",
    "Replies are dumped before staff sees them. Reply YES to review options.",
    "Replies are swallowed before staff sees them. Reply YES to review options.",
    "Replies are held indefinitely. Reply YES to review options.",
    "STOP was received from the borrower. Text us to review options.",
    "They told us to stop. Text us to review options.",
    "It suppresses every inbound reply. Reply YES to review options.",
    "Replies go to nowhere. Reply YES to review options.",
    (
        "The provider drops all inbound replies, but phone calls route to a staffed team. "
        "Reply YES to review options."
    ),
    "The borrower instructed us to refrain from texting. Text us to review options.",
    "Inbound replies are consigned to nowhere. Reply YES to review options.",
    "Inbound replies go unseen by staff. Reply YES to review options.",
    "Every inbound response is quarantined indefinitely. Reply YES to review options.",
    (
        "The provider blocks every reply; email is monitored by staff. "
        "Reply YES to review options."
    ),
    "No one monitors incoming texts. Text us to review options.",
)

_CONTEXTUAL_NAME_COPY = (
    "Please call us; assign this offer to talia quist.",
    "Please call us; make quincy zabinski the focus.",
    "Please call us; keep quincy zabinski in the campaign.",
    "Please call us; this offer concerns quincy zabinski.",
    "Please call us about the home owned by quincy zabinski.",
    "Please call us about the mortgage belonging to quincy zabinski.",
    "Please call us about quincy zabinski's home.",
    "Please call us; the applicant is quincy zabinski.",
    "Please call us; the account holder is quincy zabinski.",
    "Please call us; send this to quincy zabinski.",
    "Please call us; this offer is intended for quincy zabinski.",
    "Please call us; route this offer to velnora quibble.",
    "Please call us; deliver this message to velnora quibble.",
    "Please call us; velnora quibble is the recipient.",
    "Please call us; focus on velnora quibble.",
    "Please call us; this is for velnora quibble.",
    "Please call us; review velnora quibble mortgage.",
)

_SAFE_CONTROLS = (
    "A request to pause calls is on file. Instead, email us to review options.",
    "A request to refrain from emails is recorded. Instead, call us to review options.",
    "Replies are held indefinitely. Replacement channel is phone; call us.",
    (
        "Our provider filters inbound replies for malware then delivers them to a "
        "staffed team. Reply YES to review options."
    ),
    (
        "Our provider filters inbound replies for malware, and staff reads the replies. "
        "Reply YES to review options."
    ),
    "Please call us; assign this offer to the campaign queue.",
    "Please call us; make campaign queue the focus.",
    "Please call us; keep branch review in the queue.",
    "Please call us; this offer is intended for human review.",
    "Please call us; the applicant is fully eligible.",
    "Please call us; the account holder is in good standing.",
    "Please call us; send this to the servicing team.",
    "Please call us; this offer concerns customer experience.",
    "Please call us; keep customer experience the focus.",
    "Please call us; focus on mortgage growth.",
    "Please call us; route this offer to product review.",
    "Please call us; deliver this message to the compliance team.",
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("body", _CONSENT_CONTRADICTIONS)
def test_round17_consent_evidence_fails_canonical_campaign_and_final_boundaries(
    body: str,
) -> None:
    assert negative_borrower_cta_evidence(body)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("body", _CONTEXTUAL_NAME_COPY)
def test_round17_contextual_names_fail_campaign_and_final_boundaries(body: str) -> None:
    assert contains_borrower_copy_contextual_name(body)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body)
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("body", _SAFE_CONTROLS)
def test_round17_valid_replacements_delivery_and_business_phrases_pass(body: str) -> None:
    assert not contains_borrower_copy_contextual_name(body)
    assert _variant(body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)


def test_round17_channel_language_is_not_misclassified_as_a_human_name() -> None:
    text = "No one monitors incoming texts"
    assert not contains_human_name_shape(text)
    assert not contains_borrower_copy_contextual_name(text)
