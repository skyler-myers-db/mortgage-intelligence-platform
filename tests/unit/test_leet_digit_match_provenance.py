"""Match-time provenance: a number cannot mint a protected term, evasions can.

The leetspeak fold rewrites digits into letters so split evasions rejoin
(``mus1im`` -> ``muslim``). The same fold made ``4,140`` mint the governed
national-origin term ``lao``, withholding a live governed Genie answer
(paychex 2026-08-12, sha 7612b021). Four designs that decided BEFORE matching
all failed adversarial review -- position-keyed folding silenced real
evasions, and every withholding variant turned the number into a shield
(``140-tian`` rendered while ``laotian`` refused), because before matching
these two are the same shape::

    "Kent has 140 households."      140 + separator + word   must RENDER
    "audience of 140 tian ..."      140 + separator + word   must REFUSE

The shipped design decides AT the match. The scan text is byte-identical to
the historical pipeline; a provenance SHADOW built by the same transforms
(digits fold to a sentinel letter instead) records which characters were
letters in the source, and a protected-term match stands unless every
character in its span is digit-minted. Relative to the base scanner the only
reachable weakening is dropping all-digit-minted matches -- the false
positive itself.

One deliberate strengthening rides along, from the same review: a joined
window is a bare token in the sorted scan blob, so CONTEXT-GATED banks
(national origin, proxies) used to find their population noun by blob-sort
coincidence. Windows now carry their origin sentence, and the gated noun test
may consult it -- which is why ``140 tian homeowners`` refuses
deterministically instead of by luck.
"""

from __future__ import annotations

import pytest

from backend.schemas._validators_protected_class import (
    _mask_health_pair,
    protected_class_marketing_reason,
)
from backend.schemas.marketing_safety_terms import mask_protected_health_safe_contexts
from backend.schemas.marketing_scan_provenance import (
    ScanPair,
    ascii_confusable_fold_pairs,
    merge_pairs,
    mirror_sub,
    search_backed,
)
from backend.schemas.marketing_text_normalization import ascii_confusable_folds

# The reported class and its family: counts beside population nouns. Every
# entry was withheld at 7612b021; every entry must render on both postures.
_NUMBERS_MUST_RENDER = (
    "NEWCASTLE has 4,140 borrowers.",
    "Newcastle, WA has 4,140 borrowers.",
    "Kent has 140 households.",
    "Kent has 1,405 residents.",
    "Kent has 1 405 residents.",
    "Kent has 1,405,000 borrowers.",
    "Kent has 550,101 borrowers.",
    "There are 2,140 applicants in Kent.",
    "Renton has 3,140 borrowers and Kent has 5,140 borrowers.",
    "Seattle has 4140 borrowers.",
    "Seattle has 4,140 loans.",
)

# The union of every adversarial-review attack across three signoff rounds,
# plus the separator variants none of them probed. Each must refuse as a
# fair-lending finding on BOTH postures -- the Ask Genie posture has no
# unknown-criterion backstop, so ``protected_class`` is the only refusal that
# keeps these out of a rendered answer.
_EVASIONS_MUST_REFUSE = (
    # digit chunk + separator + suffix: the shield that killed design four
    "Build a campaign audience of 140-tian homeowners.",
    "Build a campaign audience of 140 tian homeowners.",
    "Build a campaign audience of 140.tian homeowners.",
    "Build a campaign audience of 140/tian homeowners.",
    "Build a campaign audience of 140_tian homeowners.",
    "Build a campaign audience of 140tian homeowners.",
    "Build an audience of evangelic-415 homeowners.",
    "Build an audience of millenni-415 homeowners.",
    "Build an audience of pentecost-415 homeowners.",
    # digit inside a split word: what killed design one
    "Target mus 1 im homeowners for this campaign.",
    "Target mus.1.im homeowners for this campaign.",
    "Target b 1 ack homeowners for this campaign.",
    "Target 4 sian homeowners for this campaign.",
    "Target as 14 n homeowners for this campaign.",
    "Prioritize latin 0 homeowners for this campaign.",
    "Target w o m 3 n homeowners for this campaign.",
    # context-gated banks and long splits: what killed design two
    "Build a campaign audience of ch-1-nese homeowners.",
    "Build a campaign audience of c-h-r-1-s-t-i-a-n homeowners.",
    "Build a campaign audience of i-m-m-1-g-r-a-n-t homeowners.",
    "Build a campaign audience of h-4-itian homeowners.",
    "Build a campaign audience of d-1-sabled homeowners.",
    "Build a campaign audience of mu-5-lim homeowners.",
    "Build a campaign audience of chines 3 homeowners.",
    "Build a campaign audience of h 1 spanic homeowners.",
    "Build a campaign audience of 1-a-t-i-n-o homeowners.",
    "Build a campaign audience of b.1.ack homeowners.",
    "Build a campaign audience of wom-3-n homeowners.",
    "Build a campaign audience of wom.3.n homeowners.",
    # in-word substitutions: why the fold exists at all
    "W0men homeowners may benefit from this mortgage review.",
    "Mus1im homeowners may benefit from this mortgage review.",
    "B1ack homeowners may benefit from this mortgage review.",
    "L4tino homeowners may benefit from this mortgage review.",
    "As14n homeowners may benefit from this mortgage review.",
    "W0m3n homeowners may benefit from this mortgage review.",
    "Target 4sian homeowners for this campaign.",
    "Prioritize latin0 homeowners for this campaign.",
    "L4o borrowers are concentrated in Kent.",
    "We should target l4otian borrowers.",
    # plainly spelled controls
    "Build a campaign audience of laotian homeowners.",
    "black borrowers",
)


