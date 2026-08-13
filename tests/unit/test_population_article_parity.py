"""Article parity on the population-determiner slots of the criterion machine.

"Assign an owner." refused as ``unreviewed_criterion`` while "Assign the
owner." and "Assign owners." were answered (pre-existing false positive,
measured 2026-08-13). Two determiner slots spelled themselves ``the``-only:

* ``_REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE`` -- the allow branch for
  criterion-free formation commands -- admitted only ``the`` before the
  population noun, so the ``a``/``an`` form fell past it; and
* ``_AUDIENCE_FORMATION_BOUND_POPULATION_RE`` then captured the bare article
  ITSELF as the criterion ("assign an owner" -> criterion "an"), and
  ``_is_reviewed_pre_population_binding``'s transparent-determiner list had
  ``the``/``these``/``those``/``all``/``any`` but not ``a``/``an``.

Same family, all measured: "Queue a borrower.", "Target a customer.",
"Prioritize a lead.", "Shortlist an applicant.". This is the same defect
shape as the attribute-side article fix (#213), one vocabulary over: the
article is a determiner slot, not vocabulary, and admitting it names no new
criterion.

Fix battery, 2,355 probes x three surfaces (prompt, marketing reason,
campaign copy): 258 gains, every one an ``a``/``an`` form of an
already-allowed ``the``/bare twin; 0 losses; 0 reason shifts among
still-refused probes; 0 gains carrying a banked or unreviewed token.

Every refusal below asserts the EXACT reason string: ``is not None`` passes
through a silent reclassification, and the banked twins here are refused by
a different machine (the term banks) on purpose.
"""

from __future__ import annotations

import pytest

from backend.schemas._validators_unsafe_text import contains_unsafe_ai_text
from backend.schemas.marketing_selection_criteria import (
    _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE,
    _is_reviewed_pre_population_binding,
)
from backend.services.genie_message_policy import protected_prompt_match

# The restored family: an indefinite article before a closed population noun
# in a formation directive. Each has a ``the`` or bare twin that already
# answered on the pre-fix tree.
_ARTICLE_DIRECTIVES_ANSWERABLE = (
    "Assign an owner.",
    "Assign an owner for the campaign.",
    "Assign a reviewer.",
    "Queue a borrower.",
    "Target a customer.",
    "Prioritize a lead.",
    "Shortlist an applicant.",
    "Add an owner.",
    "Rank a candidate.",
    # The bound-population capture reached the article even without a
    # clause-initial directive; the mid-sentence form refused too.
    "Please review the file and assign an owner.",
)


@pytest.mark.parametrize("prompt", _ARTICLE_DIRECTIVES_ANSWERABLE)
def test_an_indefinite_article_before_a_population_is_answerable(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None, prompt


def test_the_bare_directive_branch_owns_the_allow() -> None:
    """Branch attribution: reroutes of this family should be visible, not silent."""

    assert _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch("assign an owner") is not None
    assert _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch("assign the owner") is not None
    assert _is_reviewed_pre_population_binding("an") is True
    assert _is_reviewed_pre_population_binding("the") is True


# ---------------------------------------------------------------------------
# Invariants -- green on BOTH sides of the fix. They pin what a transparent
# determiner must never do, independent of the change.

_ARTICLE_NEVER_LAUNDERS = (
    # Whatever remains after stripping a determiner must still full-match the
    # reviewed vocabulary; an unreviewed modifier keeps the clause refused.
    "Assign a zyrplax borrower to the campaign.",
    "Assign the zyrplax borrower to the campaign.",
)


@pytest.mark.parametrize("prompt", _ARTICLE_NEVER_LAUNDERS)
def test_an_article_cannot_launder_an_unreviewed_modifier(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


_BANKED_TWINS_KEEP_THEIR_REASON = (
    # The term banks fire above this machine and must keep owning these.
    "Assign a patient.",
    "Assign a diabetic.",
    "Assign an owner with eczema.",
)


@pytest.mark.parametrize("prompt", _BANKED_TWINS_KEEP_THEIR_REASON)
def test_the_term_banks_still_own_the_banked_twins(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "protected_class_language", prompt


# The destination-tailed command once refused for EVERY determiner -- an
# article-independent family that #222 measured unchanged and deliberately
# left pinned refusing. The closed destination tail on the bare-directive
# branch (``_REVIEWED_BARE_DIRECTIVE_DESTINATION_TAIL``) now admits it for
# every determiner alike; the family battery and its fail-closed twins live
# in test_bare_directive_destination_tail.py. Article parity still holds:
# all three determiners carry the same verdict.
_TAILED_FORMS_NOW_ANSWERABLE = (
    "Assign an owner to this campaign.",
    "Assign the owner to this campaign.",
    "Assign owners to this campaign.",
)


@pytest.mark.parametrize("prompt", _TAILED_FORMS_NOW_ANSWERABLE)
def test_the_destination_tailed_family_is_admitted_for_every_determiner(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None, prompt


def test_campaign_copy_surface_moves_with_the_prompt_surface() -> None:
    """The strict surface keeps refusing the laundering form, not the command."""

    assert contains_unsafe_ai_text("Assign a zyrplax borrower to the campaign.") is True
    assert contains_unsafe_ai_text("Assign an owner.") is False
