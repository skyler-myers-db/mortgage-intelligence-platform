"""Structural proof for audience-assignment admission morphology."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas._validators_protected_class import contains_protected_class_marketing_text
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.marketing_audience_admission import audience_admission_criterion
from backend.schemas.portfolio_campaign import CampaignRecommendationVariant

_ADMISSION_VERBS = (
    ("Assign", "assigned"),
    ("Route", "routed"),
    ("Direct", "directed"),
    ("Insert", "inserted"),
    ("Allocate", "allocated"),
    ("Dispatch", "dispatched"),
    # These are deliberately absent from the legacy closed verb vocabulary;
    # the relationship grammar, not an enumerated synonym list, must govern.
    ("Schedule", "scheduled"),
    ("Channel", "channeled"),
    ("Bundle", "bundled"),
)


def _variant(body: str) -> CampaignRecommendationVariant:
    return CampaignRecommendationVariant(
        variant_name="Guidance-led",
        subject="Mortgage options",
        body=f"{body} Contact us to review mortgage options.",
        hypothesis="A reviewed invitation may support a response.",
    )


@pytest.mark.parametrize(("active", "participle"), _ADMISSION_VERBS)
def test_active_and_passive_admission_families_extract_the_complete_criterion(
    active: str,
    participle: str,
) -> None:
    assert (
        audience_admission_criterion(
            f"{active} borrowers to the campaign when scleroderma is present"
        )
        == "scleroderma is present"
    )
    assert (
        audience_admission_criterion(
            f"Borrowers have been {participle} to the campaign based on current LTV"
        )
        == "current LTV"
    )
    assert (
        audience_admission_criterion(
            f"Candidates with scleroderma are {participle} to the campaign"
        )
        == "scleroderma"
    )


@pytest.mark.parametrize(("active", "participle"), _ADMISSION_VERBS)
@pytest.mark.parametrize("voice", ("active", "passive", "bound"))
def test_unreviewed_admission_families_reject_every_public_schema_path(
    active: str,
    participle: str,
    voice: str,
) -> None:
    copies = {
        "active": f"{active} borrowers to the campaign when scleroderma is present.",
        "passive": (f"Borrowers are {participle} to the campaign when scleroderma is present."),
        "bound": f"Candidates with scleroderma are {participle} to the campaign.",
    }
    copy = copies[voice]

    assert contains_protected_class_marketing_text(copy)
    with pytest.raises(ValidationError, match="protected-class"):
        _variant(copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        GrowthAgentPromptRunRequest(prompt=copy)
    with pytest.raises(ValidationError, match="reviewed, non-PII"):
        ComposePlanRequest(objective=copy)


@pytest.mark.parametrize(
    "copy",
    (
        "Assign borrowers to the campaign when high equity is present.",
        "Candidates with current mortgage rates are routed to the campaign.",
        "Applicants have been allocated to the cohort when current LTV is documented.",
        "Review priority distribution and assign the next owner.",
    ),
)
def test_closed_mortgage_admission_controls_remain_safe(copy: str) -> None:
    assert not contains_protected_class_marketing_text(copy)
    assert _variant(copy).body.startswith(copy)
    assert GrowthAgentPromptRunRequest(prompt=copy).prompt == copy
    assert ComposePlanRequest(objective=copy).objective == copy


@pytest.mark.parametrize(("active", "_participle"), _ADMISSION_VERBS)
def test_each_active_family_preserves_a_closed_pre_destination_criterion(
    active: str,
    _participle: str,
) -> None:
    copy = f"{active} borrowers with current LTV into the campaign."
    assert not contains_protected_class_marketing_text(copy)
    assert _variant(copy).body.startswith(copy)


def test_admission_state_carries_into_a_later_criterion() -> None:
    unsafe = (
        "Assign borrowers to the campaign when high equity is present. "
        "They must also have scleroderma."
    )
    assert contains_protected_class_marketing_text(unsafe)


def test_read_only_population_movement_question_remains_safe() -> None:
    question = "How has the in-the-money population moved recently?"
    assert not contains_protected_class_marketing_text(question)


def test_permit_source_gap_query_is_not_mislabeled_as_protected_targeting() -> None:
    question = "Show HELOC candidates with recent permits and strong equity."
    assert not contains_protected_class_marketing_text(question)
