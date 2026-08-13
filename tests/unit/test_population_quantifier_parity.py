"""A count quantifies a population; the criterion-free quantified command answers.

Pre-existing false positive, measured 2026-08-13: "Add the top 50 borrowers
to the campaign." and "Add the top 50 borrowers." both refused as
``unreviewed_criterion`` while "Add the top 50 borrowers with the highest
rate spread to the campaign." answered -- the CRITERION-FREE quantified
command was the only refused member of its family, backwards twice over: a
count quantifies a population and names no criterion (#218 doctrine), and
the criterion-carrying form is the one that binds more, not less.

The criterion-carrying form parsed because the admission grammar's open
action slot absorbed "add the top" and the #218 quantifier slot took "50".
No criterion-free branch had a slot for the count anywhere, so the family
refused through five distinct sites, each measured with a flipping twin:

* the bare-directive allow branch ("Add the top 50 borrowers to the
  campaign." / "Add the top 50 eligible borrowers to the campaign."),
* the prenominal-attribute allow branch ("Select the top 50 high home
  equity borrowers."),
* the bound-population capture, which read "the top 50" ITSELF as the
  unreviewed criterion in declarative clauses no imperative branch sees
  ("We picked the top 50 borrowers for the campaign."),
* the admission grammar's passive shapes, where no open action slot exists
  to absorb the lead word ("The top 50 high equity borrowers are placed
  into the campaign."), and
* the admission-criterion reviewer, handed "the top 50 high equity" by the
  criterion-first passive capture.

Fix: one shared closed fragment -- an optional literal lead word
(top|best|next|first) plus ``POPULATION_QUANTIFIER_DIGITS`` -- embedded at
all five sites, allow side and fail-closed side together, per the
numeric-quantifier-blind-lead-ins pattern. The lead word admits nothing
alone ("Add the top borrowers ..." keeps refusing), the digits admit
nothing anywhere (never an open adjective slot), and the admission
``_MODIFIERS`` half is load-bearing for fail-closed parity: without it,
teaching the criterion machine that a count is transparent would have let
"The top 50 borrowers are placed into the campaign based on <unreviewed
term>." prove no relation and sail past the criterion capture.

The battery then caught what the first cut of this fix would have cost, and
two closures rode along because of it:

* the admission ``_MODIFIERS`` run sat only IN FRONT of the count, so "the
  50 ELIGIBLE borrowers" proved no relation -- once the count was
  transparent, "The 50 eligible borrowers are placed into the campaign
  based on left-handedness." sailed past both nets (105 probes). The run
  now also follows the count, adjectives only.
* count-free and PRE-EXISTING on main: "The high home equity borrowers are
  placed into the campaign based on left-handedness." sailed, because a
  reviewed-attribute premodifier parses in neither grammar and the
  post-outcome capture required its connector to touch the participle. The
  capture now reads through a closed destination span and the admission
  grammar's causal connectors, and judges the criterion with the admission
  reviewer.

Differential battery, 4,200 verdict probes x four surfaces plus a 530-probe
admission-deletion name battery, base (main 26b3ae56) vs fix -- results
recorded in the PR description; every gain criterion-free or
reviewed-criterion with a base-allowed count-free twin, every loss an
unreviewed/banked condition riding a passive destination (the hole above,
closed), zero reason shifts, zero name-detection changes.

Every refusal below asserts the EXACT reason string: ``is not None`` passes
through a silent reclassification.
"""

from __future__ import annotations

import pytest

from backend.schemas._validators_unsafe_text import contains_unsafe_ai_text
from backend.schemas.marketing_selection_criteria import (
    _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE,
)
from backend.services.genie_message_policy import (
    identity_prompt_match,
    protected_prompt_match,
)

# The admitted family, one row per measured flipping site: imperative bare
# and destination-tailed forms across counts and lead words, the closed
# premodifier composition, the prenominal reviewed attribute, declarative
# formation, and the passive admission shapes.
_QUANTIFIED_CRITERION_FREE_ANSWERABLE = (
    "Add the top 50 borrowers to the campaign.",
    "Add the top 50 borrowers.",
    "Add 50 borrowers to the campaign.",
    "Add the top 1,000 borrowers to the campaign.",
    "Queue the next 25 leads.",
    "Shortlist the best 10 customers for the campaign.",
    "Move the first 100 borrowers into the queue.",
    "Add the top 50 eligible borrowers to the campaign.",
    "Select the top 50 high home equity borrowers.",
    "We picked the top 50 borrowers for the campaign.",
    "We shortlisted the best 25 customers for the offer.",
    "The top 50 high equity borrowers are placed into the campaign.",
    "The top 50 borrowers are placed into the campaign based on rate spread.",
    # The criterion-carrying twin that always answered; pinned so the family
    # can never split again.
    "Add the top 50 borrowers with the highest rate spread to the campaign.",
)


