"""The three residuals left open by the 2026-08-12 leet-fold work.

Each was found by adversarial review of that change rather than by the suite,
and each is independent of the others:

1. ``PROTECTED_NATIONAL_ORIGIN_RE`` was built by sorting a ``frozenset`` on
   length alone, so equal-length terms came out in hash order and the compiled
   pattern differed on every process start. Harmless to matching -- two
   equal-length literals cannot both match at one position -- but it makes the
   pattern useless as a build artifact, and it cost a false "the refactor
   changed something" during the vocabulary split.

2. The scanner re-ran in full for every rendered table cell, and a governed
   table repeats its cells heavily.

A third residual -- the missing leading article before "the highest
opportunity scores" -- is deliberately NOT fixed here. See the module note in
``test_selection_criterion_count_invariance`` and the branch summary: the
reviewed attribute vocabulary is shared with the CAMPAIGN copy surface, which
must not inherit the analytics ranking bypass, so widening it broke
``test_campaign_copy_surface_keeps_fail_closed_ranking_grammar``. Scoping the
article to the directive grammar does not separate them either, because the
declarative co-reference "those with X" matches that same grammar. Fixing it
needs the analytics posture threaded into the criterion machine.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from backend.schemas._validators_protected_class import (
    _CACHEABLE_SCAN_CHARS,
    _protected_class_marketing_reason,
    _protected_class_marketing_reason_cached,
    protected_class_marketing_reason,
)

# Decisions that must not change when the memoizing front door is in play.
_CACHE_IDENTITY_CORPUS = (
    "NEWCASTLE has 4,140 borrowers.",
    "Kent has 1,405 residents.",
    "Target mus 1 im homeowners for this campaign.",
    "W0men homeowners may benefit from this mortgage review.",
    "Build a campaign audience of ch-1-nese homeowners.",
    "Identify the top 10 borrowers with eczema.",
    "identify the top 10 borrowers with the highest potential.",
    "black borrowers",
    "Rank our segments by average rate spread.",
    "",
    "The quick brown fox.",
)


@pytest.mark.parametrize("value", _CACHE_IDENTITY_CORPUS)
@pytest.mark.parametrize("analytics", (False, True))
def test_memoized_and_direct_decisions_are_identical(value: str, analytics: bool) -> None:
    """The cache may not be able to change a verdict.

    Every entry here is under the gate, because that is the only place the
    claim has content: past the gate the front door does not consult the
    cache at all, so an over-length case would assert ``f(x) == f(x)`` against
    an empty cache. :func:`test_the_gate_bypasses_rather_than_diverges` covers
    the other side.
    """

    assert protected_class_marketing_reason(
        value, assume_reviewed_read_only_analytics=analytics
    ) == _protected_class_marketing_reason(
        value, assume_reviewed_read_only_analytics=analytics
    )


def test_the_national_origin_pattern_is_identical_across_hash_seeds() -> None:
    """Built in a subprocess per seed, because the import is what varies.

    ``PYTHONHASHSEED`` is fixed at interpreter start, so the only honest way to
    show the pattern no longer depends on it is to start interpreters.
    """

    script = (
        "from backend.schemas.marketing_safety_terms import "
        "PROTECTED_NATIONAL_ORIGIN_RE as RE; print(RE.pattern)"
    )
    patterns = {
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "1", "2", "3")
    }

    assert len(patterns) == 1, "national-origin alternation still varies with the hash seed"


def test_longest_term_still_wins_the_alternation() -> None:
    """The reason the sort exists at all, kept explicit alongside the tie-break."""

    from backend.schemas.marketing_safety_terms import PROTECTED_NATIONAL_ORIGIN_RE

    match = PROTECTED_NATIONAL_ORIGIN_RE.search("laotian borrowers")
    assert match is not None
    assert match.group() == "laotian"


def test_the_gate_bypasses_rather_than_diverges() -> None:
    """Past the length gate the cache is skipped, not consulted-and-wrong.

    Pins the property the over-length parametrize case only appeared to test:
    a long value leaves the cache untouched AND still agrees with the direct
    call, so the gate is a bypass rather than a second code path.
    """

    long_value = "Kent has 1,405 residents. " * 40
    assert len(long_value) > _CACHEABLE_SCAN_CHARS
    _protected_class_marketing_reason_cached.cache_clear()

    assert protected_class_marketing_reason(long_value) == _protected_class_marketing_reason(
        long_value
    )
    assert _protected_class_marketing_reason_cached.cache_info().currsize == 0
