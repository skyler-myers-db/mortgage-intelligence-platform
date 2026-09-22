"""Two ordinary sentences from a live deep-research synthesis, and their guards.

Captured 2026-09-08 by replaying ``_synthesis_prompt(...)`` from
``databricks_genie_sweep`` against the paychex governed space and bisecting the
refused draft sentence by sentence, then by word window. Both sentences echo
the repo-authored prompt's own vocabulary ("state the offer call and the
signal behind it", "signal columns (rate spread, equity, triggers)", "any
single screen"), and each was withheld by a different detector on the OUTPUT
surface (``genie_visible_text_unsafe``):

* ``The offer call inside that top cohort is overwhelmingly Refinance +
  HELOC ...`` -- minimal fragment ``call inside that``. Not an outreach
  detector: the CONTEXTUAL person-name pattern reads ``call`` as a contact
  verb and, because ``inside`` was missing from its preposition row, paired
  "inside that" as a lowercase name. The twin with "recommended offer" passed.
  The same slot refused "the target of this campaign", "contact via email"
  and "the message of the brief".
* ``That means the best immediate targets are not simply the borrowers with
  the most visible live triggers on a single screen.`` -- minimal fragment
  ``targets are not simply the borrowers with the``. The health
  governance-intent pattern reads ``targets`` as a selection lead-in, and its
  generic ``population with <object>`` branch fails closed unless the WHOLE
  object is reviewed Module 0 vocabulary. ``triggers`` (the why-now signal the
  analytics-shape surface already reviews) was not in the attribute fragment,
  and neither was the presentation locus "on a single screen". The twin with
  "priorities" passed.

Both fixes are allow-side and closed: the rest of the English preposition
class (minus ``per``, an attested given name) on the name pattern, and the
trigger signal plus its screen locus on the reviewed attribute vocabulary.
Negation earns nothing -- "the targets are not simply the borrowers with
zyrplax" still refuses -- because a hedged selection statement still binds
the population to the object, and the health net is the only thing reading an
unknown object there.
"""

from __future__ import annotations

import pytest

from backend.schemas._validators_person_names import (
    contains_human_name_shape,
    shares_token_with_person_lexicon,
)
from backend.schemas._validators_protected_class import (
    contains_protected_class_marketing_text,
    protected_class_marketing_reason,
)
from backend.schemas._validators_unsafe_text import contains_unsafe_ai_text
from backend.schemas.marketing_selection_vocabulary import matches_reviewed_mortgage_attribute
from backend.services.genie_message_policy import genie_visible_text_unsafe

_OFFER_CALL_SENTENCE = (
    "The offer call inside that top cohort is overwhelmingly Refinance + HELOC: "
    "refi_plus_heloc accounts for 9 borrowers, while purchase accounts for 1 borrower."
)
_TARGETS_SENTENCE = (
    "That means the best immediate targets are not simply the borrowers with the most "
    "visible live triggers on a single screen."
)


def test_the_captured_synthesis_sentences_render() -> None:
    assert genie_visible_text_unsafe(_OFFER_CALL_SENTENCE) is False
    assert genie_visible_text_unsafe(_TARGETS_SENTENCE) is False


def test_the_task_controls_keep_their_verdicts() -> None:
    assert (
        genie_visible_text_unsafe(
            "Illinois leads with 48,396 in-the-money borrowers, well ahead of Texas at 10,914."
        )
        is False
    )
    assert genie_visible_text_unsafe("Target hispanic borrowers with high equity") is True
    assert genie_visible_text_unsafe("Call John Smith about his loan.") is True


# --- sentence 1: a preposition after a contact verb is grammar, not a name ---

# The closed class added to the contextual pattern's preposition row.
_ADDED_PREPOSITIONS = (
    "inside", "outside", "within", "beyond", "behind", "above", "below", "around", "along",
    "between", "among", "through", "throughout", "upon", "onto", "via", "until", "since",
    "of", "as", "like",
)  # fmt: skip


@pytest.mark.parametrize(
    "prose",
    [
        "call inside that",
        "the call inside that cohort",
        "The offer call within that cohort is Refinance.",
        "call outside the window",
        "The target of this campaign is refinance.",
        "Contact via email is preferred.",
        "the message of the brief is clear.",
        # A noun compound, not a person: ``audience`` joins the population row.
        "The target audience for this offer is HELOC candidates.",
    ],
)
def test_a_preposition_after_a_contact_verb_is_not_a_name(prose: str) -> None:
    assert contains_human_name_shape(prose) is False, prose
    assert genie_visible_text_unsafe(prose) is False, prose


@pytest.mark.parametrize("word", _ADDED_PREPOSITIONS)
def test_every_added_preposition_is_read_by_the_pattern(word: str) -> None:
    """Reachability, word by word, with a non-vacuity control in the same frame.

    ``zyrplax`` is not a preposition, so the frame still pairs it with the
    next word; that is what proves the verb slot is the one being read.
    """

    assert contains_human_name_shape(f"call {word} that cohort") is False, word
    assert contains_human_name_shape("call zyrplax that cohort") is True


def test_the_added_prepositions_share_no_token_with_the_person_lexicon() -> None:
    """The mechanical pin the cardinal row set the precedent for.

    ``per`` is the member of the class that fails this test's spirit (Per is a
    Scandinavian given name), which is why it is not in the row.
    """

    for word in _ADDED_PREPOSITIONS:
        assert shares_token_with_person_lexicon(word) is False, word
    assert "per" not in _ADDED_PREPOSITIONS


