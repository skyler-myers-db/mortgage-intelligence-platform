"""Round 14 action-bound consent and dead-response-channel regressions."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_ROUND14_CONTRADICTIONS = (
    "Email opt-out on file. Email us to review options.",
    "Phone opt-out on file. Call us to review options.",
    "SMS opt-out on file. Text us to review options.",
    "Documented opt-out. Contact us to review options.",
    "Zero further contact. Contact us to review options.",
    "You said STOP. Text us to review options.",
    "Your unsubscribe request was logged. Email us to review options.",
    "Incoming replies are deleted on arrival. Reply YES to review options.",
    "This mailbox accepts messages, but nobody ever looks at it. Reply YES to review options.",
    "This inbox is never opened. Reply YES to review options.",
    "Replies are archived unread. Reply YES to review options.",
    "Incoming messages are automatically quarantined. Reply YES to review options.",
    "Our carrier blackholes inbound replies. Reply YES to review options.",
)

_ROUND14_SAFE_CONTROLS = (
    "Email opt-out on file. Instead, call us to review options.",
    "Phone opt-out on file. Instead, email us to review options.",
    "SMS opt-out on file. Instead, call us to review options.",
    "You said STOP. Instead, call us to review options.",
    "Your unsubscribe request was logged. Instead, call us to review options.",
    "Incoming replies are deleted on arrival. Instead, call us to review options.",
    "This mailbox accepts messages, but nobody ever looks at it. Instead, call us to review options.",
    "This inbox is never opened. Instead, call us to review options.",
    "Replies are archived unread. Instead, call us to review options.",
    "Incoming messages are automatically quarantined. Instead, call us to review options.",
    "Our carrier blackholes inbound replies. Instead, call us to review options.",
    "The mailbox accepts messages and an employee reads it daily. Reply YES to review options.",
    "Replies are archived after an employee reviews them. Reply YES to review options.",
    "Our carrier routes inbound replies to a staffed team. Reply YES to review options.",
    "Our compliance system logs unsubscribe requests for audit. Email us to review options.",
    "Incoming messages are quarantined for malware, then released to a staffed team. Reply YES to review options.",
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("contradictory_copy", _ROUND14_CONTRADICTIONS)
def test_round14_contradictions_fail_campaign_and_final_approval_boundaries(
    contradictory_copy: str,
) -> None:
    with pytest.raises(ValidationError, match="call to action"):
        _variant(contradictory_copy)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{contradictory_copy} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("safe_copy", _ROUND14_SAFE_CONTROLS)
def test_round14_replacements_and_operational_controls_pass_both_boundaries(
    safe_copy: str,
) -> None:
    assert _variant(safe_copy).body == safe_copy
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{safe_copy} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(safe_copy)
