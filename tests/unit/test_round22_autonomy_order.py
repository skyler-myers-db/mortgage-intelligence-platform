"""Round 22 order-invariant borrower-autonomy governance proof."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out."
)

_OPTIONAL_CTA_AND_AUTONOMY = (
    (
        "There is no obligation to respond.",
        "If you would like to review options, reply YES.",
    ),
    (
        "No response is required.",
        "Reply YES if you would like to review mortgage options.",
    ),
    (
        "No action is needed.",
        "You may contact us if you want to review mortgage options.",
    ),
    (
        "You are not required to call us.",
        "You can call us if you choose to review mortgage options.",
    ),
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize(
    "body",
    tuple(
        copy
        for autonomy, optional_cta in _OPTIONAL_CTA_AND_AUTONOMY
        for copy in (f"{autonomy} {optional_cta}", f"{optional_cta} {autonomy}")
    ),
)
def test_optional_cta_and_autonomy_are_valid_in_both_orders(body: str) -> None:
    assert _variant(body).body == body
    approved = _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    )
    assert body in approved


@pytest.mark.parametrize(
    "body",
    (
        "No response is required unless you reply YES.",
        "Please review the notice. No response is needed.",
        "No response is needed. Please review the notice.",
    ),
)
def test_conditional_or_informational_copy_does_not_prove_a_cta(body: str) -> None:
    with pytest.raises(ValidationError, match="call to action"):
        _variant(body)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