@pytest.mark.parametrize("prompt", _QUANTIFIED_CRITERION_FREE_ANSWERABLE)
def test_a_quantified_criterion_free_command_is_answerable(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None, prompt


def test_the_bare_directive_branch_owns_the_quantifier_slot() -> None:
    """Branch attribution: reroutes of this family should be visible, not silent."""

    for clause in (
        "add the top 50 borrowers to the campaign",
        "add 50 borrowers to the campaign",
        "queue the next 25 leads",
        "add the top 1,000 eligible borrowers to the campaign",
    ):
        assert _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch(clause) is not None, clause
    # The closed shape: a count-free lead word, an open adjective riding the
    # slot, or a fold-image lead word each break the anchor.
    for clause in (
        "add the top borrowers to the campaign",
        "add the top 50 left-handed borrowers to the campaign",
        "add the b3st 50 borrowers to the campaign",
    ):
        assert _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch(clause) is None, clause


# ---------------------------------------------------------------------------
# Invariants -- green on BOTH sides of the fix. The refusal this change
# removed bought nothing these machines do not already own.

_COUNT_FREE_LEAD_WORD_KEEPS_ITS_VERDICT = (
    # The lead word alone is NOT admitted by this change: whether "the top
    # borrowers" should answer is a separate widening with its own battery.
    "Add the top borrowers to the campaign.",
    "Add the best customers to the campaign.",
)


@pytest.mark.parametrize("prompt", _COUNT_FREE_LEAD_WORD_KEEPS_ITS_VERDICT)
def test_a_count_free_lead_word_still_fails_closed(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


_CLOSED_SHAPE_KEEPS_FAILING_CLOSED = (
    # An unreviewed word anywhere still breaks every shape the count now
    # fits: pre-population modifier, unreviewed with-criterion, unreviewed
    # passive condition, fold-image lead word, and the count riding where
    # only a criterion may stand.
    "Add the top 50 left-handed borrowers to the campaign.",
    "Add the top 50 zyrplax borrowers to the campaign.",
    "Add the b3st 50 borrowers to the campaign.",
    "The top 50 borrowers are placed into the campaign based on left-handedness.",
    "The top 50 borrowers are moved into the campaign.",
    "The top 50 borrowers are selected for the campaign.",
    # The two fail-opens the battery caught, pinned at their worst: the
    # closed premodifier AFTER the count, and the reviewed-attribute
    # premodifier that parses in neither grammar -- count-free, this second
    # shape sailed on main before this change.
    "The 50 eligible borrowers are placed into the campaign based on left-handedness.",
    "The top 50 eligible borrowers are placed into the campaign based on left-handedness.",
    "The high home equity borrowers are placed into the campaign based on left-handedness.",
    "The top 50 high home equity borrowers are placed into the campaign based on left-handedness.",
    "The qualified borrowers are routed to the queue because of left-handedness.",
)


_PASSIVE_DESTINATION_REVIEWED_CONDITIONS_ANSWER = (
    # The closure above must not cost the reviewed twins: the same passive
    # destination shapes with a reviewed condition keep answering, premod or
    # not, count or not, ``their`` or not.
    "The high home equity borrowers are placed into the campaign based on rate spread.",
    "The top 50 eligible borrowers are placed into the campaign based on rate spread.",
    "The eligible borrowers are placed into the campaign because of their rate spread.",
)


@pytest.mark.parametrize("prompt", _PASSIVE_DESTINATION_REVIEWED_CONDITIONS_ANSWER)
def test_a_reviewed_condition_behind_a_destination_still_answers(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None, prompt


@pytest.mark.parametrize("prompt", _CLOSED_SHAPE_KEEPS_FAILING_CLOSED)
def test_anything_outside_the_closed_shape_still_fails_closed(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


_TERM_BANKS_KEEP_THE_BANKED_TWINS = (
    "Add the top 50 borrowers with eczema to the campaign.",
    "Add the top 50 patients to the campaign.",
    "The top 50 borrowers are placed into the campaign based on eczema.",
)


@pytest.mark.parametrize("prompt", _TERM_BANKS_KEEP_THE_BANKED_TWINS)
def test_the_term_banks_still_own_the_banked_twins(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "protected_class_language", prompt


def test_a_count_cannot_launder_a_coordinated_unsafe_object() -> None:
    """#218's laundering pins hold with the new slots in place."""

    for prompt in (
        "Add the top 1,000 borrowers with a rate spread for the campaign and eczema.",
        "Add the top 50 borrowers with the home equity and credit score to the campaign.",
    ):
        assert protected_prompt_match(prompt) is not None, prompt


def test_person_names_stay_with_the_identity_scan() -> None:
    """The count must not change which surface owns a person name."""

    assert identity_prompt_match("Assign Maria Garcia to this campaign.") is True
    assert identity_prompt_match("Add the top 50 borrowers to the campaign.") is False


def test_campaign_copy_surface_moves_with_the_prompt_surface() -> None:
    """The strict surface admits the quantified command and keeps the controls."""

    assert contains_unsafe_ai_text("Add the top 50 borrowers to the campaign.") is False
    assert contains_unsafe_ai_text("We picked the top 50 borrowers for the campaign.") is False
    assert contains_unsafe_ai_text("Add the top 50 zyrplax borrowers to the campaign.") is True
    assert contains_unsafe_ai_text("Add the top 50 borrowers with eczema to the campaign.") is True
