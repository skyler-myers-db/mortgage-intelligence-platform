"""Round 13 consent-withdrawal and response-channel governance regressions."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas.portfolio_campaign import (
    CampaignRecommendationVariant,
    assert_borrower_campaign_copy,
)

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_ACTION_BOUND_CONTRADICTIONS = (
    "You asked us to halt all emails. Email us to review options.",
    "Asked us to halt all emails. Email us to review options.",
    "You told us to end phone calls. Call us to review options.",
    "Told us to end phone calls. Call us to review options.",
    "You requested that we suspend texts. Text us to review options.",
    "Requested we suspend texts. Text us to review options.",
    "Requested suspend texts. Text us to review options.",
    "You barred further emails. Email us to review options.",
    "Barred further emails. Email us to review options.",
    "You forbade further emails. Email us to review options.",
    "You prohibited further calls. Call us to review options.",
    "You blocked further texts. Text us to review options.",
    "You disallowed further messages. Message us to review options.",
    "Your DNC registration prohibits calls. Call us to review options.",
    "You asked us to leave you alone. Contact us to review options.",
    "Asked us to leave you alone. Contact us to review options.",
    "A STOP request was recorded. Text us to review options.",
    "An opt-out request was recorded. Contact us to review options.",
    "We recorded your STOP request. Text us to review options.",
    "We recorded your opt-out request. Contact us to review options.",
    "This mailbox is write-only. Reply YES to review options.",
    "This mailbox no longer exists. Reply YES to review options.",
    "This mailbox was shut down. Reply YES to review options.",
    "This mailbox is unstaffed. Reply YES to review options.",
    "Replies are silently dropped. Reply YES to review options.",
    "Replies bounce. Reply YES to review options.",
    "Reply routing is disabled. Reply YES to review options.",
    "Reply routing disabled. Reply YES to review options.",
    "Responses cannot reach a human. Reply YES to review options.",
    "No employee reads this mailbox. Reply YES to review options.",
    "Our provider suppresses inbound replies. Reply YES to review options.",
    "Our mail provider suppresses every inbound reply. Reply YES to review options.",
    "The carrier filters incoming replies. Reply YES to review options.",
)

_EXPLICIT_REPLACEMENT_AND_OPERATIONAL_CONTROLS = (
    "You asked us to halt all emails. Instead, call us to review options.",
    "You told us to end phone calls. Instead, email us to review options.",
    "You requested that we suspend texts. Instead, email us to review options.",
    "You barred further emails. Instead, call us to review options.",
    "You forbade further emails. Instead, text us to review options.",
    "You prohibited further calls. Instead, email us to review options.",
    "You blocked further texts. Instead, call us to review options.",
    "You disallowed further messages. Instead, call us to review options.",
    "Your DNC registration prohibits calls. Instead, email us to review options.",
    "A STOP request was recorded. Instead, call us to review options.",
    "This mailbox is write-only. Instead, call us to review options.",
    "This mailbox no longer exists. Instead, call us to review options.",
    "This mailbox was shut down. Instead, call us to review options.",
    "This mailbox is unstaffed. Instead, call us to review options.",
    "Replies are silently dropped. Instead, call us to review options.",
    "Reply routing is disabled. Instead, call us to review options.",
    "Responses cannot reach a human. Instead, call us to review options.",
    "No employee reads this mailbox. Instead, call us to review options.",
    "Our provider suppresses inbound replies. Instead, call us to review options.",
    "Our mail provider suppresses every inbound reply. Instead, call us to review options.",
    "The carrier filters incoming replies. Instead, call us to review options.",
    "This mailbox is staffed and accepts inbound replies. Reply YES to review options.",
    "Our provider delivers inbound replies to our team. Reply YES to review options.",
)


def _campaign_variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("contradictory_copy", _ACTION_BOUND_CONTRADICTIONS)
def test_round13_consent_and_dead_channel_copy_is_rejected_at_both_boundaries(
    contradictory_copy: str,
) -> None:
    with pytest.raises(ValidationError, match="call to action"):
        _campaign_variant(contradictory_copy)

    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{contradictory_copy} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("safe_copy", _EXPLICIT_REPLACEMENT_AND_OPERATIONAL_CONTROLS)
def test_round13_explicit_replacement_channels_remain_valid_at_both_boundaries(
    safe_copy: str,
) -> None:
    assert _campaign_variant(safe_copy).body == safe_copy
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{safe_copy} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(safe_copy)


def test_round13_reply_routing_and_provider_cases_are_enforced_by_cta_grammar() -> None:
    """Prove these cases do not depend on any protected-class text detector."""

    contradictory_copy = (
        "Reply routing is disabled. Reply YES to review options.",
        "Our mail provider suppresses every inbound reply. Reply YES to review options.",
    )
    for body in contradictory_copy:
        with pytest.raises(ValueError, match="call to action"):
            assert_borrower_campaign_copy(body, field_name="body", require_cta=True)

    replacement_copy = (
        "Reply routing is disabled. Instead, call us to review options.",
        "Our mail provider suppresses every inbound reply. Instead, call us to review options.",
    )
    for body in replacement_copy:
        assert assert_borrower_campaign_copy(body, field_name="body", require_cta=True) == body
