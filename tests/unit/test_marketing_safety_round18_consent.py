"""Round 18 identity-relation and transport-bound consent regressions."""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import (
    _assert_disclosure_backed_draft_body,
    _assert_final_draft_subject,
)
from backend.schemas.borrower_copy_names import contains_borrower_copy_contextual_name
from backend.schemas.borrower_cta_evidence import negative_borrower_cta_evidence
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_DISCLOSURE = MagicMock(
    body="Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
)

_PII_RELATIONS = (
    "Earmarked for quorla zembrix",
    "Designate quorla zembrix as the recipient",
    "Treat quorla zembrix as the recipient",
    "Put quorla zembrix on the list",
    "Place quorla zembrix in the queue",
    "The addressee is quorla zembrix",
    "The beneficiary is quorla zembrix",
    "Allocate this offer to quorla zembrix",
    "For quorla zembrix, mortgage options",
    "Mortgage review for quorla zembrix",
    "Give quorla zembrix priority",
    "Prioritize quorla zembrix",
    "Personalize this offer for quorla zembrix",
    "Reserve this offer for quorla zembrix",
    "Channel this notice to quorla zembrix",
    "Nominate quorla zembrix as the beneficiary",
    "Slot quorla zembrix into the cohort",
    "Award quorla zembrix priority",
    "Confer priority on lorven quastor",
    "Mark the recipient as lorven quastor",
    "Name lorven quastor the beneficiary",
    "Place the offer in front of lorven quastor",
    "Grant priority to lorven quastor",
    "Identify lorven quastor as the addressee",
    "Position the notice before lorven quastor",
    "Grant beneficiary status to lorven quastor",
)

_CONSENT_CONTRADICTIONS = (
    "A request for no further emails is on file. Email us to review options.",
    "The borrower made a request to hold all calls. Call us to review options.",
    "The borrower requested us to hold all calls. Call us to review options.",
    "The borrower told us to desist from messaging. Message us to review options.",
    "The borrower requested emails be put on hold. Email us to review options.",
    "The provider intercepts incoming replies. Reply YES to review options.",
    "The provider destroys incoming replies. Reply YES to review options.",
    (
        "The SMS provider destroys incoming texts, but staff reads email. "
        "Text us to review options."
    ),
    (
        "The email provider intercepts incoming emails, but SMS is monitored by staff. "
        "Email us to review options."
    ),
    (
        "The provider intercepts incoming replies, but SMS replies route to a staffed "
        "inbox. Reply YES to review options."
    ),
    (
        "An instruction that emails remain suspended is recorded. "
        "Email us to review options."
    ),
    (
        "The customer asked for messages to be discontinued. "
        "Message us to review options."
    ),
    "The provider quarantines incoming email. Email us to review options.",
    "The gateway erases every response. Reply YES to review options.",
)

_SAFE_SUBJECTS = (
    "For more information, mortgage options",
    "Mortgage review for current customers",
    "Offer earmarked for product review",
    "The addressee is servicing team",
    "Allocate this offer to product review",
    "Give branch review priority",
    "Priority on branch review",
    "The recipient is servicing team",
    "Place the offer in front of product review",
    "Beneficiary status to product review",
)

_SAFE_CONSENT_CONTROLS = (
    "A request for no further emails is on file. Replacement channel is phone; call us.",
    "The borrower requested us to hold all calls. Instead, email us to review options.",
    (
        "The SMS provider intercepts incoming texts then routes them to a staffed SMS "
        "inbox. Text us to review options."
    ),
    (
        "The email provider intercepts incoming emails then routes them to a staffed "
        "email inbox. Email us to review options."
    ),
    (
        "The provider intercepts incoming replies then routes them to a staffed reply "
        "queue. Reply YES to review options."
    ),
    (
        "The gateway quarantines incoming email then releases it to a staffed email "
        "inbox. Email us to review options."
    ),
)


def _variant(*, subject: str, body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject=subject,
        body=body,
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize("relation", _PII_RELATIONS)
def test_round18_identity_relations_fail_campaign_final_body_and_subject(
    relation: str,
) -> None:
    body = f"{relation}. Call us to review mortgage options."
    assert contains_borrower_copy_contextual_name(relation)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(subject="Mortgage options", body=body)
    with pytest.raises(ValidationError, match="human-name-shaped"):
        _variant(subject=relation, body="Call us to review mortgage options.")
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )
    with pytest.raises(HTTPException, match="human-name-shaped"):
        _assert_final_draft_subject(draft_subject=relation, channel="email")


@pytest.mark.parametrize("body", _CONSENT_CONTRADICTIONS)
def test_round18_consent_transport_evidence_fails_campaign_and_final(body: str) -> None:
    assert negative_borrower_cta_evidence(body)
    with pytest.raises(ValidationError, match="call to action"):
        _variant(subject="Mortgage options", body=body)
    with pytest.raises(HTTPException, match="call to action"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{body} {_DISCLOSURE.body}",
            disclosure=_DISCLOSURE,
            channel="email",
        )


@pytest.mark.parametrize("subject", _SAFE_SUBJECTS)
def test_round18_business_relation_subjects_are_not_misclassified(subject: str) -> None:
    assert not contains_borrower_copy_contextual_name(subject)
    assert _variant(
        subject=subject,
        body="Call us to review mortgage options.",
    ).subject == subject
    assert _assert_final_draft_subject(draft_subject=subject, channel="email") == subject


@pytest.mark.parametrize("body", _SAFE_CONSENT_CONTROLS)
def test_round18_replacements_and_same_transport_staffing_pass(body: str) -> None:
    assert _variant(subject="Mortgage options", body=body).body == body
    assert _assert_disclosure_backed_draft_body(
        draft_body=f"{body} {_DISCLOSURE.body}",
        disclosure=_DISCLOSURE,
        channel="email",
    ).startswith(body)
