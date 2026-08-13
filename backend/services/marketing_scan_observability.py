"""Make the protected-class scanner's digit-mint suppressions observable.

The scanner drops a governed term whose span was spelled by digits the
leetspeak fold rewrote, so "NEWCASTLE has 4,140 borrowers" renders instead of
being withheld as national-origin targeting (``4,140`` folds to ``lao``). That
suppression is correct and deliberate, and until now it left no trace: an
auditor asking "when does this fair-lending control decline to fire, and on
which term bank?" had nothing to read, and the rule is subtle enough that it
was wrong three times while it was being built.

``backend.schemas.marketing_scan_provenance`` may not import runtime services
(``test_architecture_boundaries`` pins the direction), so the scanner reports
the suppression as labels on its verdict and this module turns that report
into an event. Boundaries open :func:`observed_minted_suppressions` around the
call that consumes the verdict; the scan's front door publishes into whichever
collector is active, including on a cache hit.

**Labels only, never content.** The block log this parallels
(``genie_output_blocked``) is label-only because a fully reproducible
production block with no logged reason cost real diagnostic time in the
2026-08-07 persona audit, and the fix was labels rather than content. The same
reasoning binds harder here: a suppression means the span was a rendered
business measure, so logging it would put customer counts in the log stream to
describe an event that withheld nothing.

**Observability, not the audit tables.** The Lakebase audit trail records
decisions taken against a borrower or a campaign -- an answer withheld, a
draft approved -- and every row is meant to be a decision someone can be asked
about. A digit-mint suppression is the opposite: the guard declining to fire,
on a surface where it is expected to fire routinely because ordinary answers
are full of counts. Writing a row per suppression would add high-cardinality
non-decisions to the one table whose value is that everything in it IS a
decision. The rate belongs in the health body next to the other rolling
counters, and the per-bank detail in the structured log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from backend.schemas.marketing_scan_provenance import (
    MintedSuppression,
    collect_minted_suppressions,
)
from backend.services.observability import emit, record_protected_class_suppression

_SUPPRESSION_LOG = logging.getLogger("backend.services.marketing_scan_observability")


def record_minted_suppressions(
    suppressions: Sequence[MintedSuppression],
    *,
    surface: str,
) -> None:
    """Emit one event per distinct (bank, rule) pair the scan stood down.

    ``INFO``, not ``WARNING``. The block log warns because a withheld answer
    is an exception; this fires on ordinary numeric prose by design, and a
    warning per rendered table cell would train operators to filter the very
    stream the 2026-08-07 audit showed they need.
    """

    for suppression in suppressions:
        record_protected_class_suppression(term_bank=suppression.term_bank, kind=suppression.kind)
        emit(
            _SUPPRESSION_LOG,
            "protected_class_term_suppressed",
            level=logging.INFO,
            outcome="suppressed",
            surface=surface,
            term_bank=suppression.term_bank,
            suppression_kind=suppression.kind,
        )


@contextmanager
def observed_minted_suppressions(surface: str) -> Iterator[None]:
    """Record the suppressions raised by scans inside this block.

    Records on the way out even when the block raises, because one of the
    three boundaries is ``assert_no_protected_class_marketing_text``, whose
    normal failure mode is a ``ValueError``. A scan that suppressed one bank
    and refused on another is exactly the case worth having in the log.
    """

    with collect_minted_suppressions() as collected:
        try:
            yield
        finally:
            record_minted_suppressions(collected, surface=surface)


__all__ = ["observed_minted_suppressions", "record_minted_suppressions"]
