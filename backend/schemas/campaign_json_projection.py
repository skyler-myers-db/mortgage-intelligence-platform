"""Fail-closed public projection for persisted campaign JSONB fields."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Literal, cast

from backend.schemas._validators import normalize_public_lender_ref, reviewed_state_codes
from backend.schemas.campaign_json_inputs import (
    normalize_channel_cascade,
    normalize_holdout,
    normalize_roi_assumptions,
    normalize_send_window,
    normalize_suppression_policy,
    require_exact_json_keys,
)
from backend.schemas.portfolio_campaign import (
    assert_public_campaign_json,
    assert_public_campaign_text,
)

CampaignPublicJsonField = Literal[
    "criteria",
    "suppression_policy",
    "channel_cascade",
    "send_window",
    "holdout",
    "roi_assumptions",
]
PortfolioProjector = Callable[[dict[str, object]], dict[str, object]]

_LEGACY_CRITERIA_KEYS = frozenset(
    {
        "segment",
        "min_spread_bps",
        "min_equity_pct",
        "heloc_equity_min_pct",
        "heloc_propensity_min",
        "intent_signal",
        "filed_permits",
        "states",
        "marketing_eligibility",
        "consent_status",
        "recency",
    }
)
_GENIE_CRITERIA_KEYS = frozenset(
    {
        "source",
        "borrower_ids",
        "criteria_hash",
        "criteria_keys",
        "source_assets",
        "visualization_kind",
        "conversation_id",
        "message_id",
        "question_hash",
        "row_count",
        "route",
        "result_filters",
        "sql_hash",
    }
)
_GENIE_REPLAY_FILTER_KEYS = frozenset(
    {
        "zips",
        "county",
        "counties",
        "states",
        "segment_codes",
        "segment_mode",
        "target_lender_ref",
        "portfolio_criteria",
        "borrower_ids",
        "source",
    }
)
_GENIE_SEGMENT_CODES = frozenset({"itm", "listed", "permit", "investor", "equity", "retention"})
_GENIE_VISUALIZATION_KINDS = frozenset({"bar", "line", "metric", "pie", "scatter", "table"})


def _bounded_public_number(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float | int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = float(cast(float | int | str, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{field_name} must be within its reviewed bounds")
    return int(number) if number.is_integer() else number


def _reviewed_text_list(
    value: object,
    *,
    field_name: str,
    pattern: str,
    max_items: int,
) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"{field_name} must be a bounded reviewed list")
    out: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if not re.fullmatch(pattern, text):
            raise ValueError(f"{field_name} contains an unreviewed value")
        if text not in out:
            out.append(text)
    return out


def _project_legacy_criteria(
    value: dict[str, object],
    *,
    portfolio_fields: frozenset[str],
    portfolio_projector: PortfolioProjector,
) -> dict[str, object]:
    require_exact_json_keys(value, allowed=_LEGACY_CRITERIA_KEYS, field_name="criteria")
    segment = str(value.get("segment") or "").strip()
    if segment not in {"itm", "cashout", "heloc"}:
        raise ValueError("criteria.segment must use a reviewed legacy segment")
    shared = portfolio_projector({key: value[key] for key in set(value) & portfolio_fields})
    out: dict[str, object] = {"segment": segment, **shared}
    for key, (minimum, maximum) in {
        "min_spread_bps": (0.0, 2_000.0),
        "heloc_equity_min_pct": (0.0, 100.0),
        "heloc_propensity_min": (0.0, 1_000.0),
    }.items():
        if key in value:
            out[key] = _bounded_public_number(
                value[key],
                field_name=f"criteria.{key}",
                minimum=minimum,
                maximum=maximum,
            )
    if "intent_signal" in value:
        if value["intent_signal"] != "cotality_heloc_propensity":
            raise ValueError("criteria.intent_signal must use the reviewed signal")
        out["intent_signal"] = "cotality_heloc_propensity"
    if "filed_permits" in value:
        if value["filed_permits"] != "pending_not_inferred":
            raise ValueError("criteria.filed_permits must preserve the reviewed carveout")
        out["filed_permits"] = "pending_not_inferred"
    return out


def _project_genie_replay_filters(
    value: object,
    *,
    portfolio_projector: PortfolioProjector,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("criteria.result_filters must be a reviewed object")
    require_exact_json_keys(
        value,
        allowed=_GENIE_REPLAY_FILTER_KEYS,
        field_name="criteria.result_filters",
    )
    out: dict[str, object] = {}
    for key in ("zips", "counties"):
        if key in value:
            out[key] = _reviewed_text_list(
                value[key],
                field_name=f"criteria.result_filters.{key}",
                pattern=r"\d{5}",
                max_items=500,
            )
    if "county" in value:
        county = str(value["county"] or "").strip()
        if not re.fullmatch(r"\d{5}", county):
            raise ValueError("criteria.result_filters.county must be a reviewed FIPS code")
        out["county"] = county
    if "states" in value:
        states = [
            state.upper()
            for state in _reviewed_text_list(
                value["states"],
                field_name="criteria.result_filters.states",
                pattern=r"[A-Za-z]{2}",
                max_items=56,
            )
        ]
        allowed_states = reviewed_state_codes()
        if allowed_states and any(state not in allowed_states for state in states):
            raise ValueError("criteria.result_filters.states must use reviewed state codes")
        out["states"] = states
    if "segment_codes" in value:
        segment_codes = [
            segment.lower()
            for segment in _reviewed_text_list(
                value["segment_codes"],
                field_name="criteria.result_filters.segment_codes",
                pattern=r"[A-Za-z_]+",
                max_items=6,
            )
        ]
        if any(segment not in _GENIE_SEGMENT_CODES for segment in segment_codes):
            raise ValueError("criteria.result_filters.segment_codes are unreviewed")
        out["segment_codes"] = segment_codes
    if "segment_mode" in value:
        mode = str(value["segment_mode"] or "").strip().lower()
        if mode not in {"all", "any"}:
            raise ValueError("criteria.result_filters.segment_mode must be all or any")
        out["segment_mode"] = mode
    if "target_lender_ref" in value:
        lender_ref = normalize_public_lender_ref(
            str(value["target_lender_ref"] or ""),
            allow_all=True,
        )
        if lender_ref:
            out["target_lender_ref"] = lender_ref
    if "portfolio_criteria" in value:
        portfolio_value = value["portfolio_criteria"]
        if not isinstance(portfolio_value, dict):
            raise ValueError("criteria.result_filters.portfolio_criteria must be an object")
        out["portfolio_criteria"] = portfolio_projector(portfolio_value)
    if "borrower_ids" in value:
        out["borrower_ids"] = _reviewed_text_list(
            value["borrower_ids"],
            field_name="criteria.result_filters.borrower_ids",
            pattern=r"B-[0-9A-Z]{13}",
            max_items=500,
        )
    if "source" in value:
        source = str(value["source"] or "").strip()
        if source not in {"genie", "trusted_sql"}:
            raise ValueError("criteria.result_filters.source must be reviewed")
        out["source"] = source
    return out


def _project_genie_criteria(
    value: dict[str, object],
    *,
    portfolio_projector: PortfolioProjector,
) -> dict[str, object]:
    require_exact_json_keys(value, allowed=_GENIE_CRITERIA_KEYS, field_name="criteria")
    source = str(value.get("source") or "").strip()
    if source not in {"genie", "trusted_sql"}:
        raise ValueError("criteria.source must use a reviewed Genie source")
    out: dict[str, object] = {"source": source}
    if "borrower_ids" in value:
        out["borrower_ids"] = _reviewed_text_list(
            value["borrower_ids"],
            field_name="criteria.borrower_ids",
            pattern=r"B-[0-9A-Z]{13}",
            max_items=500,
        )
    if "result_filters" in value:
        out["result_filters"] = _project_genie_replay_filters(
            value["result_filters"],
            portfolio_projector=portfolio_projector,
        )
    if value.get("route") is not None:
        route = assert_public_campaign_text(
            value["route"],
            field_name="criteria route",
            max_length=2000,
        )
        if route != "/lead-queue" and not route.startswith("/lead-queue?"):
            raise ValueError("criteria.route must target the reviewed lead queue")
        out["route"] = route
    if "row_count" in value:
        row_count = value["row_count"]
        if isinstance(row_count, bool):
            raise ValueError("criteria.row_count must be a bounded integer")
        try:
            count = int(cast(int | str, row_count))
        except (TypeError, ValueError) as exc:
            raise ValueError("criteria.row_count must be a bounded integer") from exc
        if str(row_count).strip() != str(count) or not 0 <= count <= 10_000_000:
            raise ValueError("criteria.row_count must be a bounded integer")
        out["row_count"] = count
    for field_name in ("criteria_hash", "sql_hash", "question_hash"):
        if field_name in value and value[field_name] is not None:
            digest = str(value[field_name]).strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", digest):
                raise ValueError(f"criteria.{field_name} must be a bounded digest")
    for field_name in ("conversation_id", "message_id"):
        if field_name in value and value[field_name] is not None:
            assert_public_campaign_text(
                value[field_name],
                field_name=f"criteria {field_name}",
                max_length=256,
            )
    if "criteria_keys" in value:
        _reviewed_text_list(
            value["criteria_keys"],
            field_name="criteria.criteria_keys",
            pattern=r"[a-z][a-z0-9_]{0,63}",
            max_items=50,
        )
    if "source_assets" in value:
        _reviewed_text_list(
            value["source_assets"],
            field_name="criteria.source_assets",
            pattern=r"[A-Za-z0-9_.]{1,160}",
            max_items=10,
        )
    if value.get("visualization_kind") is not None:
        kind = str(value["visualization_kind"]).strip()
        if kind not in _GENIE_VISUALIZATION_KINDS:
            raise ValueError("criteria.visualization_kind must be reviewed")
    if not out.get("borrower_ids") and not out.get("result_filters"):
        raise ValueError("criteria must include a reviewed replay target")
    return out


def project_public_campaign_json_field(
    field_name: CampaignPublicJsonField,
    value: object,
    *,
    portfolio_fields: frozenset[str],
    portfolio_projector: PortfolioProjector,
) -> dict[str, object] | list[dict[str, object]] | None:
    """Return the exact public projection for one persisted campaign JSON field."""
    assert_public_campaign_json(value, field_name=f"campaign {field_name}")
    if field_name == "criteria":
        if not isinstance(value, dict):
            raise ValueError("campaign criteria must be a reviewed object")
        keys = set(value)
        if keys.issubset(portfolio_fields):
            return portfolio_projector(value)
        if "segment" in keys:
            return _project_legacy_criteria(
                value,
                portfolio_fields=portfolio_fields,
                portfolio_projector=portfolio_projector,
            )
        if "source" in keys:
            return _project_genie_criteria(value, portfolio_projector=portfolio_projector)
        raise ValueError("campaign criteria has no reviewed shape")
    if field_name == "suppression_policy":
        if not isinstance(value, dict):
            raise ValueError("campaign suppression_policy must be a reviewed object")
        return normalize_suppression_policy(value)
    if field_name == "channel_cascade":
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError("campaign channel_cascade must be a reviewed list")
        return normalize_channel_cascade(cast(list[dict[str, object]], value))
    if field_name == "send_window":
        if not isinstance(value, dict):
            raise ValueError("campaign send_window must be a reviewed object")
        return normalize_send_window(value)
    if field_name == "holdout":
        if value is not None and not isinstance(value, dict):
            raise ValueError("campaign holdout must be a reviewed object or null")
        return normalize_holdout(cast(dict[str, object] | None, value))
    if field_name == "roi_assumptions":
        if value is not None and not isinstance(value, dict):
            raise ValueError("campaign roi_assumptions must be a reviewed object or null")
        return normalize_roi_assumptions(cast(dict[str, object] | None, value))
    raise AssertionError("unhandled campaign public JSON field")
