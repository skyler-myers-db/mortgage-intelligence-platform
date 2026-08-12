"""The three residuals left open by the 2026-08-12 leet-fold work.

Each was found by adversarial review of that change rather than by the suite,
and each is independent of the others:

1. ``PROTECTED_NATIONAL_ORIGIN_RE`` was built by sorting a ``frozenset`` on
   length alone, so equal-length terms came out in hash order and the compiled
   pattern differed on every process start. Harmless to matching -- two
   equal-length literals cannot both match at one position -- but it makes the
   pattern useless as a build artifact, and it cost a false "the refactor
   changed something" during the vocabulary split.

2. A leading article was admitted on the potential/upside alternative of the
   reviewed attribute vocabulary and nowhere else, so "the highest potential"
   was reviewed while "the highest opportunity scores" -- the product's own
   governed scoring column -- was refused as an unknown criterion.

3. The scanner re-ran in full for every rendered table cell, and a governed
   table repeats its cells heavily.

The captures for (2), on paychex 2026-08-12, both refused in seconds with "it
is outside the reviewed Module 0 vocabulary" before any repository call::

    Show me the top 50 borrowers with the highest opportunity scores.
    Show me the top 20 borrowers with the highest lead scores.

With the article admitted, the same two questions return ``source=genie``,
``trusted=True``, 50 and 20 rows from ``mip.gold.lead_population``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from backend.schemas._validators_protected_class import (
    _CACHEABLE_SCAN_CHARS,
    _protected_class_marketing_reason,
    protected_class_marketing_reason,
)
from backend.schemas.marketing_selection_reviewed_attributes import (
    _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE,
)

# Governed Module 0 attributes, each written the way a growth lead writes it.
# Every one must be reviewed vocabulary with an article in front.
_REVIEWED_WITH_ARTICLE = (
    "the highest opportunity scores",
    "the highest lead scores",
    "a competitor lien",
    "the average rate spread",
    "the highest LTV",
    "an active property listing",
    "the highest potential",
    "the strong equity",
)

# The fail-closed boundary. An article must not turn an UNREVIEWED attribute
# into a reviewed one -- that is the whole risk of widening the head of the
# fragment rather than one alternative.
_UNREVIEWED_WITH_ARTICLE = (
    "the average credit score",
    "a credit score",
    "the highest FICO",
    "an eczema diagnosis",
    "the borrower race",
    "a zip code",
    "the highest income",
    "the largest household",
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
    "x" * (_CACHEABLE_SCAN_CHARS + 50),
)


@pytest.mark.parametrize("phrase", _REVIEWED_WITH_ARTICLE)
def test_an_article_does_not_make_a_reviewed_attribute_unreviewed(phrase: str) -> None:
    assert _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(phrase) is not None


@pytest.mark.parametrize("phrase", _UNREVIEWED_WITH_ARTICLE)
def test_an_article_does_not_make_an_unreviewed_attribute_reviewed(phrase: str) -> None:
    assert _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(phrase) is None


@pytest.mark.parametrize("phrase", _REVIEWED_WITH_ARTICLE)
def test_the_bare_form_is_reviewed_too(phrase: str) -> None:
    """The article is additive: dropping it must not change the verdict."""

    bare = phrase.split(" ", 1)[1] if phrase.split(" ", 1)[0] in {"the", "a", "an"} else phrase
    assert _REVIEWED_MORTGAGE_ATTRIBUTE_FULL_RE.fullmatch(bare) is not None


@pytest.mark.parametrize("value", _CACHE_IDENTITY_CORPUS)
@pytest.mark.parametrize("analytics", (False, True))
def test_memoized_and_direct_decisions_are_identical(value: str, analytics: bool) -> None:
    """The cache may not be able to change a verdict, including past its gate."""

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
