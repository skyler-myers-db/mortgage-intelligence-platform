"""The digit-mint suppression has to leave a trace, and only a trace.

The protected-class scanner drops a governed term whose span was spelled by
digits the leetspeak fold rewrote, so "NEWCASTLE has 4,140 borrowers" renders
rather than being withheld as national-origin targeting (``4,140`` folds to
``lao``). That suppression is correct. It was also silent, and it is the
decision a fair-lending auditor asks about, so these pin that it is now
reported -- as labels, without content, at each boundary that consumes the
verdict, and identically whether or not the scan cache is warm.

The counterpart property matters as much as the presence one: a suppression is
recorded only when the guard let text THROUGH. Text the guard refuses must
report nothing, or the log stops distinguishing "declined to fire" from
"fired".
"""

from __future__ import annotations

import logging

import pytest

from backend.schemas._validators_protected_class import (
    _protected_class_marketing_scan_cached,
    assert_no_protected_class_marketing_text,
    protected_class_marketing_scan,
)
from backend.schemas.marketing_scan_provenance import (
    MintedSuppression,
    collect_minted_suppressions,
)
from backend.services import observability
from backend.services.genie_message_policy import (
    genie_visible_text_unsafe,
    protected_prompt_match,
)
from backend.services.marketing_scan_observability import (
    observed_minted_suppressions,
    record_minted_suppressions,
)

# The reported defect, captured live on paychex 2026-08-12. ``4,140`` folds to
# ``a,lao`` and ``lao`` is governed national-origin vocabulary.
_DEFECT_TEXT = "NEWCASTLE has 4,140 borrowers."
# Same shape, different bank: ``415`` folds to ``als``, which the direct
# protected-class bank recognises next to a population noun.
_HEALTH_BANK_TEXT = "Kent has 415 borrowers."


@pytest.fixture(autouse=True)
def _clear_counters() -> None:
    observability._reset_counters_for_tests()


def test_the_reported_defect_renders_and_says_why() -> None:
    """The whole point: text that renders, with the suppression on the record."""

    verdict = protected_class_marketing_scan(_DEFECT_TEXT)

    assert verdict.reason is None
    assert MintedSuppression("national_origin", "minted_term_run") in verdict.suppressions


def test_a_second_bank_is_named_separately() -> None:
    """The bank label has to discriminate, or it is not worth recording."""

    banks = {
        suppression.term_bank
        for suppression in protected_class_marketing_scan(_HEALTH_BANK_TEXT).suppressions
    }

    assert banks and "national_origin" not in banks


@pytest.mark.parametrize(
    "refused",
    (
        # A term the author really typed, spelled with no digits at all.
        "laotian borrowers",
        # A split evasion the fold rejoins: the span keeps typed letters.
        "Target mus 1 im homeowners for this campaign.",
        # The mirror case the veto exists to protect -- ``1055`` folds to
        # ``loss`` beside a typed ``hearing``, which is a real disability term.
        "hearing 1055 borrowers",
    ),
)
def test_refused_text_reports_no_suppression(refused: str) -> None:
    """A suppression means the guard stood down. A refusal is not one.

    Without this, the log would fill with suppressions from scans that
    refused anyway, and "how often does this rule let something through?"
    would no longer be answerable from it.
    """

    verdict = protected_class_marketing_scan(refused)

    assert verdict.reason is not None
    assert verdict.suppressions == ()


def test_ordinary_prose_reports_nothing() -> None:
    verdict = protected_class_marketing_scan("Rank our segments by average rate spread.")

    assert verdict == (None, ())


def test_a_warm_cache_still_reports() -> None:
    """The failure mode a reason-only cache would have produced.

    A rendered governed table re-scans repeated cells -- 106 of 200 in the
    live 2026-08-12 capture. If the cache held the reason without the
    suppressions, the first cell would report and the other eighteen would
    not, and the signal would read as a falling rate whenever the cache
    warmed rather than as a constant one.
    """

    _protected_class_marketing_scan_cached.cache_clear()
    with collect_minted_suppressions() as cold:
        protected_class_marketing_scan(_DEFECT_TEXT)
    assert _protected_class_marketing_scan_cached.cache_info().currsize == 1

    with collect_minted_suppressions() as warm:
        protected_class_marketing_scan(_DEFECT_TEXT)

    assert cold and warm == cold


