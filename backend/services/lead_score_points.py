"""Project a gold lead row's five weighted sub-scores into ``score_points``.

Audit wow-stage-2 (gold half): gold.borrower_360 projects
``economic_incentive_points`` .. ``evidence_points`` from its own subscores CTE,
and lead_population / the geo drill-down read carry them. A queue row then
shows the same score anatomy the dossier's ScoreSpine shows, without a
per-row /proof read.

The projection is fail-closed and never fabricates:

* any key absent or NULL (an older gold refresh, or the optional-column NULL
  twins of ``optional_gold_columns``) returns None;
* a value that is not a finite number in 0..100 returns None;
* the five must sum, banker-rounded (ROUND_HALF_EVEN, the SQL ``BROUND``) and
  clipped to 0..100, to the row's own ``opportunity_score``; otherwise the
  points disagree with the score they explain and the row gets None. A
  mismatch logs ONE warning per process, ``lead_score_points_mismatch``,
  with counts only and never an id.

DECIMAL columns arrive as a ``str``, a ``float`` or a ``Decimal`` depending on
the client path (``databricks_sql._coerce``); each is read through
``Decimal(str(value))`` so a float never adds binary noise to the sum.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from threading import Lock
from typing import Any

from backend.schemas.lead import LeadScorePoints
from backend.services.observability import emit

log = logging.getLogger(__name__)

# (LeadScorePoints key, gold column), in the canonical fn_lead_score order.
SCORE_POINT_KEYS: tuple[tuple[str, str], ...] = (
    ("economic_incentive", "economic_incentive_points"),
    ("intent_trigger", "intent_trigger_points"),
    ("fit", "fit_points"),
    ("relationship", "relationship_points"),
    ("evidence", "evidence_points"),
)

_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_MISMATCH_LOCK = Lock()
_MISMATCH_COUNT = 0


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed > _HUNDRED:
        return None
    return parsed


def _note_mismatch() -> None:
    global _MISMATCH_COUNT
    with _MISMATCH_LOCK:
        _MISMATCH_COUNT += 1
        first = _MISMATCH_COUNT == 1
    if first:
        emit(log, "lead_score_points_mismatch", level=logging.WARNING, mismatches=1)


def score_points_from_row(row: Mapping[str, Any]) -> LeadScorePoints | None:
    """The row's ``LeadScorePoints``, or None when they are absent or disagree."""
    points: dict[str, Decimal] = {}
    for key, column in SCORE_POINT_KEYS:
        value = _decimal(row.get(column))
        if value is None:
            return None
        points[key] = value
    score = row.get("opportunity_score")
    if score is None or isinstance(score, bool):
        return None
    try:
        expected = int(score)
    except (TypeError, ValueError):
        return None
    total = sum(points.values(), Decimal(0))
    recomputed = max(0, min(100, int(total.quantize(_ONE, rounding=ROUND_HALF_EVEN))))
    if recomputed != expected:
        _note_mismatch()
        return None
    return LeadScorePoints(**{key: float(value) for key, value in points.items()})


def _reset_lead_score_points_for_tests() -> None:
    global _MISMATCH_COUNT
    with _MISMATCH_LOCK:
        _MISMATCH_COUNT = 0


__all__ = ["SCORE_POINT_KEYS", "score_points_from_row"]
