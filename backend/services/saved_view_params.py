"""The query-string grammar a saved Lead Queue view may hold (tables-09 phase 2).

A saved view is a URL the actor could already share through Copy link, so
its grammar is the Copy-link grammar (``leadQueueShareParams`` in
``frontend/src/routes/lead-queue.filters.ts``) minus the campaign binding:
the Lead Queue filters, the sort and direction, and the column preset.
Everything else is refused, not dropped -- the open row, an assignee email
(only ``assigned_to=me`` resolves per viewer), the Growth Agent proof keys,
``campaign_id`` / ``variant_name``, every ``include_*`` switch, ``limit`` and
any unknown key -- so what is stored is exactly what was reviewed.

Every value is checked with the helpers the Lead Queue itself uses, and a
refusal raises ONE fixed ``ValueError`` that never echoes a key or a value:
the text reaches a 422 body and a person typed it.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode

from fastapi import HTTPException
from pydantic import ValidationError

from backend.schemas._validators_tenant import normalize_public_lender_ref
from backend.schemas.lead import SEGMENT_CODE_VALUES
from backend.schemas.lead_query import SCORE_BOUND_RANGE, SPREAD_BOUND_RANGE
from backend.schemas.portfolio import PortfolioCriteria
from backend.schemas.usps import USPS_STATE_CODES
from backend.services.lead_query_helpers import (
    parse_borrower_ids,
    parse_city_states,
    parse_csv_filter,
    parse_segment_codes,
)

SAVED_VIEW_PARAMS_MAX_LENGTH = 2048
SAVED_VIEW_PARAMS_MAX_KEYS = 40
SAVED_VIEW_PARAMS_REFUSED = "params must be a Lead Queue view: its filters, sort and column preset only"

# The Portfolio vocabulary the queue replays (PORTFOLIO_FILTER_KEYS in
# lead-queue.filters.ts). target_lender_ref is one of them.
_PORTFOLIO_KEYS = frozenset(
    {
        "occupancy",
        "lien_status",
        "lender_relationship",
        "target_lender_ref",
        "product",
        "loan_product",
        "origination_channel",
        "min_equity_pct_label",
        "owner_link",
        "purchase_intent",
        "marketing_eligibility",
        "consent_status",
        "recency",
    }
)
_BOUND_RANGES: dict[str, tuple[int, int]] = {
    "min_opportunity_score": SCORE_BOUND_RANGE,
    "max_opportunity_score": SCORE_BOUND_RANGE,
    "min_rate_spread_bps": SPREAD_BOUND_RANGE,
    "max_rate_spread_bps": SPREAD_BOUND_RANGE,
}
_SORT_KEYS = frozenset({"relationship", "assignment", "outreach", "equity", "rate", "score", "confidence"})
_APPROVAL = frozenset({"pending", "approved", "rejected", "hold"})
_OUTREACH = frozenset({"none", "queued", "actioned", "sent", "bounced", "replied"})
_FUNNEL = frozenset(
    {"addressable", "in_the_money", "high_opportunity", "offer_recommended", "approved", "actioned"}
)
_SEGMENTS = frozenset(SEGMENT_CODE_VALUES)
_FIVE_DIGITS = re.compile(r"\d{5}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_INTEGER = re.compile(r"-?\d{1,5}")

#: Every key a saved view may carry.
SAVED_VIEW_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "segment",
        "segment_codes",
        "segment_mode",
        "state",
        "zip",
        "states",
        "zips",
        "cities",
        "county",
        "counties",
        "borrower_ids",
        "approval_status",
        "outreach_status",
        "assigned_to",
        "aged_days",
        "funnel_stage",
        "cohort_id",
        "sort",
        "dir",
        "view",
    }
    | _PORTFOLIO_KEYS
    | frozenset(_BOUND_RANGES)
)


def _refuse() -> ValueError:
    return ValueError(SAVED_VIEW_PARAMS_REFUSED)


def _check_scalar(key: str, value: str) -> None:
    if key == "segment":
        ok = value in _SEGMENTS
    elif key == "segment_mode":
        ok = value in {"any", "all"}
    elif key == "state":
        ok = value.upper() in USPS_STATE_CODES
    elif key in {"zip", "county"}:
        ok = _FIVE_DIGITS.fullmatch(value) is not None
    elif key == "approval_status":
        ok = value in _APPROVAL
    elif key == "outreach_status":
        ok = value in _OUTREACH
    elif key == "assigned_to":
        # The "Assigned to me" preset only: it resolves per viewer, so the
        # view never stores an email.
        ok = value.lower() == "me"
    elif key == "aged_days":
        ok = value.isdigit() and 1 <= int(value) <= 90
    elif key == "funnel_stage":
        ok = value in _FUNNEL
    elif key == "cohort_id":
        ok = _UUID.fullmatch(value) is not None
    elif key == "sort":
        ok = value in _SORT_KEYS
    elif key == "dir":
        ok = value in {"asc", "desc"}
    elif key == "view":
        ok = value == "sales-ops"
    elif key in _BOUND_RANGES:
        low, high = _BOUND_RANGES[key]
        ok = _INTEGER.fullmatch(value) is not None and low <= int(value) <= high
    else:
        ok = True
    if not ok:
        raise _refuse()


def _check_lists(pairs: dict[str, str]) -> None:
    """Multi-value keys, through the Lead Queue's own parsers (they raise 422)."""

    try:
        parse_segment_codes(pairs.get("segment_codes"))
        parse_csv_filter(pairs.get("states"), width=2, label="states")
        parse_csv_filter(pairs.get("zips"), width=5, label="zips", numeric=True)
        parse_csv_filter(pairs.get("counties"), width=5, label="counties", numeric=True)
        parse_city_states(pairs.get("cities"))
        parse_borrower_ids(pairs.get("borrower_ids"))
    except HTTPException as exc:
        raise _refuse() from exc
    for key in ("segment_codes", "states", "zips", "counties", "cities", "borrower_ids"):
        if key in pairs and not any(part.strip() for part in pairs[key].split(",")):
            raise _refuse()


