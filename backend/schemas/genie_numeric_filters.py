"""Canonical bounds for the numeric floors a governed Genie answer can replay.

One definition, five readers. A reviewed floor has to survive the whole
handoff -- the Genie action that writes the cohort row, the audit ledger it
writes alongside it, the campaign JSON projection that gates the approval
decision proof, the Lead Queue that reads the row back, and the campaign
treatment set that materializes the approved population. Each of those used to
carry its own copy of the range:

    backend/services/genie_actions.py           _REPLAYABLE_NUMERIC_FILTERS
    backend/services/audit_store.py             _RESULT_FILTER_NUMERIC_BOUNDS
    backend/schemas/campaign_json_projection.py _GENIE_NUMERIC_FILTER_BOUNDS
    backend/services/lead_query_helpers.py      COHORT_NUMERIC_FILTER_BOUNDS
    backend/services/campaign_treatment.py      _numeric_floor call sites

Five copies is five chances to disagree, and disagreement is not a cosmetic
drift: a key the action accepts but the ledger rejects raises
``AuditMetadataValueViolation`` *after* the Lakebase cohort row is already
written, and that subclasses ``RuntimeError`` while the caller catches
``ValueError`` -- so it surfaces as a 500 on a cohort that now exists. That is
how PR #191 shipped a latent 500. They all import this mapping now, so the
vocabularies move together or not at all.

Ranges (inclusive on both ends), measured live 2026-08-11 against paychex
``mip.gold.borrower_360``, 5,156,184 rows:

``min_opportunity_score`` / ``min_equity_pct`` -- 0..100. Both are percentages
    or 0-100 scores. Neither is ever negative in gold, and a negative floor on
    either is a malformed answer, not a narrower question. These MUST NOT be
    widened to accept negatives.

``min_rate_spread_bps`` -- -1000..5000. Rate spread is signed: POSITIVE means
    the borrower's coupon is above market (refi incentive), NEGATIVE means
    below it (a retention/recapture question, and the reason a "spread >= -25"
    answer is a real, common shape rather than a malformed one). Live domain
    is min -569, p1 -447, p50 0, p99 122, max 830, with 2,561,392 of 5,156,184
    rows negative -- half the book. A 0 floor therefore already narrows.
    The -1000 floor is a domain guard with ~1.75x headroom below the observed
    minimum: reaching it needs a borrower coupon 10 percentage points under
    market, which the [1%, 15%] rate clamp in ``gold_borrower_360.sql`` makes
    implausible, while still rejecting a mis-keyed magnitude (a dollar amount,
    a fraction scaled wrong). The 5000 ceiling is unchanged and equally
    generous -- 6x the observed maximum. If the FRED market rate ever rises
    far enough that legitimate answers cut below -1000, this floor is what to
    revisit; an out-of-range floor fails visibly with a 400 rather than
    silently replaying broader, which is the whole point.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# key -> (inclusive minimum, inclusive maximum)
GENIE_NUMERIC_FILTER_BOUNDS: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {
        "min_opportunity_score": (0, 100),
        "min_equity_pct": (0, 100),
        "min_rate_spread_bps": (-1000, 5000),
    }
)

GENIE_NUMERIC_FILTER_KEYS: Final[frozenset[str]] = frozenset(GENIE_NUMERIC_FILTER_BOUNDS)


def numeric_filter_bounds(key: str) -> tuple[int, int] | None:
    """Return the reviewed inclusive range for ``key``, or None if unknown."""

    return GENIE_NUMERIC_FILTER_BOUNDS.get(key)


def is_reviewed_numeric_floor(key: str, value: object) -> bool:
    """True when ``value`` is a whole number inside ``key``'s reviewed range.

    Bools are rejected explicitly: ``True`` is an ``int`` in Python and would
    otherwise replay as a floor of 1.
    """

    bounds = GENIE_NUMERIC_FILTER_BOUNDS.get(key)
    if bounds is None or isinstance(value, bool) or not isinstance(value, int):
        return False
    minimum, maximum = bounds
    return minimum <= value <= maximum


def numeric_filter_range_text(key: str) -> str:
    """Human-readable range for an error detail (``between -1000 and 5000``)."""

    bounds = GENIE_NUMERIC_FILTER_BOUNDS.get(key)
    if bounds is None:
        return "outside the reviewed vocabulary"
    return f"between {bounds[0]} and {bounds[1]}"
