"""Shadow-pipeline provenance for the protected-class scanner.

The leetspeak fold turns digits into letters so split evasions rejoin
(``mus1im`` -> ``muslim``). The same fold turns a NUMBER into letters nobody
typed: ``4,140`` -> ``a,lao``, and ``lao`` is a governed national-origin term,
so a live Genie answer reading "NEWCASTLE has 4,140 borrowers" was withheld as
protected-class targeting (paychex, 2026-08-12).

Four designs that decided BEFORE matching all failed, each measured:

* skipping the fold for isolated digit runs silenced the ``1`` in
  ``mus 1 im`` -- adversarial review measured on the order of a hundred
  protected terms fully open on the Ask Genie posture (the exact count is
  corpus-dependent; the corpora were not preserved);
* provenance-filtered rejoin windows missed the context-gated banks
  (``ch-1-nese`` rendered: the rejoined bare token has no population noun);
* withholding the fold when the folded SPELLING is governed left the run as
  digits, and the rejoin tokenizer skips digits, so the number became a
  shield -- ``140-tian`` rendered while ``laotian`` refused, with weakenings
  measured in the low thousands on the review corpus;
* extending that check to whole-and-per-group still missed spliced windows
  (``1,405,000`` minted ``laos`` from ``l``+``aos``).

The two cases are the same shape before matching -- ``140`` + separator +
word -- so the decision has to happen AT the match: a protected-term match
stands unless every character in its span is a digit-minted letter. ``lao``
from ``4,140`` has none and is dropped; ``laotian`` from ``140-tian`` carries
``tian`` and refuses; ``muslim`` from ``mus 1 im`` carries ``mus``/``im`` and
refuses. The scan text is the base scanner's plus joined windows of nine
to twelve tokens (the cap was eight, which hid eleven-letter terms spelled
one letter per token); additional windows can only ADD matches, so that
widening moves in the refusing direction, and match acceptance is the only
thing provenance itself changes: it drops matches containing a wholly minted
word and nothing else. The same commit carries one
deliberate second change OUTSIDE this module -- joined windows hand the
context-gated banks their origin sentence for the population-noun test
(``window_origins`` in the scanner) -- so the full behavior delta is those
two effects together, each measured separately in the signoff artifacts.

Provenance is carried as a SHADOW STRING, not a mask array: the same
transforms run on a twin whose leet stage folds digits to the sentinel
letter ``Q`` instead of ``o/l/e/a/s/t``. Every transform downstream of the
fold decides by character class -- ``[A-Za-z]``, ``[A-Za-z0-9]``, hyphen
lookarounds -- and the sentinel is a letter, so real and shadow stay the same
length with the same token structure, and the class-keyed transforms run
unmodified on both strings. A position is source-backed exactly when the two
strings agree there. Two recipes could not be reused and are mirrored as
pair-aware twins pinned against their originals by
``test_leet_digit_match_provenance``: :func:`ascii_confusable_fold_pairs`
here, and ``_mask_health_pair`` in the scanner. The safe-context
substitutions likewise cannot re-run on sentinel text, so :func:`mirror_sub`
splices the shadow at the REAL string's match spans.

Fail-safe: if real and shadow ever diverge in length, the pair degrades to
``shadow == real`` -- every span reads as source-backed, which is exactly the
base scanner's behavior. Degradation can only ever restore the old false
positive, never open the guard.
"""

from __future__ import annotations

import re

# One sentinel for every foldable digit. Uppercase so it can never equal a
# folded letter (both tables emit lowercase); a SOURCE uppercase ``Q`` shadows
# as itself, agrees with the real string, and correctly reads as backed.
SHADOW_SENTINEL = "Q"
SHADOW_LEET_TABLE = str.maketrans("013457", SHADOW_SENTINEL * 6)


class ScanPair:
    """A scan string and its provenance shadow, kept the same length."""

    __slots__ = ("real", "shadow")

    def __init__(self, real: str, shadow: str | None = None) -> None:
        self.real = real
        # Length divergence degrades to base behavior (all backed), fail-safe.
        self.shadow = shadow if shadow is not None and len(shadow) == len(real) else real

    def backed(self, start: int, end: int) -> bool:
        """True when the span holds a source-backed letter AND no minted word.

        Two conditions, both load-bearing. At least one backed letter keeps a
        wholly minted term from standing on its own (``lao`` from ``4,140``).
        That alone is not enough: a bank whose pattern matches the term
        TOGETHER with its population noun hands the noun's provenance to the
        whole span -- ``415 borrowers`` folds to ``als borrowers``, the
        health-status pattern matches both words, and ``borrowers`` is backed
        (measured in signoff round two; ``als`` is the one governed term
        mintable outside the national-origin bank). So a multi-letter WORD
        inside the span that is entirely digit-minted vetoes the match: it is
        a number the fold rewrote, whatever stands beside it. One-letter runs
        are separator-fold artefacts (``c 4 n c 3 r`` scatters), not words,
        and stay exempt so scatter evasions keep refusing.
        """

        real = self.real
        shadow = self.shadow
        stop = min(end, len(real))
        any_backed = False
        run_length = 0
        run_backed = False
        for index in range(start, stop + 1):
            char = real[index] if index < stop else ""
            if char.isalpha():
                run_length += 1
                if shadow[index] == char:
                    run_backed = True
                    any_backed = True
                continue
            if run_length > 1 and not run_backed:
                return False
            run_length = 0
            run_backed = False
        return any_backed