def test_the_collector_deduplicates_across_repeated_cells() -> None:
    """One boundary reports the distinct pairs it stood down, not a match count.

    The scan text is a join of roughly thirty de-obfuscation variants, so
    counting raw matches would report a number that tracks the scanner's
    internal fan-out rather than anything an auditor asked about.
    """

    with collect_minted_suppressions() as collected:
        for _ in range(19):
            protected_class_marketing_scan(_DEFECT_TEXT)

    assert len(collected) == len(set(collected))


def test_nothing_is_collected_outside_a_boundary() -> None:
    """No ambient accumulation: an unopened collector is not a leak."""

    protected_class_marketing_scan(_DEFECT_TEXT)

    with collect_minted_suppressions() as collected:
        pass

    assert collected == []


class TestBoundariesRecord:
    """Each of the three boundaries that consume the verdict."""

    def test_genie_answer(self) -> None:
        with collect_minted_suppressions() as collected:
            assert genie_visible_text_unsafe(_DEFECT_TEXT) is False

        assert collected == []  # the boundary consumed its own collector
        assert observability.recent_protected_class_suppressions() > 0

    def test_genie_structured_cell(self) -> None:
        assert genie_visible_text_unsafe("4,140 borrowers", structured_value=True) is False

        assert observability.recent_protected_class_suppressions() > 0

    def test_prompt_guard(self) -> None:
        assert protected_prompt_match(_DEFECT_TEXT) is None

        assert observability.recent_protected_class_suppressions() > 0

    def test_campaign_assert(self) -> None:
        with observed_minted_suppressions("outreach_approval"):
            assert_no_protected_class_marketing_text(_DEFECT_TEXT, field_name="draft_body")

        assert observability.recent_protected_class_suppressions() > 0

    def test_campaign_assert_records_even_when_it_raises(self) -> None:
        """The assert's normal failure mode is a ``ValueError``.

        One field suppresses and renders, a later one refuses. Recording on
        the success path would lose the first, which is the case an auditor
        most wants: copy that tripped the fair-lending scanner twice, once
        stood down and once enforced. Without the ``finally`` this counts 0.
        """

        with (
            pytest.raises(ValueError, match="protected-class"),
            observed_minted_suppressions("outreach_approval"),
        ):
            assert_no_protected_class_marketing_text(_DEFECT_TEXT, field_name="draft_body")
            assert_no_protected_class_marketing_text(
                "laotian borrowers", field_name="draft_body"
            )

        assert observability.recent_protected_class_suppressions() > 0


class TestTheRecordIsLabelsOnly:
    """Never the content. The block log is label-only for the same reason."""

    def test_the_event_carries_no_scanned_text(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="backend.services.marketing_scan_observability")

        record_minted_suppressions(
            protected_class_marketing_scan(_DEFECT_TEXT).suppressions,
            surface="genie_answer",
        )

        events = [
            record
            for record in caplog.records
            if record.getMessage() == "protected_class_term_suppressed"
        ]
        assert events
        for record in events:
            payload = str(getattr(record, "mip_extras", {}))
            assert "NEWCASTLE" not in payload
            assert "4,140" not in payload
            assert "4140" not in payload
            assert "lao" not in payload

    def test_the_event_still_names_the_bank_and_the_rule(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Label-only is not the same as content-free.

        The 2026-08-07 persona audit's lesson was that a block with no logged
        reason costs diagnostic time; dropping the labels to be safe would
        reproduce exactly that.
        """

        caplog.set_level(logging.INFO, logger="backend.services.marketing_scan_observability")

        record_minted_suppressions(
            (MintedSuppression("national_origin", "minted_term_run"),),
            surface="genie_answer",
        )

        extras = [
            getattr(record, "mip_extras", {})
            for record in caplog.records
            if record.getMessage() == "protected_class_term_suppressed"
        ]
        assert extras == [
            {
                "surface": "genie_answer",
                "term_bank": "national_origin",
                "suppression_kind": "minted_term_run",
            }
        ]