@pytest.mark.parametrize(
    "prose",
    [
        # Lowercase and outside the lexicon: the contextual pattern's real job.
        "call zara quinlan today",
        "contact zara quinlan about the offer",
        "email zara quinlan",
        # Per is a given name; the row deliberately leaves it scanning.
        "call per hansen today",
        # A particle is not a preposition, and a name naturally follows "call up".
        "call up zara quinlan",
        "Call John Smith about his loan.",
        "call john smith about the refi",
        # Excluding the preposition hides no name the other two scans can see:
        # the title-case pair and the lexicon pair still read past it.
        "call inside Zara Quinlan today",
        "call inside john smith today",
        "The target of john smith is a HELOC.",
    ],
)
def test_a_name_after_a_contact_verb_still_refuses(prose: str) -> None:
    assert contains_human_name_shape(prose) is True, prose
    assert genie_visible_text_unsafe(prose) is True, prose


def test_bare_outreach_directives_are_unchanged_on_the_analytics_surface() -> None:
    """Neither sentence's fix touches these: they were never refused here.

    The output surface is a read-only analytics narrative; its detectors are
    PII, injection, confidential, name-shape and fair-lending, not an outreach
    ban. Pinned so a future reader does not credit or blame this change.
    """

    assert genie_visible_text_unsafe("call them today") is False
    assert genie_visible_text_unsafe("call every borrower on this list") is False


# --- sentence 2: a reviewed signal behind a selection lead-in -----------------


@pytest.mark.parametrize(
    "prose",
    [
        _TARGETS_SENTENCE,
        "The targets are the borrowers with the most live triggers.",
        "We target borrowers with the most visible live triggers.",
        "targets are not simply the borrowers with the highest opportunity scores on a single "
        "screen.",
        "The best immediate priorities are not simply the borrowers with the most visible live "
        "triggers on a single screen.",
    ],
)
def test_a_reviewed_signal_behind_a_selection_lead_in_renders(prose: str) -> None:
    assert (
        contains_protected_class_marketing_text(prose, assume_reviewed_read_only_analytics=True)
        is False
    ), prose
    assert genie_visible_text_unsafe(prose) is False, prose


@pytest.mark.parametrize(
    "prose",
    [
        # Negation earns nothing: an unknown object in the captured frame.
        "That means the best immediate targets are not simply the borrowers with zyrplax.",
        "That means the best immediate targets are not simply the borrowers with eczema.",
        # A condition that happens to start with the reviewed noun.
        "The targets are the borrowers with trigger finger.",
        "The targets are the borrowers with migraine triggers.",
        # The reviewed object cannot carry a protected class or an unreviewed conjunct.
        "That means the best immediate targets are not simply the hispanic borrowers with the "
        "most visible live triggers on a single screen.",
        "That means the best immediate targets are not simply the borrowers with the most "
        "visible live triggers on a single screen and eczema.",
        "The targets are the borrowers with live triggers and the highest credit scores.",
        "Target hispanic borrowers with high equity",
    ],
)
def test_negation_and_the_new_vocabulary_admit_nothing_unreviewed(prose: str) -> None:
    assert genie_visible_text_unsafe(prose) is True, prose


@pytest.mark.parametrize(
    "prose",
    [
        "That means the best immediate targets are not simply the borrowers with zyrplax.",
        "The targets are the borrowers with trigger finger.",
        "That means the best immediate targets are not simply the borrowers with the most "
        "visible live triggers on a single screen and eczema.",
    ],
)
def test_the_health_net_is_still_the_detector_that_refuses(prose: str) -> None:
    """Exact reason, not merely "refused" (a reclassification would look green).

    The health governance-intent bank reports ``protected_class`` and runs
    before the criterion machine, with or without the analytics flag.
    """

    assert (
        protected_class_marketing_reason(prose, assume_reviewed_read_only_analytics=True)
        == "protected_class"
    ), prose


@pytest.mark.parametrize(
    "criterion",
    [
        "triggers",
        "live triggers",
        "the most live triggers",
        "the most visible live triggers",
        "key triggers",
        "why-now triggers",
        "why now triggers",
        "multiple live triggers",
        "the most visible live triggers on a single screen",
        "the most live triggers visible on a single screen",
        # The locus is a tail of the fragment, not of one alternative.
        "the highest opportunity scores on a single screen",
        # ``most`` is the count-noun twin of the ``highest`` already hoisted.
        "the most home equity",
        "the most competitor liens",
    ],
)
def test_the_trigger_signal_is_reviewed_vocabulary(criterion: str) -> None:
    assert matches_reviewed_mortgage_attribute(criterion) is True, criterion


@pytest.mark.parametrize(
    "criterion",
    [
        "trigger finger",
        "migraine triggers",
        "asthma triggers",
        "the most zyrplax",
        "most credit scores",
        "the most FICO",
        "the most eczema",
        "triggers and eczema",
        "the most triggers on a single screen and eczema",
        "on a single screen",
        "the most",
        "triggers on a single dashboard",
    ],
)
def test_the_trigger_alternative_and_the_screen_tail_admit_nothing_else(criterion: str) -> None:
    assert matches_reviewed_mortgage_attribute(criterion) is False, criterion


def test_the_trigger_signal_is_admissible_on_both_surfaces() -> None:
    """A reviewed Module 0 signal is admissible copy on the campaign surface too."""

    for prose in (
        "The top borrower candidates overall are those with the most live triggers.",
        "Rank borrowers with the most live triggers.",
    ):
        assert contains_unsafe_ai_text(prose) is False, prose
        assert genie_visible_text_unsafe(prose) is False, prose
    for prose in (
        "Rank borrowers with the most zyrplax.",
        "Add borrowers with live triggers and eczema to the campaign.",
    ):
        assert contains_unsafe_ai_text(prose) is True, prose
