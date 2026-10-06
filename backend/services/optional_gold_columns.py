"""Fail-soft projection of optional gold columns (audits wow-stage-2 / wow-stage-4).

A roll-forward can promote the App ahead of the gold refresh that adds a
column (a first install deploys the App before any gold exists; the local
piecewise recipe can ship the App before ``mip_refresh_scores``). A read of the
new column then fails with ``UNRESOLVED_COLUMN``. For the columns registered
here that would turn the whole Lead Queue or dossier into a 503 over fields
the UI treats as optional.

This module is a CLOSED registry of column families. Each family maps the
exact projection fragments the repositories interpolate to NULL twins that
alias every column. ``ResilientSqlClient`` (``databricks_sql``) consults it on
a ``DatabricksSqlColumnMissingError`` naming a registered column of a family
whose fragment occurs in the failing statement:

* it latches the family until ``monotonic now + settings.mip_cache_ttl_s``;
* it replaces the family's fragments with their NULL twins and re-runs the
  statement ONCE through the same resilient call;
* while the latch holds, statements are rewritten before they execute, so the
  failing round trip is skipped;
* it logs one WARNING per latch, ``optional_gold_columns_unavailable``, with
  the family and the error class only (no SQL, no ids).

Anything else re-raises unchanged. This is resilience, not a mock: the rows
are real Unity Catalog rows and only the optional fields come back NULL. The
fallback only ever yields NULL, never a fabricated value, and every client
renders a NULL score_points or crossing field as absent, never as 0.

``databricks_shared`` builds its projection constants FROM these fragments, so
the fragment text has one source. A registered column must never appear in
backend SQL outside its fragment (no WHERE or ORDER BY use), because only the
projection can be rewritten (pinned by tests/unit/test_optional_gold_columns.py).
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Lock

from backend.config.settings import settings
from backend.services.observability import emit

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptionalColumnFamily:
    """One family: its columns and each (fragment, NULL twin) pair."""

    name: str
    columns: tuple[str, ...]
    fragments: tuple[tuple[str, str], ...]


# --- score_points: the five weighted sub-scores (wow-stage-2) ---------------
SCORE_POINTS_COLUMNS: tuple[str, ...] = (
    "economic_incentive_points",
    "intent_trigger_points",
    "fit_points",
    "relationship_points",
    "evidence_points",
)


def _qualified(alias: str, columns: tuple[str, ...]) -> str:
    return "".join(f"{alias}.{column}, " for column in columns)


def _null_twin(typed: tuple[tuple[str, str], ...]) -> str:
    return "".join(f"CAST(NULL AS {sql_type}) AS {column}, " for column, sql_type in typed)


_SCORE_POINTS_TYPED = tuple((column, "DECIMAL(5,2)") for column in SCORE_POINTS_COLUMNS)
LP_SCORE_POINTS_FRAGMENT = _qualified("lp", SCORE_POINTS_COLUMNS)
B360_SCORE_POINTS_FRAGMENT = _qualified("b", SCORE_POINTS_COLUMNS)

# --- spread_history: the dossier's Crossed-the-line inputs (wow-stage-4) ----
SPREAD_HISTORY_COLUMNS: tuple[str, ...] = ("first_pos_date", "first_pos_rate_type", "first_itm_week")
_SPREAD_HISTORY_TYPED = (
    ("first_pos_date", "DATE"),
    ("first_pos_rate_type", "STRING"),
    ("first_itm_week", "DATE"),
)
DOSSIER_SPREAD_HISTORY_FRAGMENT = "".join(f"{column}, " for column in SPREAD_HISTORY_COLUMNS)

FAMILIES: dict[str, OptionalColumnFamily] = {
    "score_points": OptionalColumnFamily(
        name="score_points",
        columns=SCORE_POINTS_COLUMNS,
        fragments=(
            (LP_SCORE_POINTS_FRAGMENT, _null_twin(_SCORE_POINTS_TYPED)),
            (B360_SCORE_POINTS_FRAGMENT, _null_twin(_SCORE_POINTS_TYPED)),
        ),
    ),
    "spread_history": OptionalColumnFamily(
        name="spread_history",
        columns=SPREAD_HISTORY_COLUMNS,
        fragments=((DOSSIER_SPREAD_HISTORY_FRAGMENT, _null_twin(_SPREAD_HISTORY_TYPED)),),
    ),
}

# Databricks names the unresolved column as backticked parts after "with
# name" (``[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column, variable, or function
# parameter with name `lp`.`fit_points` cannot be resolved``); the older
# analyzer wording quotes it after "cannot resolve". Only the column named
# there counts: a "Did you mean" suggestion list can mention other columns.
_NAMED_COLUMN_RE = re.compile(r"with name ((?:`[^`]+`\.?)+)")
_LEGACY_NAMED_COLUMN_RE = re.compile(r"cannot resolve '`?([\w.`]+?)`?'")

_LATCH: dict[str, float] = {}
_LATCH_LOCK = Lock()
# Read at call time, so a test can drive the latch with its own clock.
_monotonic: Callable[[], float] = time.monotonic


def _missing_column_name(message: str) -> str | None:
    match = _NAMED_COLUMN_RE.search(message)
    if match is not None:
        parts = re.findall(r"`([^`]+)`", match.group(1))
        return parts[-1].lower() if parts else None
    legacy = _LEGACY_NAMED_COLUMN_RE.search(message)
    if legacy is not None:
        return legacy.group(1).replace("`", "").rsplit(".", 1)[-1].lower()
    return None


def family_for_missing_column(error: BaseException | None, statement: str) -> str | None:
    """The registered family the failure names, when its fragment is in ``statement``.

    ``error`` is a ``DependencyDownError.last_error``. Only a
    ``DatabricksSqlColumnMissingError`` qualifies; a permission refusal, a
    missing table or an outage returns None, so the caller re-raises.
    """
    from backend.services.databricks_sql import DatabricksSqlColumnMissingError

    if not isinstance(error, DatabricksSqlColumnMissingError):
        return None
    column = _missing_column_name(str(error))
    if column is None:
        return None
    for family in FAMILIES.values():
        if column in family.columns and any(fragment in statement for fragment, _ in family.fragments):
            return family.name
    return None


def rewrite(statement: str, families: Iterable[str]) -> str:
    """Replace each named family's fragments with their NULL twins."""
    for name in families:
        for fragment, twin in FAMILIES[name].fragments:
            statement = statement.replace(fragment, twin)
    return statement