def translate_pair(pair: ScanPair, table: dict[int, int], shadow_table: dict[int, int]) -> ScanPair:
    """Apply a 1:1 translation, folding the shadow with the sentinel table."""

    return ScanPair(pair.real.translate(table), pair.shadow.translate(shadow_table))


def regex_pair(pair: ScanPair, pattern: re.Pattern[str], replacement: str) -> ScanPair:
    """Apply the same class-keyed substitution to both strings independently.

    Valid only for patterns that match identically on real and shadow -- i.e.
    patterns deciding by character class where the sentinel is equivalent
    (separator runs, hyphens between letters, ``vv`` which no fold emits).
    That restriction is the safety argument, and it is NOT machine-enforced:
    the constructor's length check catches only length divergence, and an
    equal-length substitution with a word-shaped pattern would desynchronize
    positions silently. Every pattern passed here must decide by character
    class alone.
    """

    return ScanPair(
        pattern.sub(replacement, pair.real),
        pattern.sub(replacement, pair.shadow),
    )


def replace_pair(pair: ScanPair, old: str, new: str) -> ScanPair:
    """Apply an equal-length literal replacement to both strings."""

    return ScanPair(pair.real.replace(old, new), pair.shadow.replace(old, new))


_DOUBLE_V_RE = re.compile(r"vv", re.IGNORECASE)


def ascii_confusable_fold_pairs(pair: ScanPair) -> list[ScanPair]:
    """``ascii_confusable_folds``'s recipe, carried as pairs.

    Five entries, at most four distinct (the two fold orders commute); the
    duplicate is kept so the emitted set stays literally the original's.

    Both folds are shadow-safe to run independently: no leet table emits ``v``
    or ``I`` and the sentinel is neither, so ``vv`` runs and capital ``I``s
    exist at the same positions in both strings. Pinned against the original
    by ``test_leet_digit_match_provenance`` so the recipes cannot drift apart.
    """

    capital_i_folded = replace_pair(pair, "I", "l")
    double_v_folded = regex_pair(pair, _DOUBLE_V_RE, "w")
    return [
        pair,
        capital_i_folded,
        double_v_folded,
        regex_pair(capital_i_folded, _DOUBLE_V_RE, "w"),
        replace_pair(double_v_folded, "I", "l"),
    ]


def mirror_sub(pair: ScanPair, pattern: re.Pattern[str], replacement: str) -> ScanPair:
    """Substitute on the REAL string, splicing the shadow at the same spans.

    For word-shaped patterns (safe contexts) that cannot be re-run on shadow
    text. ``replacement`` enters the shadow verbatim: a space stays unbacked,
    and an inserted scan sentinel word stays matchable.
    """

    real_parts: list[str] = []
    shadow_parts: list[str] = []
    cursor = 0
    for match in pattern.finditer(pair.real):
        real_parts.append(pair.real[cursor : match.start()])
        shadow_parts.append(pair.shadow[cursor : match.start()])
        real_parts.append(replacement)
        shadow_parts.append(replacement)
        cursor = match.end()
    real_parts.append(pair.real[cursor:])
    shadow_parts.append(pair.shadow[cursor:])
    return ScanPair("".join(real_parts), "".join(shadow_parts))


def splice_pair(pair: ScanPair, start: int, end: int, insertion: str) -> ScanPair:
    """Replace [start, end) with ``insertion`` on both strings."""

    return ScanPair(
        f"{pair.real[:start]}{insertion}{pair.real[end:]}",
        f"{pair.shadow[:start]}{insertion}{pair.shadow[end:]}",
    )


def tokenize_pair(pair: ScanPair, pattern: re.Pattern[str]) -> list[ScanPair]:
    """Split both strings on the REAL string's token spans."""

    return [
        ScanPair(pair.real[match.start() : match.end()], pair.shadow[match.start() : match.end()])
        for match in pattern.finditer(pair.real)
    ]


def join_pairs(pairs: list[ScanPair], separator: str) -> ScanPair:
    """Join pairs with a separator that enters the shadow verbatim."""

    return ScanPair(
        separator.join(pair.real for pair in pairs),
        separator.join(pair.shadow for pair in pairs),
    )


def merge_pairs(pairs: list[ScanPair]) -> dict[str, str]:
    """Dedup by real text, OR-merging provenance across generating paths.

    Two paths can mint the same scan text with different provenance -- source
    ``mus lim`` and folded ``mus 1 im`` both yield ``muslim``. A position is
    backed if ANY path backs it, so a term genuinely present in the source is
    never dropped because an unbacked twin was generated alongside it.
    """

    merged: dict[str, str] = {}
    for pair in pairs:
        real, shadow = pair.real, pair.shadow
        existing = merged.get(real)
        if existing is None:
            merged[real] = shadow
        elif existing != shadow:
            merged[real] = "".join(
                r if a == r or b == r else a for r, a, b in zip(real, existing, shadow, strict=True)
            )
    return merged


def search_backed(
    pattern: re.Pattern[str],
    pair: ScanPair,
) -> re.Match[str] | None:
    """First match whose span contains a source-backed letter, else None."""

    for match in pattern.finditer(pair.real):
        if pair.backed(match.start(), match.end()):
            return match
    return None
