"""A criterion-free admission command answers; its criterion twins keep their owners.

"Assign an owner to this campaign." refused as ``unreviewed_criterion`` for
EVERY determiner (pre-existing, measured 2026-08-13 during #222). The
admission grammar itself proves the family names no criterion --
``audience_admission_criterion`` returns None because there is no connector
to capture -- but the bare-directive allow branch admitted only a
``for the campaign|offer|review`` tail, so the clause fell to the
affirmative-directive fail-closed tail. The verdict hinged on the
preposition: "Assign an owner for the campaign." answered, and so did the
mid-sentence form "Please review the file and assign an owner to this
campaign." (the anchored affirmative branch never fires there).

Fix: ``_REVIEWED_BARE_DIRECTIVE_DESTINATION_TAIL`` -- a closed transfer
preposition into a closed determiner-plus-noun destination, appended as a
tail alternative on the bare-directive branch. Every slot is a literal
alternation, so no open vocabulary can ride inside the allowed span.

Differential battery, 1,482 probes x four surfaces (prompt, marketing
reason, campaign copy, identity): 893 gains, every one criterion-free with
a base-allowed stripped twin (trigger-removal attribution); 0 losses;
0 reason shifts among still-refused probes; 0 gains carrying a banked or
unreviewed token.

Every refusal below asserts the EXACT reason string: ``is not None`` passes
through a silent reclassification, and each refusing family here is owned
by a DIFFERENT machine on purpose -- the admission grammar, the term banks,
the closed-shape anchor, or the identity scan.
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

# The admitted family: command verb + closed population reference + closed
# destination tail. Sampled across verbs, populations, prepositions,
# determiners, and destination nouns; each slot's full alternation was
# exercised by the battery.
_DESTINATION_TAILED_ANSWERABLE = (
    "Assign an owner to this campaign.",
    "Assign the owner to this campaign.",
    "Assign owners to this campaign.",
    "Assign an owner to a campaign.",
    "Assign an owner to the reviewed campaign.",
    "Add these borrowers to the campaign.",
    "Add borrowers to the lead queue.",
    # The hyphenated twin ("highest-scoring") refuses on BOTH sides of this
    # change, tail or no tail: the hyphen-removal de-obfuscation fold hands
    # the criterion machine ``highestscoring``, a joined token outside every
    # premodifier vocabulary. Pre-existing fold-image gap, filed separately.
    "Add the highest scoring leads to the campaign.",
    "Move the selected borrowers into the queue.",
    "Move everyone in the list to the campaign.",
    "Transfer those leads to that segment.",
    "Put borrowers onto the list.",
    "Enroll customers in the campaign.",
    "Route prospects to the audience.",
    "Queue a borrower into this cohort.",
    "Please assign an owner to this campaign.",
)


@pytest.mark.parametrize("prompt", _DESTINATION_TAILED_ANSWERABLE)
def test_a_criterion_free_admission_command_is_answerable(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None, prompt


def test_the_bare_directive_branch_owns_the_allow() -> None:
    """Branch attribution: reroutes of this family should be visible, not silent."""

    for clause in (
        "assign an owner to this campaign",
        "add these borrowers to the campaign",
        "move the selected borrowers into the queue",
        "add borrowers to the lead queue",
    ):
        assert _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch(clause) is not None, clause
    # The closed shape, not a general class: a criterion between population
    # and tail, an unreviewed destination modifier, or an unknown destination
    # noun each break the anchor.
    for clause in (
        "assign borrowers with eczema to this campaign",
        "assign borrowers to the zyrplax campaign",
        "move borrowers to this crowd",
        "add borrowers to the campaigns",
    ):
        assert _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch(clause) is None, clause


# ---------------------------------------------------------------------------
# Invariants -- green on BOTH sides of the fix. The refusal this change
# removed bought nothing these machines do not already own.

_ADMISSION_GRAMMAR_KEEPS_THE_CRITERION_FORMS = (
    # A connector binds a criterion; ``marketing_audience_admission`` parses
    # it BEFORE the bare-directive branch is consulted.
    "Add borrowers to this campaign because they rent.",
    "Add borrowers with zyrplax to this campaign.",
    # Passives and nominal relations are unproved-relation forms, not
    # commands; ``_UNREVIEWED_RELATION_PATTERNS`` owns them.
    "Borrowers are assigned to this campaign.",
    "The admission of borrowers to the campaign.",
)


@pytest.mark.parametrize("prompt", _ADMISSION_GRAMMAR_KEEPS_THE_CRITERION_FORMS)
def test_the_admission_grammar_still_owns_criterion_and_relation_forms(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


_CLOSED_SHAPE_KEEPS_FAILING_CLOSED = (
    # An unreviewed word anywhere breaks the anchor: destination modifier,
    # unknown destination noun, plural destination, trailing purpose clause,
    # a second stacked tail, or an unreviewed population modifier.
    "Assign borrowers to the zyrplax campaign.",
    "Move borrowers to this crowd.",
    "Add borrowers to the campaigns.",
    "Assign owners to this campaign to exclude renters.",
    "Add borrowers to the queue to hide the audit trail.",
    "Assign an owner to this campaign for the review.",
    "Assign a zyrplax borrower to the campaign.",
    # "Add the top 50 borrowers to the campaign." sat here as a closed-shape
    # control, but it was pinning the NEXT false positive, not a control: a
    # count quantifies the population and names no criterion, and the
    # quantifier slot admitted it on 2026-08-13. Its family now lives in
    # ``test_population_quantifier_parity``; the open-vocabulary twin keeps
    # the fail-closed seat.
    "Add the top 50 zyrplax borrowers to the campaign.",
)


@pytest.mark.parametrize("prompt", _CLOSED_SHAPE_KEEPS_FAILING_CLOSED)
def test_anything_outside_the_closed_shape_still_fails_closed(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


_TERM_BANKS_KEEP_THE_BANKED_TWINS = (
    # The banks fire above this machine, in either the population or the
    # destination slot, in the same clause or a following one.
    "Add borrowers with eczema to this campaign.",
    "Assign borrowers to the eczema campaign.",
    "Add patients to this campaign.",
    "Assign an owner to this patient.",
    "Add borrowers to this campaign because they have eczema.",
    "Add borrowers to the campaign. They all have eczema.",
)


@pytest.mark.parametrize("prompt", _TERM_BANKS_KEEP_THE_BANKED_TWINS)
def test_the_term_banks_still_own_the_banked_twins(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "protected_class_language", prompt


def test_reviewed_criterion_admissions_still_answer() -> None:
    """The admission grammar's reviewed-vocabulary allow is untouched."""

    assert protected_prompt_match("Add borrowers with a competitor lien to this campaign.") is None
    # A following clause may bind a REVIEWED mortgage attribute through the
    # contextual coreference net; the eczema twin above keeps refusing.
    assert protected_prompt_match("Add borrowers to the campaign. They all have a rate spread.") is None


def test_person_names_stay_with_the_identity_scan() -> None:
    """The fair-lending machine never owned names; the identity scan does."""

    assert identity_prompt_match("Assign Maria Garcia to this campaign.") is True
    assert protected_prompt_match("Assign Maria Garcia to this campaign.") is None
    assert identity_prompt_match("Assign an owner to this campaign.") is False


def test_campaign_copy_surface_moves_with_the_prompt_surface() -> None:
    """The strict surface admits the command and keeps refusing the launder."""

    assert contains_unsafe_ai_text("Assign an owner to this campaign.") is False
    assert contains_unsafe_ai_text("Add these borrowers to the campaign.") is False
    assert contains_unsafe_ai_text("Assign borrowers to the zyrplax campaign.") is True
    assert contains_unsafe_ai_text("Add borrowers with eczema to this campaign.") is True
