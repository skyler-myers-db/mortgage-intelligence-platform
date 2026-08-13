"""The two-letter ``MS`` is Mississippi as well as multiple sclerosis.

A direct health-bank entry matched the bare token ``MS``, so every governed
per-state rollup that named Mississippi came back as a fair-lending finding --
"Borrowers in MS.", "State rollup: 70 MS borrowers and 20 LA borrowers." --
and geography drill-down is a hero surface per CLAUDE.md. Mississippi was the
only USPS code affected: the other 50 were already clean, which is what makes
this a term-shaped defect rather than a geography-shaped one.

The repair narrows the TERM instead of exempting the PLACE. Recognizing a place
is not authority to strip it, and a place exemption would have had to hold on
each surface separately -- the collision reached the co-pilot, the plan
objective, the Genie prompt/planner screens, the visible prose and cell scans,
AND the governed place dimension's admission gate, which refused ``MS`` exactly
the way it refuses ``BLACK`` and ``CANCER``.

So the tests below are mostly INVARIANTS rather than fix-pins: multiple
sclerosis must still refuse through every reviewed carrier, the other
abbreviations must still refuse standalone, and the admission gate must still
reject a genuinely protected value. Those hold on both sides of the change. The
one behavioral pin is stated as a cross-state equivalence -- ``MS`` must be
indistinguishable from ``TX`` -- so it cannot be satisfied by special-casing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.schemas._validators_protected_class import protected_class_marketing_reason
from backend.schemas.agent_plan import ComposePlanRequest
from backend.schemas.growth_agent import GrowthAgentPromptRunRequest
from backend.schemas.usps import USPS_STATE_CODES
from backend.services.genie_message_policy import (
    genie_visible_text_unsafe,
    protected_prompt_match,
)
from backend.services.genie_place_dimension import _disarms_a_protected_class_canary

# Governed per-state rollup shapes the product actually renders.
_GEOGRAPHY_TEMPLATES = (
    "{code} 70 borrowers.",
    "State rollup: 70 {code} borrowers and 20 LA borrowers.",
    "Borrowers in {code}.",
    "How many borrowers are in {code}?",
    "Show the top counties in {code}.",
    "State rollup by {code}.",
    "{code} leads the refinance funnel.",
    "What is the average rate spread in {code}?",
    "Which {code} counties have the most HELOC candidates?",
    "Show me {code} borrowers with high equity.",
    "Total UPB for {code}.",
)

# Every reviewed way the corpus binds a condition to a person.
_HEALTH_CARRIERS = (
    "Target borrowers with {term} for a refinance offer.",
    "Select homeowners who have {term}.",
    "Build a cohort of borrowers diagnosed with {term}.",
    "People living with {term} were selected for this offer.",
    "Homeowners suffering from {term} qualify for this offer.",
    "Borrowers managing {term} are the intended audience.",
    "Contact borrowers battling {term}.",
    "The campaign is for homeowners with {term}.",
    "Borrowers affected by {term} should receive this offer.",
    "Select applicants recovering from {term}.",
    "Homeowners treated for {term} are qualified.",
    "Borrowers dealing with {term} are eligible.",
    "Homeowners afflicted with {term} were selected for this offer.",
    "Applicants undergoing treatment who have {term} are eligible.",
    "{term} patients are eligible for this offer.",
    "{term}-affected homeowners were selected for this offer.",
    "{term} survivors are the intended audience.",
    "{term} sufferers were selected for this offer.",
    "{term} symptoms determine eligibility.",
    "Eligibility depends on the {term} diagnosis.",
)


@pytest.mark.parametrize("code", sorted(USPS_STATE_CODES))
@pytest.mark.parametrize("template", _GEOGRAPHY_TEMPLATES)
def test_governed_state_rollup_is_not_a_fair_lending_finding(code: str, template: str) -> None:
    """No USPS code turns a governed rollup into a protected-class refusal."""

    assert protected_class_marketing_reason(template.format(code=code)) != "protected_class"


@pytest.mark.parametrize("template", _GEOGRAPHY_TEMPLATES)
def test_mississippi_matches_a_state_with_no_health_collision(template: str) -> None:
    """``MS`` is indistinguishable from ``TX``, so nothing special-cases it."""

    assert protected_class_marketing_reason(template.format(code="MS")) == (
        protected_class_marketing_reason(template.format(code="TX"))
    )


@pytest.mark.parametrize(
    "term",
    ("MS", "ms", "M.S.", "multiple sclerosis", "COPD", "ADHD", "HIV", "OCD", "ALS", "AIDS"),
)
@pytest.mark.parametrize("carrier", _HEALTH_CARRIERS)
def test_health_carriers_still_refuse(term: str, carrier: str) -> None:
    """A condition bound to a person is a fair-lending finding, ``MS`` included."""

    assert protected_class_marketing_reason(carrier.format(term=term)) == "protected_class"


@pytest.mark.parametrize("term", ("COPD", "ADHD", "HIV", "OCD", "ALS", "AIDS"))
def test_unambiguous_abbreviations_still_refuse_standalone(term: str) -> None:
    """Only the two-letter ``MS`` gained a carrier requirement."""

    assert protected_class_marketing_reason(f"{term} 70 borrowers.") == "protected_class"
    assert protected_class_marketing_reason(f"Borrowers in {term}.") == "protected_class"


@pytest.mark.parametrize("term", ("multiple sclerosis", "M.S.", "m.s.", "M-S"))
def test_spelled_out_and_dotted_forms_stay_unconditioned(term: str) -> None:
    """Neither the full phrase nor a dotted evasion has a geography reading."""

    assert protected_class_marketing_reason(f"Rank {term} borrowers.") == "protected_class"


@pytest.mark.parametrize(
    "text",
    (
        "State rollup: 70 MS borrowers and 20 LA borrowers.",
        "Borrowers in MS.",
        "Which MS counties have the most HELOC candidates?",
    ),
)
def test_state_rollup_clears_every_guard_surface(text: str) -> None:
    """The collision reached all of these surfaces, so all of them are pinned."""

    assert protected_prompt_match(text) is None
    assert genie_visible_text_unsafe(text) is False
    assert genie_visible_text_unsafe(text, structured_value=True) is False
    assert GrowthAgentPromptRunRequest(prompt=text).prompt == text
    assert ComposePlanRequest(objective=text).objective == text


@pytest.mark.parametrize(
    "text",
    (
        "Target borrowers with MS for a refinance offer.",
        "MS patients are eligible for this offer.",
        "Target borrowers with multiple sclerosis.",
    ),
)
def test_health_carrier_still_refused_on_every_guard_surface(text: str) -> None:
    """Narrowing the term must not open the surfaces it was protecting."""

    assert protected_prompt_match(text) is not None
    assert genie_visible_text_unsafe(text) is True
    with pytest.raises(ValidationError):
        GrowthAgentPromptRunRequest(prompt=text)
    with pytest.raises(ValidationError):
        ComposePlanRequest(objective=text)


@pytest.mark.parametrize(
    "text",
    (
        # The honorific, in the loan-officer copy the product actually renders.
        "Ms. Johnson is the loan officer.",
        # Milliseconds -- the other real-world collision the bare token had.
        "Response time was 250 ms on average.",
        "The p95 latency is 40 ms.",
    ),
)
def test_other_two_letter_ms_collisions_are_not_findings(text: str) -> None:
    """Mississippi was not the only thing the bare token was misreading."""

    assert protected_class_marketing_reason(text) is None


def test_place_dimension_admits_mississippi_but_not_a_protected_value() -> None:
    """``MS`` was refused as a governed place value the way ``BLACK`` is."""

    assert _disarms_a_protected_class_canary("MS") is False
    assert _disarms_a_protected_class_canary("LA") is False
    # The gate itself is unchanged: a genuinely protected value still fails it.
    assert _disarms_a_protected_class_canary("BLACK") is True
    assert _disarms_a_protected_class_canary("CANCER") is True
