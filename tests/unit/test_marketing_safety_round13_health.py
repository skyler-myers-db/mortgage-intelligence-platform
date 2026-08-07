"""Round 13 regressions for protected-health marketing selection."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.outreach import _assert_disclosure_backed_draft_body
from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_UNSAFE_HEALTH_SELECTION_COPY = (
    "Homeowners undergoing surgery were selected for this offer. Contact us to review options.",
    "This offer is reserved for patients taking blood thinners. Contact us to review options.",
    "People using insulin qualify for this mortgage review. Contact us to review options.",
    "This offer is available only to homeowners being treated with insulin. Contact us to review options.",
    "We picked homeowners using insulin for this campaign. Contact us to review options.",
    "Recipients on metformin were picked for this mortgage review. Contact us to review options.",
    "People living with COPD were selected for this offer. Contact us to review options.",
    "COPD-affected homeowners were selected for this offer. Contact us to review options.",
    "Migraine patients were selected for this offer. Contact us to review options.",
    "Borrowers with a psychiatric diagnosis were picked for this offer. Contact us to review options.",
    "Applicants managing a neurological disorder qualify for this review. Contact us to review options.",
    "Homeowners with a respiratory condition are eligible for this offer. Contact us to review options.",
    "People in addiction recovery were selected for this campaign. Contact us to review options.",
    "Homeowners receiving treatment were picked for this offer. Contact us to review options.",
    "Borrowers taking medication were selected for this review. Contact us to review options.",
    "Patients taking Jardiance were selected for this offer. Contact us to review options.",
    "Homeowners taking atorvastatin were selected for this offer. Contact us to review options.",
    "Borrowers taking prescription medication called Jardiance were picked. Contact us to review options.",
)

_ROUND14_UNENUMERATED_HEALTH_SELECTION_COPY = (
    "Individuals with arthritis were selected for this offer. Contact us to review options.",
    "Residents who suffer from Crohn disease were picked for this offer. Contact us to review options.",
    "Households dealing with sickle cell disease qualify for this review. Contact us to review options.",
    "Individuals afflicted with cystic fibrosis were selected. Contact us to review options.",
    "Residents with hepatitis C were selected for this offer. Contact us to review options.",
    "Households dealing with obesity were selected for this review. Contact us to review options.",
    "Individuals who suffer from hemophilia were selected. Contact us to review options.",
    "Residents afflicted with sleep apnea were picked for this offer. Contact us to review options.",
    "Households dealing with chronic pain qualify for this review. Contact us to review options.",
    "Individuals with long COVID were selected for this offer. Contact us to review options.",
    "Residents who suffer from endometriosis were selected. Contact us to review options.",
    "Households afflicted with infertility were picked for this offer. Contact us to review options.",
    "Individuals dealing with fibromyalgia qualify for this review. Contact us to review options.",
    "Residents with autoimmune disease were selected for this offer. Contact us to review options.",
    "Households with blindness were selected. Contact us to review options.",
    "Individuals with hearing loss were picked for this offer. Contact us to review options.",
    # Eczema deliberately has no vocabulary entry or reviewed suffix. The
    # strong clinical relationship plus selection policy must still reject it.
    "Residents diagnosed with eczema were selected. Contact us to review options.",
    "Households with COPD were selected for this offer. Contact us to review options.",
)

_ROUND14_WEAK_RELATIONSHIP_SELECTION_COPY = (
    "Households with eczema were selected for this offer. Contact us to review options.",
    "Borrowers with GERD were selected for this offer. Contact us to review options.",
    "Residents with acne were selected for this offer. Contact us to review options.",
    "Individuals with narcolepsy were selected for this offer. Contact us to review options.",
    "Homeowners with gout were selected for this offer. Contact us to review options.",
    (
        "Households with high equity and eczema were selected for this offer. "
        "Contact us to review options."
    ),
    (
        "Health data is excluded from targeting and households with eczema were selected "
        "for this offer. Contact us to review options."
    ),
)

_ROUND15_STRUCTURAL_HEALTH_SELECTION_COPY = (
    "Households whose diagnosis is arthritis were selected for this offer. Contact us to review options.",
    "Residents whose diagnosis was xylophagia were selected for this offer. Contact us to review options.",
    "Individuals whose medical condition is eczema were selected. Contact us to review options.",
    "Borrowers having eczema were selected for this offer. Contact us to review options.",
    "Residents who report eczema were selected for this offer. Contact us to review options.",
    "Homeowners carrying an eczema diagnosis were selected. Contact us to review options.",
    "Households coping with eczema were selected for this offer. Contact us to review options.",
    (
        "Arthritis information is never used for campaign selection; residents having "
        "xylophagia were selected for this offer. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Eczema is required too. "
        "Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Eczema is an additional "
        "criterion. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. They must also have "
        "eczema. Contact us to review options."
    ),
    "Only include those diagnosed with eczema. Contact us to review options.",
)

_ROUND16_CONTEXTUAL_CRITERION_COPY = (
    (
        "Households with high equity were selected for this offer. This group must also "
        "have eczema. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Each must have zorb "
        "syndrome. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. All of them need eczema. "
        "Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Eczema mandatory. "
        "Contact us to review options."
    ),
    (
        "Select households with high equity for this offer, provided they have "
        "eczema. Contact us to review options."
    ),
    (
        "Select households with high equity for this offer, for whom eczema "
        "documented. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Also require zorb "
        "syndrome. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Eligibility also depends "
        "on zorb syndrome. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Zorb syndrome determines "
        "final eligibility. Contact us to review options."
    ),
    (
        "Households with high equity were selected for this offer. Filter them by zorb "
        "syndrome. Contact us to review options."
    ),
    "Eligibility depends on zorb syndrome. Contact us to review options.",
    "Zorb syndrome determines eligibility. Contact us to review options.",
    "Only select by zorb syndrome. Contact us to review options.",
    "Only filter by zorb syndrome. Contact us to review options.",
)

_SAFE_HEALTH_CONTEXT_COPY = (
    "Diabetes documentation is excluded from campaign selection. Contact us to review options.",
    "Documentation for people with diabetes is excluded from campaign eligibility. Contact us to review options.",
    "Cancer treatment documentation is retained only for compliance review. Contact us to review options.",
    "Health status is not used for campaign eligibility. Contact us to review options.",
    "Underwriting conditions are reviewed before mortgage approval. Contact us to review options.",
    "Fixed-rate loan terms are reviewed during underwriting. Contact us to review options.",
    "Reply routing is disabled. Instead, call us to review options.",
    "Households with high equity were selected for a reviewed HELOC campaign. Contact us to review options.",
    "Residents dealing with high mortgage rates were selected for a refinance review. Contact us to review options.",
    "Individuals who suffer from high mortgage payments were selected for servicing review. Contact us to review options.",
    "Individuals with high LTV were selected for portfolio review. Contact us to review options.",
    "Homeowners with current loan balances were selected for refinance review. Contact us to review options.",
    "Applicants with listed properties were selected for purchase review. Contact us to review options.",
    "Borrowers with strong rate spreads were selected for refinance review. Contact us to review options.",
    "Portfolio health is reviewed before campaign selection. Contact us to review options.",
    "The health of the loan portfolio is reviewed before campaign selection. Contact us to review options.",
    "Arthritis information is never used for campaign selection. Contact us to review options.",
    "Select households with high equity for this HELOC campaign. Contact us to review options.",
    "Households with fixed-rate mortgages were selected for this offer. Contact us to review options.",
    "Borrowers with existing liens were selected for retention options. Contact us to review options.",
    "Homeowners with HELOC intent were selected for home equity review. Contact us to review options.",
    "Residents with low LTV were selected for portfolio review. Contact us to review options.",
    (
        "Households with high equity were selected. High equity is required too. "
        "Contact us to review options."
    ),
    (
        "Borrowers with existing liens were selected. They must also have existing liens "
        "for retention options. Contact us to review options."
    ),
    (
        "Homeowners with HELOC intent were selected. HELOC intent is an additional criterion. "
        "Contact us to review options."
    ),
    "Only include those with low LTV. Contact us to review options.",
    (
        "Households with high equity were selected. This group must also have high equity. "
        "Contact us to review options."
    ),
    (
        "Households with high equity were selected. Each must have fixed-rate mortgages. "
        "Contact us to review options."
    ),
    (
        "Borrowers with existing liens were selected. All of them need existing liens for "
        "retention options. Contact us to review options."
    ),
    "Residents with low LTV were selected. Low LTV is mandatory. Contact us to review options.",
    (
        "Select homeowners with high equity, provided they have HELOC intent for home "
        "equity review. Contact us to review options."
    ),
    (
        "Select households with high equity, for whom high equity is documented. "
        "Contact us to review options."
    ),
    (
        "Borrowers with current loan balances were selected. Also require current loan "
        "balances. Contact us to review options."
    ),
    (
        "Borrowers with strong rate spreads were selected. Eligibility also depends on strong "
        "rate spreads. Contact us to review options."
    ),
    (
        "Residents with low LTV were selected. Low LTV determines final eligibility. "
        "Contact us to review options."
    ),
    (
        "Applicants with listed properties were selected. Filter them by listed properties. "
        "Contact us to review options."
    ),
    (
        "Households with high equity were selected. Eligibility does not depend on health "
        "status. Contact us to review options."
    ),
    (
        "Households with high equity were selected. Eczema is not mandatory for eligibility. "
        "Contact us to review options."
    ),
    (
        "Households with high equity were selected. Eczema is never required for eligibility. "
        "Contact us to review options."
    ),
    "Eligibility depends on high equity. Contact us to review options.",
    "High equity determines eligibility. Contact us to review options.",
    "Only select by high equity. Contact us to review options.",
    "Only filter by low LTV. Contact us to review options.",
    "Eligibility does not depend on eczema. Contact us to review options.",
    "Eczema no longer determines eligibility. Contact us to review options.",
)

_UNSAFE_COORDINATED_HEALTH_SELECTION_COPY = (
    (
        "Health data is excluded from targeting and homeowners with COPD were selected "
        "for this offer. Contact us to review options."
    ),
    (
        "Cancer documentation is retained only for compliance and patients taking insulin "
        "were selected. Contact us to review options."
    ),
    (
        "Health data is not used for selection while migraine patients receive this offer. "
        "Contact us to review options."
    ),
    (
        "Health data is excluded from targeting; homeowners with COPD were selected for this "
        "offer. Contact us to review options."
    ),
)


def _disclosure() -> MagicMock:
    return MagicMock(
        body=(
            "Summit Mortgage, NMLS #123456. Equal Housing Lender. "
            "Reply unsubscribe to opt out."
        )
    )


@pytest.mark.parametrize(
    "unsafe_copy",
    (
        *_UNSAFE_HEALTH_SELECTION_COPY,
        *_ROUND14_UNENUMERATED_HEALTH_SELECTION_COPY,
        *_ROUND14_WEAK_RELATIONSHIP_SELECTION_COPY,
        *_ROUND15_STRUCTURAL_HEALTH_SELECTION_COPY,
        *_ROUND16_CONTEXTUAL_CRITERION_COPY,
    ),
)
def test_health_selection_is_rejected_at_recommendation_and_final_approval_boundaries(
    unsafe_copy: str,
) -> None:
    assert contains_protected_class_marketing_text(unsafe_copy) is True

    with pytest.raises(ValidationError, match="protected-class"):
        CampaignRecommendationVariant(
            variant_name="Guidance-led",
            subject="Mortgage options review",
            body=unsafe_copy,
            hypothesis="Guidance framing may support a review request.",
        )

    disclosure = _disclosure()
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{unsafe_copy} {disclosure.body}",
            disclosure=disclosure,
            channel="email",
        )


@pytest.mark.parametrize("safe_copy", _SAFE_HEALTH_CONTEXT_COPY)
def test_health_exclusions_and_ordinary_mortgage_prose_remain_available(
    safe_copy: str,
) -> None:
    assert contains_protected_class_marketing_text(safe_copy) is False
    CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options review",
        body=safe_copy,
        hypothesis="Guidance framing may support a review request.",
    )

    disclosure = _disclosure()
    body = f"{safe_copy} {disclosure.body}"
    assert (
        _assert_disclosure_backed_draft_body(
            draft_body=body,
            disclosure=disclosure,
            channel="email",
        )
        == body
    )


@pytest.mark.parametrize(
    "unsafe_copy",
    (
        "Health data is excluded from targeting, but homeowners with COPD were selected "
        "for this offer. Contact us to review options.",
        *_UNSAFE_COORDINATED_HEALTH_SELECTION_COPY,
    ),
)
def test_health_exclusion_cannot_mask_later_coordinated_selection(unsafe_copy: str) -> None:
    assert contains_protected_class_marketing_text(unsafe_copy) is True

    with pytest.raises(ValidationError, match="protected-class"):
        CampaignRecommendationVariant(
            variant_name="Benefit-led",
            subject="Mortgage options review",
            body=unsafe_copy,
            hypothesis="A reviewed invitation may support a response.",
        )

    disclosure = _disclosure()
    with pytest.raises(HTTPException, match="protected-class"):
        _assert_disclosure_backed_draft_body(
            draft_body=f"{unsafe_copy} {disclosure.body}",
            disclosure=disclosure,
            channel="email",
        )
