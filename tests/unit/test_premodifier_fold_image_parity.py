"""Separator parity for reviewed compounds under the intra-word hyphen fold.

"Add the highest-scoring leads to the campaign." refused as
``unreviewed_criterion`` while the space twin was answered (pre-existing
false positive, measured 2026-08-13). The protected-class scanner evaluates a
de-obfuscated variant that deletes intra-word hyphens ("highest-scoring" ->
"highestscoring"), and one tripping variant refuses the whole prompt. The
reviewed vocabularies spelled their compounds ``[- ]`` -- hyphen or space,
never joined -- so on that variant every allow grammar missed and the
bound-population capture read the joined image as an unreviewed criterion.

Fixed by spelling each already-reviewed compound's own joined fold image
(``[- ]?``) at every embedding the per-variant machine consults: the four
premodifier sites (``highest-scoring``/``marketing-eligible``), the
``government-backed`` attribute, the ``home-equity`` purpose/CTA/consent
products, and the named-lender ``loan-to-value``. A closed enumeration of
fold images, never a fold relaxation: the de-obfuscation itself is untouched,
so unreviewed compounds keep failing closed on their joined images.

Fix battery, 126 prompts x three surfaces (prompt guard, marketing reason,
campaign copy): every gain is a hyphen or joined spelling of a compound whose
space twin already answered; 0 losses; controls (unreviewed compounds in all
three separator forms, leet-inside-compound, spaced-out health terms)
unchanged. Refusals below assert the EXACT reason string: ``is not None``
would pass through a silent reclassification.
"""

from __future__ import annotations

import pytest

from backend.schemas.marketing_selection_criteria import (
    _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE,
    _is_reviewed_pre_population_binding,
)
from backend.services.genie_message_policy import protected_prompt_match

# The restored family: hyphenated and fold-joined spellings of reviewed
# compounds whose space twin already answered on the pre-fix tree.
_FOLD_IMAGE_ANSWERABLE = (
    "Add the highest-scoring leads to the campaign.",
    "Add the highest-scoring leads.",
    "Add the highestscoring leads.",
    "Queue the highest-scoring borrowers for this campaign.",
    "Add the marketing-eligible leads to the campaign.",
    "Add the marketingeligible leads to the campaign.",
    "Add the government-backed loan leads.",
    "Queue the government-backed loan borrowers for this campaign.",
    "Queue the borrowers with a high ltv for the home-equity campaign.",
    "List Rocket borrowers whose loan-to-value is above 80%",
)


@pytest.mark.parametrize("prompt", _FOLD_IMAGE_ANSWERABLE)
def test_a_reviewed_compound_answers_in_every_separator_form(prompt: str) -> None:
    assert protected_prompt_match(prompt) is None, prompt


def test_the_premodifier_branches_own_the_allow() -> None:
    """Branch attribution: reroutes of this family should be visible, not silent."""

    assert (
        _REVIEWED_BARE_AUDIENCE_DIRECTIVE_RE.fullmatch("add the highestscoring leads")
        is not None
    )
    assert _is_reviewed_pre_population_binding("the highestscoring") is True
    assert _is_reviewed_pre_population_binding("the marketingeligible") is True


# ---------------------------------------------------------------------------
# Invariants -- green on BOTH sides of the fix. They pin what the closed
# fold-image enumeration must never admit, independent of the change.

# An unreviewed compound stays refused in ALL separator forms: the fold keeps
# de-obfuscating, and the joined image of anything outside the reviewed
# enumeration is still an unknown criterion.
_UNREVIEWED_IN_EVERY_FORM = (
    "Add the left-handed leads to the campaign.",
    "Add the left handed leads to the campaign.",
    "Add the lefthanded leads to the campaign.",
    "Add the equity-rich leads to the campaign.",
    "Add the equityrich leads to the campaign.",
    # A reviewed attribute with an UNREVIEWED purpose product: the purpose
    # slot widened only by its own fold image, not by new vocabulary.
    "Queue the borrowers with a high ltv for the crypto campaign.",
    # Trigger-removed twins of the two remaining gain carriers: an unreviewed
    # measure in the named-lender query, an unreviewed product in the
    # consent-aware reroute. The carrier admits nothing on its own.
    "List Rocket borrowers whose credit-worthiness is above 80%",
    "Create a crypto campaign for the borrowers whose email opt-out is on file and call them instead.",
)


@pytest.mark.parametrize("prompt", _UNREVIEWED_IN_EVERY_FORM)
def test_an_unreviewed_compound_refuses_in_every_separator_form(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


# The admitted image alphabet is letters-only: a leet character inside a
# reviewed compound is not a reviewed spelling, and the plain variant fails
# closed regardless of what the fold recovers.
_LEET_INSIDE_A_REVIEWED_COMPOUND = (
    "Add the highest-sc0ring leads to the campaign.",
    "Add the marketing-eligib1e leads to the campaign.",
)


@pytest.mark.parametrize("prompt", _LEET_INSIDE_A_REVIEWED_COMPOUND)
def test_the_admitted_fold_image_is_letters_only(prompt: str) -> None:
    assert protected_prompt_match(prompt) == "unreviewed_criterion", prompt


def test_the_fold_still_deobfuscates_protected_terms() -> None:
    """The de-obfuscation variants themselves are untouched by this fix."""

    assert (
        protected_prompt_match("Add the e-c-z-e-m-a leads to the campaign.")
        == "protected_class_language"
    )
    assert (
        protected_prompt_match("Add the wheelchair-bound leads to the campaign.")
        == "protected_class_language"
    )