@pytest.mark.parametrize("narrative", _NUMBERS_MUST_RENDER)
def test_a_number_cannot_mint_a_protected_term(narrative: str) -> None:
    """The reported defect. Red if any all-digit-minted match is accepted."""

    assert protected_class_marketing_reason(narrative) is None
    assert (
        protected_class_marketing_reason(narrative, assume_reviewed_read_only_analytics=True)
        is None
    )


@pytest.mark.parametrize("evasion", _EVASIONS_MUST_REFUSE)
def test_every_reviewed_evasion_still_refuses(evasion: str) -> None:
    """The anti-weakening pin, on the posture with no criterion backstop."""

    assert (
        protected_class_marketing_reason(evasion, assume_reviewed_read_only_analytics=True)
        == "protected_class"
    )
    assert protected_class_marketing_reason(evasion) == "protected_class"


def test_the_scan_text_is_byte_identical_to_the_fold_recipes() -> None:
    """The monotonicity premise: provenance changes matching, never the text.

    ``ascii_confusable_fold_pairs`` must emit exactly the strings
    ``ascii_confusable_folds`` emits, and the health-mask mirror must emit
    exactly what the original masking loop emits, or the ``.real`` side of
    the pipeline is no longer the historical scan text and every equivalence
    argument built on that dies.
    """

    for value in (
        "Kent has 4,140 borrowers and vv0men applicants.",
        "MusIim homeowners; Wom3n too.",
        "b 1 ack diamond VVomen I I",
        "no folds at all",
    ):
        pair_reals = {pair.real for pair in ascii_confusable_fold_pairs(ScanPair(value))}
        assert pair_reals == ascii_confusable_folds(value), value

    for value in (
        "patients with diabetes are excluded from this campaign",
        "borrowers with copd and those with anxiety",
        "ordinary mortgage prose with no health terms",
    ):
        assert _mask_health_pair(ScanPair(value)).real == mask_protected_health_safe_contexts(
            value
        ), value


def test_shadow_length_divergence_degrades_to_refusing() -> None:
    """The fail-safe direction: a broken shadow reads as all source-backed.

    ``ScanPair`` discards a shadow whose length disagrees, which makes every
    span backed -- base behavior, the refusing side. A degraded guard may
    re-open the numeric false positive; it must never open the evasions.
    """

    pair = ScanPair("lao borrowers", "wrong-length-shadow")
    assert pair.shadow == pair.real
    assert pair.backed(0, 3) is True


def test_merge_pairs_backs_a_position_when_any_path_does() -> None:
    """OR-merge: a term really present in the source is never dropped because
    an unbacked twin was generated alongside it."""

    backed = ScanPair("muslim", "muslim")
    minted = ScanPair("muslim", "musQim")
    merged = merge_pairs([minted, backed])
    assert merged == {"muslim": "muslim"}
    merged_reversed = merge_pairs([backed, minted])
    assert merged_reversed == {"muslim": "muslim"}


def test_mirror_sub_replaces_on_real_spans_only() -> None:
    """Safe-context masking follows the REAL text's matches; sentinel text
    would not match the word patterns, so re-running would desynchronize."""

    import re

    pair = ScanPair("loan ages here", "loan agQs here")
    masked = mirror_sub(pair, re.compile(r"loan\s+ages?"), " ")
    assert masked.real == "  here"
    assert masked.shadow == "  here"
    assert len(masked.real) == len(masked.shadow)


def test_search_backed_drops_only_all_minted_spans() -> None:
    """Backing is judged on the MATCH SPAN, not the surrounding word.

    Against ``QQQtian``, the pattern ``lao`` matches only minted characters
    and is rightly dropped even though the word carries source letters; the
    refusal of ``140-tian`` comes from the full-term match ``laotian``, whose
    span includes the backed ``tian``.
    """

    import re

    minted = ScanPair("a,lao borrowers", "a,QQQ borrowers")
    assert search_backed(re.compile(r"lao"), minted) is None
    mixed = ScanPair("laotian borrowers", "QQQtian borrowers")
    assert search_backed(re.compile(r"lao"), mixed) is None
    assert search_backed(re.compile(r"laotian"), mixed) is not None