def _check_portfolio(pairs: dict[str, str]) -> None:
    portfolio = {key: value for key, value in pairs.items() if key in _PORTFOLIO_KEYS}
    lender = portfolio.pop("target_lender_ref", None)
    if lender is not None:
        try:
            normalize_public_lender_ref(lender, allow_all=True)
        except ValueError as exc:
            raise _refuse() from exc
    if portfolio:
        try:
            PortfolioCriteria.model_validate(portfolio)
        except ValidationError as exc:
            raise _refuse() from exc


def _check_bounds(pairs: dict[str, str]) -> None:
    for dimension in ("opportunity_score", "rate_spread_bps"):
        low, high = pairs.get(f"min_{dimension}"), pairs.get(f"max_{dimension}")
        if low is not None and high is not None and int(low) > int(high):
            raise _refuse()
    # A Genie cohort replays its own thresholds; GET /leads refuses both.
    if "cohort_id" in pairs and any(key in pairs for key in _BOUND_RANGES):
        raise _refuse()


def canonical_saved_view_params(raw: str) -> tuple[str, str]:
    """Validate ``raw`` against the saved-view grammar.

    Returns the canonical query string (no leading ``?``, keys sorted, values
    trimmed) and its SHA-256 hex digest, the view's filter fingerprint.
    Raises ``ValueError(SAVED_VIEW_PARAMS_REFUSED)`` on anything else.
    """

    if not isinstance(raw, str):
        raise _refuse()
    text = raw.strip()
    text = text[1:] if text.startswith("?") else text
    if not text or len(text) > SAVED_VIEW_PARAMS_MAX_LENGTH:
        raise _refuse()
    try:
        entries = parse_qsl(text, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise _refuse() from exc
    if len(entries) > SAVED_VIEW_PARAMS_MAX_KEYS:
        raise _refuse()
    pairs: dict[str, str] = {}
    for key, value in entries:
        trimmed = value.strip()
        if key not in SAVED_VIEW_PARAM_KEYS or key in pairs or not trimmed:
            raise _refuse()
        _check_scalar(key, trimmed)
        pairs[key] = trimmed
    _check_lists(pairs)
    _check_portfolio(pairs)
    _check_bounds(pairs)
    canonical = urlencode(sorted(pairs.items()))
    if len(canonical) > SAVED_VIEW_PARAMS_MAX_LENGTH:
        raise _refuse()
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()
