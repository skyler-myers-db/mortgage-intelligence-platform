"""Reviewed cohort route filters for governed Genie actions.

Single responsibility: turn a Genie action payload's ``result_filters`` into
the closed, reviewed vocabulary the Lead Queue can replay verbatim -- the
per-key value validators (list, city/state pair, numeric floor), the
disclosure shaping for keys that cannot be replayed, and the replay-key
vocabularies themselves. Nothing here reads or writes Lakebase; the audit,
idempotency, and campaign paths live in ``backend.services.genie_actions``.

The vocabularies are the load-bearing part: one cohort filter key has to be
spelled the same way in five closed sets or the action 500s downstream, so
they stay together in one auditable module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.genie_geo_filters import (
    GENIE_CITY_FILTER_KEY,
    MAX_CITY_FILTER_VALUES,
    parse_city_state_pair,
)
from backend.schemas.genie_numeric_filters import GENIE_NUMERIC_FILTER_BOUNDS
from backend.schemas.lead import GENIE_REPLAY_SEGMENT_CODES
from backend.schemas.portfolio import PortfolioCriteria
from backend.services.audit_store import _assert_allowlisted, _assert_no_pii
from backend.services.genie_answers import GenieActionRequest

_MAX_ACTION_FILTER_VALUES = 500
# Reviewed numeric floors the Lead Queue applies verbatim, mapped to their
# inclusive (minimum, maximum). Each is an integer `>=` predicate over a
# gold.borrower_360 column, so a Genie answer narrowed by one of these hands
# off the SAME population it just reported instead of a broader one:
#   min_opportunity_score -> b.opportunity_score
#   min_equity_pct        -> equity_pct (via reviewed PortfolioCriteria)
#   min_rate_spread_bps   -> b.rate_spread_bps
# The ranges live in ONE place -- backend/schemas/genie_numeric_filters.py --
# because four other vocabularies validate the same values downstream and a
# key bounded differently in one of them 500s after the cohort row is written.
_REPLAYABLE_NUMERIC_FILTERS: Mapping[str, tuple[int, int]] = GENIE_NUMERIC_FILTER_BOUNDS
# Keys the Lead Queue can actually replay (see `_cohort_route_filters`).
# Anything else in a Genie answer's result_filters is disclosed, not applied.
_REPLAYABLE_FILTER_KEYS = frozenset(
    {
        "zips",
        GENIE_CITY_FILTER_KEY,
        "county",
        "counties",
        "states",
        "segment_codes",
        "segment_mode",
        "target_lender_ref",
        "portfolio_criteria",
        "borrower_ids",
        "source",
        *_REPLAYABLE_NUMERIC_FILTERS,
    }
)
# Built from the one canonical replay vocabulary, not spelled out again.
_GENIE_SEGMENT_CODE_RE = re.compile(
    r"^(" + "|".join(sorted(GENIE_REPLAY_SEGMENT_CODES)) + r")$", re.IGNORECASE
)
_MAX_UNREPLAYABLE_FILTER_KEYS = 12
_MAX_UNREPLAYABLE_FILTER_KEY_LEN = 64
_UNSAFE_FILTER_KEY_CHARS = re.compile(r"[^a-z0-9_]+")
_MAX_ACTION_STATE_VALUES = 56
_LEAD_QUEUE_REPLAY_KEYS = frozenset(
    {
        "state",
        "states",
        "zip",
        "zips",
        # Plural-only, and stripped off the inbound URL like every other
        # replayable key: `_route_with_cohort` re-adds it from the cohort row,
        # so a hand-edited `?cities=…` cannot survive alongside a cohort_id.
        GENIE_CITY_FILTER_KEY,
        "county",
        "counties",
        "borrower_ids",
        "segment",
        "segment_codes",
        "segment_mode",
        "target_lender_ref",
        "cohort_id",
        "funnel_stage",
        "approval_status",
        "outreach_status",
        "assigned_to",
        "aged_days",
        "limit",
        "geography",
        "occupancy",
        "lien_status",
        "lender_relationship",
        "product",
        "min_equity_pct_label",
        "owner_link",
        "purchase_intent",
        "marketing_eligibility",
        "consent_status",
        "recency",
        *_REPLAYABLE_NUMERIC_FILTERS,
    }
)
_LEAD_QUEUE_PORTFOLIO_QUERY_KEYS = frozenset(
    {
        "geography",
        "occupancy",
        "lien_status",
        "lender_relationship",
        "product",
        "min_equity_pct_label",
        "owner_link",
        "purchase_intent",
        "recency",
    }
)


def _list_filter(
    raw: Any,
    *,
    field: str,
    max_items: int,
    pattern: re.Pattern[str],
) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=400,
            detail=f"Genie cohort {field} filter must be a reviewed list",
        )
    out: list[str] = []
    for item in raw:
        value = str(item).strip()
        if not pattern.fullmatch(value):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid replay filter",
            )
        value = value.upper() if pattern.pattern != r"^\d{5}$" else value
        if value not in out:
            if len(out) >= max_items:
                raise HTTPException(
                    status_code=400,
                    detail="Genie cohort includes too many replay filters",
                )
            out.append(value)
    return out


def _city_states_filter(raw: Any) -> list[str]:
    """Validate the cohort's ``CITY~ST`` list, or 400 like every sibling key.

    Deliberately STRICTER than the writer, which fails closed to a disclosure:
    by the time a payload reaches here the pairs are a reviewed cohort key, and
    a malformed one is a bad request rather than a city we could not read.
    """

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(
            status_code=400,
            detail=f"Genie cohort {GENIE_CITY_FILTER_KEY} filter must be a reviewed list",
        )
    out: list[str] = []
    for item in raw:
        pair = parse_city_state_pair(item)
        if pair is None:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid replay filter",
            )
        value = f"{pair[0]}~{pair[1]}"
        if value in out:
            continue
        if len(out) >= MAX_CITY_FILTER_VALUES:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes too many replay filters",
            )
        out.append(value)
    return out


def _numeric_floor(raw: Any, *, field: str, minimum: int, maximum: int) -> int | None:
    """Return a reviewed integer floor, or None when the answer omitted it.

    Closed vocabulary, same posture as the list filters above: the value must
    be a whole number inside the column's domain. Fractions, booleans, ranges,
    expressions, and out-of-range numbers are rejected rather than coerced,
    because a coerced threshold would silently replay a different population
    than the answer reported. A `0` floor is kept, not dropped -- for
    ``min_rate_spread_bps`` it is a real predicate (spreads can be negative).

    The range is per field, not shared: score and equity floor at 0, while
    ``min_rate_spread_bps`` accepts negatives because the column IS negative on
    half of gold. See ``backend/schemas/genie_numeric_filters.py``.
    """

    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
        raise HTTPException(
            status_code=400,
            detail=f"Genie cohort {field} filter must be a reviewed integer threshold",
        )
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Genie cohort {field} filter must be a reviewed integer threshold",
        ) from exc
    if value < minimum or value > maximum:
        raise HTTPException(
            status_code=400,
            detail=f"Genie cohort {field} filter is outside the reviewed range",
        )
    return value


def _disclosed_filter_key(key: object) -> str:
    """Normalize an unreviewed Genie filter name for safe disclosure.

    The key text is model-authored, so it is data, not an identifier we
    control. Fold it to the audit vocabulary's identifier shape before it
    reaches the Lakebase cohort row or the audit ledger.
    """

    normalized = _UNSAFE_FILTER_KEY_CHARS.sub("_", str(key).strip().lower()).strip("_")
    return normalized[:_MAX_UNREPLAYABLE_FILTER_KEY_LEN]


def _cohort_route_filters(
    payload: GenieActionRequest, payload_borrower_ids: list[str]
) -> dict[str, Any]:
    """Return the reviewed, lead-queue-replayable filter subset."""

    filters_raw = payload.criteria.get("result_filters")
    if filters_raw is not None and not isinstance(filters_raw, dict):
        raise HTTPException(
            status_code=400,
            detail="Genie cohort result_filters must be a reviewed object",
        )
    filters = filters_raw if isinstance(filters_raw, dict) else {}
    out: dict[str, Any] = {}
    source = str(payload.criteria.get("source") or "genie")

    zips = _list_filter(
        filters.get("zips"),
        field="zips",
        max_items=_MAX_ACTION_FILTER_VALUES,
        pattern=re.compile(r"^\d{5}$"),
    )
    if zips:
        out["zips"] = zips
    city_states = _city_states_filter(filters.get(GENIE_CITY_FILTER_KEY))
    if city_states:
        out[GENIE_CITY_FILTER_KEY] = city_states
    county_raw = str(filters.get("county") or "").strip()
    if county_raw:
        if not re.fullmatch(r"^\d{5}$", county_raw):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid county filter",
            )
        out["county"] = county_raw
    counties = _list_filter(
        filters.get("counties"),
        field="counties",
        max_items=_MAX_ACTION_FILTER_VALUES,
        pattern=re.compile(r"^\d{5}$"),
    )
    if counties:
        out["counties"] = counties
    states = _list_filter(
        filters.get("states"),
        field="states",
        max_items=_MAX_ACTION_STATE_VALUES,
        pattern=re.compile(r"^[A-Za-z]{2}$"),
    )
    if states:
        out["states"] = states
    segment_codes = _list_filter(
        filters.get("segment_codes"),
        field="segment_codes",
        max_items=6,
        pattern=_GENIE_SEGMENT_CODE_RE,
    )
    if segment_codes:
        out["segment_codes"] = [s.lower() for s in segment_codes]
        mode = str(filters.get("segment_mode") or "any").lower()
        if mode not in {"any", "all"}:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes invalid segment mode",
            )
        out["segment_mode"] = mode
    target_lender_ref = str(filters.get("target_lender_ref") or "").strip()
    if target_lender_ref:
        try:
            target_lender_ref = (
                normalize_public_lender_ref(
                    target_lender_ref,
                    allow_all=True,
                )
                or ""
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unsafe lender alias",
            ) from exc
    if target_lender_ref and target_lender_ref != "All":
        out["target_lender_ref"] = target_lender_ref

    portfolio_raw = filters.get("portfolio_criteria")
    if portfolio_raw is None:
        portfolio_raw = payload.criteria.get("portfolio_criteria")
    if portfolio_raw is not None and not isinstance(portfolio_raw, dict):
        raise HTTPException(
            status_code=400,
            detail="Genie cohort includes unreviewed portfolio criteria",
        )
    if isinstance(portfolio_raw, dict):
        try:
            portfolio_model = PortfolioCriteria(**portfolio_raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unreviewed portfolio criteria",
            ) from exc
        if portfolio_model.marketing_eligibility not in {None, "Eligible only"}:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unsupported marketing eligibility filter",
            )
        if portfolio_model.consent_status not in {None, "Any"}:
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unsupported consent filter",
            )
        if not portfolio_model.has_effective_predicate(count_default_marketing=False):
            raise HTTPException(
                status_code=400,
                detail="Genie cohort includes unreviewed portfolio criteria",
            )
        portfolio_criteria = portfolio_model.model_dump(exclude_none=True)
        portfolio_criteria["marketing_eligibility"] = "Eligible only"
        portfolio_criteria.pop("consent_status", None)
        if portfolio_criteria:
            out["portfolio_criteria"] = portfolio_criteria

    # Reviewed numeric floors. Measured live 2026-08-11 against paychex gold:
    # "in-the-money borrowers in IL" is 1,766 and the queue replaying
    # segment_codes=[itm] + states=[IL] matched it exactly, but the same
    # answer narrowed to opportunity_score >= 80 is 32 and replayed as 1,766
    # (55x) because the reviewed subset was geography/segment/lender only.
    # These three keys carry the threshold through to the same `>=` predicate
    # the answer used, so the handoff reproduces the answer's population
    # instead of a broader one under the same heading.
    for field, (minimum, maximum) in _REPLAYABLE_NUMERIC_FILTERS.items():
        floor = _numeric_floor(
            filters.get(field), field=field, minimum=minimum, maximum=maximum
        )
        if floor is not None:
            out[field] = floor

    if payload_borrower_ids:
        out["borrower_ids"] = payload_borrower_ids
    if out and source in {"genie", "trusted_sql"}:
        out["source"] = source
    # Name whatever predicates remain outside the reviewed vocabulary (LTV
    # bands, propensity cuts, anything a future Genie answer invents). The
    # queue cannot apply them, so /leads reconciles its count against the
    # stated one and says which predicates went missing rather than
    # presenting a different population under the same question.
    if out:
        unreplayable = sorted(
            {
                disclosed
                for key in filters
                if key not in _REPLAYABLE_FILTER_KEYS
                and (disclosed := _disclosed_filter_key(key))
            }
        )
        if unreplayable:
            out["unreplayable_filters"] = unreplayable[:_MAX_UNREPLAYABLE_FILTER_KEYS]
    _assert_no_pii(out)
    _assert_allowlisted({"result_filters": out})
    return out
