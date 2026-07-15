"""Reviewed campaign JSON input normalizers.

Campaign fields are persisted as JSONB for compatibility, but they are not an
untyped API surface. These helpers keep request validation and read-time public
projection on the same exact allowlists.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from typing import cast

from backend.schemas.portfolio_campaign import assert_public_campaign_json

OUTREACH_CHANNELS: frozenset[str] = frozenset({"email", "sms", "direct_mail"})
_SEND_WINDOW_DAYS = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
)
_SEND_WINDOW_DAY_ALIASES = {
    "mon": "Monday",
    "monday": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "tuesday": "Tuesday",
    "wed": "Wednesday",
    "weds": "Wednesday",
    "wednesday": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "thursday": "Thursday",
    "fri": "Friday",
    "friday": "Friday",
    "sat": "Saturday",
    "saturday": "Saturday",
    "sun": "Sunday",
    "sunday": "Sunday",
}
_SUPPRESSION_POLICY_KEYS = frozenset(
    {"default", "require_marketing_eligible", "marketing_eligibility", "frequency_cap_days"}
)
_ROI_ASSUMPTION_KEYS = frozenset(
    {
        "budget_usd",
        "expected_conversion_rate",
        "expected_conversion_rate_pct",
        "lo_capacity",
        "cost_per_contact_usd",
        "source",
    }
)
_ROI_SOURCE_LABELS = frozenset({"operator_configured", "operator_required_before_live_send"})


def require_exact_json_keys(
    value: dict[str, object],
    *,
    allowed: frozenset[str],
    field_name: str,
) -> None:
    if not set(value).issubset(allowed):
        raise ValueError(f"{field_name} contains unreviewed fields")


def _reviewed_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return int(value)
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    raise ValueError(f"{field_name} must be an integer")


def normalize_channel_cascade(value: list[dict[str, object]]) -> list[dict[str, object]]:
    if len(value) > 6:
        raise ValueError("channel_cascade supports at most 6 steps")
    seen_steps: set[int] = set()
    normalized: list[dict[str, object]] = []
    for raw in value:
        require_exact_json_keys(
            raw,
            allowed=frozenset({"channel", "step", "after_days"}),
            field_name="channel_cascade",
        )
        channel = str(raw.get("channel") or "").strip()
        if channel not in OUTREACH_CHANNELS:
            raise ValueError("channel cascade channel must be email, sms, or direct_mail")
        step_raw = raw.get("step") or 0
        after_days_raw = raw.get("after_days") or 0
        try:
            step = _reviewed_integer(step_raw, field_name="channel cascade step")
            after_days = _reviewed_integer(
                after_days_raw,
                field_name="channel cascade after_days",
            )
        except ValueError as exc:
            raise ValueError("channel cascade step and after_days must be integers") from exc
        if step <= 0 or step > 100 or step in seen_steps:
            raise ValueError("channel cascade steps must be bounded, positive, and unique")
        if after_days < 0 or after_days > 365:
            raise ValueError("channel cascade after_days must be between 0 and 365")
        seen_steps.add(step)
        normalized.append({"channel": channel, "step": step, "after_days": after_days})
    return sorted(normalized, key=lambda item: cast(int, item["step"]))


def normalize_send_window(value: dict[str, object]) -> dict[str, object]:
    if not value:
        return value
    require_exact_json_keys(
        value,
        allowed=frozenset({"days", "timezone", "start_local", "end_local", "start", "end"}),
        field_name="send_window",
    )
    if "start" in value and "start_local" in value:
        raise ValueError("send_window must use one reviewed start field")
    if "end" in value and "end_local" in value:
        raise ValueError("send_window must use one reviewed end field")
    days_raw = value.get("days") or []
    if not isinstance(days_raw, list):
        raise ValueError("send_window.days must be a list")
    days = [
        _SEND_WINDOW_DAY_ALIASES.get(str(raw).strip().lower(), str(raw).strip())
        for raw in days_raw
        if str(raw).strip()
    ]
    if (
        not days
        or len(days) > 7
        or len(set(days)) != len(days)
        or any(day not in _SEND_WINDOW_DAYS for day in days)
    ):
        raise ValueError("send_window.days must use unique reviewed day labels")
    start = str(value.get("start_local") or value.get("start") or "").strip()
    end = str(value.get("end_local") or value.get("end") or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", end
    ):
        raise ValueError("send_window start/end must be valid HH:MM times")
    if start >= end:
        raise ValueError("send_window start_local must be before end_local")
    timezone = str(value.get("timezone") or "borrower_local").strip()
    if timezone != "borrower_local":
        raise ValueError("send_window timezone must be borrower_local")
    return {"days": days, "timezone": timezone, "start_local": start, "end_local": end}


def normalize_holdout(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    require_exact_json_keys(
        value,
        allowed=frozenset({"method", "size_pct"}),
        field_name="holdout",
    )
    method = str(value.get("method") or "hash_modulo").strip()
    if method != "hash_modulo":
        raise ValueError("holdout.method must be hash_modulo")
    size_pct_raw = value.get("size_pct", 0)
    if isinstance(size_pct_raw, bool) or not isinstance(size_pct_raw, int | float | str):
        raise ValueError("holdout.size_pct must be numeric")
    try:
        size_pct_decimal = Decimal(str(size_pct_raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("holdout.size_pct must be numeric") from exc
    if not size_pct_decimal.is_finite() or not Decimal(0) <= size_pct_decimal <= Decimal(50):
        raise ValueError("holdout.size_pct must be finite and between 0 and 50")
    if size_pct_decimal * 100 != (size_pct_decimal * 100).to_integral_value():
        raise ValueError("holdout.size_pct must use at most two decimal places")
    size_pct = float(size_pct_decimal)
    return {"method": method, "size_pct": size_pct}


def normalize_suppression_policy(value: dict[str, object]) -> dict[str, object]:
    assert_public_campaign_json(value, field_name="campaign suppression policy")
    require_exact_json_keys(
        value,
        allowed=_SUPPRESSION_POLICY_KEYS,
        field_name="suppression_policy",
    )
    normalized: dict[str, object] = {}
    if "default" in value:
        if value["default"] != "eligible_only":
            raise ValueError("suppression_policy.default must be eligible_only")
        normalized["default"] = "eligible_only"
    if "require_marketing_eligible" in value:
        required = value["require_marketing_eligible"]
        if not isinstance(required, bool):
            raise ValueError("suppression_policy.require_marketing_eligible must be boolean")
        normalized["require_marketing_eligible"] = required
    if "marketing_eligibility" in value:
        if value["marketing_eligibility"] != "Eligible only":
            raise ValueError("suppression_policy.marketing_eligibility must be Eligible only")
        normalized["marketing_eligibility"] = "Eligible only"
    if "frequency_cap_days" in value:
        raw_days = value["frequency_cap_days"]
        if isinstance(raw_days, bool):
            raise ValueError("suppression_policy.frequency_cap_days must be an integer")
        try:
            days = int(cast(int | str, raw_days))
        except (TypeError, ValueError) as exc:
            raise ValueError("suppression_policy.frequency_cap_days must be an integer") from exc
        if str(raw_days).strip() != str(days) or not 30 <= days <= 365:
            raise ValueError("suppression_policy.frequency_cap_days must be between 30 and 365")
        normalized["frequency_cap_days"] = days
    return normalized


def _finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = float(cast(float | int | str, value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def normalize_roi_assumptions(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    assert_public_campaign_json(value, field_name="campaign ROI assumptions")
    require_exact_json_keys(value, allowed=_ROI_ASSUMPTION_KEYS, field_name="roi_assumptions")
    normalized = dict(value)
    for key in {
        "budget_usd",
        "expected_conversion_rate",
        "expected_conversion_rate_pct",
        "lo_capacity",
    } & normalized.keys():
        if normalized[key] is None:
            normalized.pop(key)
            continue
        numeric = _finite_number(normalized[key], field_name=f"roi_assumptions.{key}")
        if numeric < 0:
            raise ValueError(f"roi_assumptions.{key} must be non-negative")
        if key in {"expected_conversion_rate", "expected_conversion_rate_pct"} and numeric > 100:
            raise ValueError(f"roi_assumptions.{key} must be 100 or less")
        normalized[key] = numeric
    cost = normalized.get("cost_per_contact_usd")
    if isinstance(cost, dict):
        require_exact_json_keys(
            cost,
            allowed=OUTREACH_CHANNELS,
            field_name="roi_assumptions.cost_per_contact_usd",
        )
        checked: dict[str, float] = {}
        for channel, amount in cost.items():
            if amount is None:
                continue
            numeric = _finite_number(
                amount,
                field_name=f"roi_assumptions.cost_per_contact_usd value for {channel}",
            )
            if numeric < 0:
                raise ValueError("roi_assumptions.cost_per_contact_usd values must be non-negative")
            checked[channel] = numeric
        normalized["cost_per_contact_usd"] = checked
    elif cost is None:
        normalized.pop("cost_per_contact_usd", None)
    else:
        numeric_cost = _finite_number(cost, field_name="roi_assumptions.cost_per_contact_usd")
        if numeric_cost < 0:
            raise ValueError("roi_assumptions.cost_per_contact_usd must be non-negative")
        normalized["cost_per_contact_usd"] = numeric_cost
    if "source" in normalized:
        source = str(normalized["source"] or "").strip()
        if source not in _ROI_SOURCE_LABELS:
            raise ValueError("roi_assumptions.source must use a reviewed source label")
        normalized["source"] = source
    return normalized