def latched_families() -> tuple[str, ...]:
    current = _monotonic()
    with _LATCH_LOCK:
        for name in [name for name, until in _LATCH.items() if until <= current]:
            del _LATCH[name]
        return tuple(sorted(_LATCH))


def rewrite_latched(statement: str) -> str:
    """Rewrite ``statement`` for every family whose latch still holds."""
    latched = latched_families()
    return rewrite(statement, latched) if latched else statement


def latch(family: str, *, error: BaseException | None) -> None:
    """Hold ``family`` on its NULL twins for one soft cache TTL; warn once per latch."""
    if family not in FAMILIES:
        raise ValueError(f"unregistered optional gold column family: {family}")
    current = _monotonic()
    with _LATCH_LOCK:
        newly = _LATCH.get(family, float("-inf")) <= current
        _LATCH[family] = current + max(0.0, float(settings.mip_cache_ttl_s))
    if newly:
        emit(
            log,
            "optional_gold_columns_unavailable",
            level=logging.WARNING,
            family=family,
            error_class=type(error).__name__ if error is not None else None,
        )


def _reset_optional_gold_columns_for_tests() -> None:
    with _LATCH_LOCK:
        _LATCH.clear()


__all__ = [
    "B360_SCORE_POINTS_FRAGMENT",
    "DOSSIER_SPREAD_HISTORY_FRAGMENT",
    "FAMILIES",
    "LP_SCORE_POINTS_FRAGMENT",
    "SCORE_POINTS_COLUMNS",
    "SPREAD_HISTORY_COLUMNS",
    "OptionalColumnFamily",
    "family_for_missing_column",
    "latch",
    "latched_families",
    "rewrite",
    "rewrite_latched",
]
