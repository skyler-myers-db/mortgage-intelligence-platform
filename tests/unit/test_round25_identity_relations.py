"""Round 25 structural correspondence-identity relation regressions."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
)
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant
from backend.services.audit_store import (
    AuditMetadataValueViolation,
    build_safe_audit_metadata,
)
from tests.unit.growth_refusal_contract import GROWTH_REFUSAL_MESSAGE_RE

_DISCLOSURE = SimpleNamespace(
    body=("Summit Mortgage, NMLS #123456. Equal Housing Lender. " "Reply unsubscribe to opt out.")
)

_IDENTITY_RELATIONS = (
    "CC is siobhan q quigley",
    "Courtesy copy is siobhan q quigley",
    "The carbon copy is for xochitl q. quintero",
    "Prepared on behalf of siobhan q quigley",
    "This notice was prepared in the name of xochitl q. quintero",
    "Correspond with siobhan q quigley about this mortgage review",
    "Our team will correspond with siobhan q quigley about this mortgage review",
    "Please communicate with xochitl q. quintero regarding this email",
    "Payable to siobhan q quigley",
    "Payable to: siobhan q. quigley",
    "This notice is deliverable to xochitl q. quintero",
    "Deliverable to - siobhan q quigley",
)

_SAFE_BUSINESS_VALUES = (
    "Mail to: our compliance team",
    "Recipient: the compliance team",
    "For delivery to: our regional branch office",
    "Subject: our mortgage payment update",
    "Please copy the underwriting manager",
    # Safe values also compose through each newly governed relationship.
    "Courtesy copy is our compliance team",
    "Prepared on behalf of the compliance team",
    "Correspond with our compliance team about this mortgage review",
    "Payable to our accounting department",
    "This notice is deliverable to the regional branch office",
)


def _variant(*, body: str, subject: str = "Mortgage options") -> object:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("copy", _IDENTITY_RELATIONS)
def test_identity_relations_reject_every_shared_copy_boundary(copy: str) -> None:
    """Relationship-shaped identities must not survive any approval boundary."""

    objective = f"{copy}. Review governed mortgage opportunities."
    body = f"{copy}. Reply YES to review mortgage options. {_DISCLOSURE.body}"

    assert contains_borrower_copy_contextual_name(copy)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(body=body)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        GrowthAgentPromptRunRequest(prompt=objective)
    with pytest.raises(ValidationError, match=GROWTH_REFUSAL_MESSAGE_RE):
        ComposePlanRequest(objective=objective)
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_final_draft_subject(draft_subject=copy, channel="email")
    with pytest.raises(AuditMetadataValueViolation, match="human-name-shaped"):
        build_safe_audit_metadata({"draft_subject": copy}, action="outreach.approve")


@pytest.mark.parametrize("copy", _SAFE_BUSINESS_VALUES)
def test_reviewed_business_values_survive_shared_copy_boundaries(copy: str) -> None:
    """Closed organization/content grammar preserves ordinary business copy."""

    objective = f"{copy}. Review governed mortgage opportunities."
    body = f"{copy}. Reply YES to review mortgage options. {_DISCLOSURE.body}"

    assert not contains_borrower_copy_contextual_name(copy)
    assert _variant(body=body)
    assert GrowthAgentPromptRunRequest(prompt=objective).prompt == objective
    assert ComposePlanRequest(objective=objective).objective == objective
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=_DISCLOSURE,
            channel="email",
        )
        == body
    )
    assert _assert_final_draft_subject(draft_subject=copy, channel="email") == copy
    assert (
        build_safe_audit_metadata({"draft_subject": copy}, action="outreach.approve")[
            "draft_subject"
        ]
        == copy
    )


def test_bare_copy_state_is_not_an_identity_relation() -> None:
    copy = "CC is enabled for the servicing team."

    assert not contains_borrower_copy_contextual_name(copy)
    assert _variant(body=f"{copy} Reply YES to review mortgage options.")
