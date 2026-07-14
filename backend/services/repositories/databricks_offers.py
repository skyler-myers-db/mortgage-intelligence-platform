"""Databricks-backed offer recommendation inputs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from backend.schemas.offer import (
    validate_offer_evidence_ids,
    validate_offer_source_refreshed_at,
)
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.repositories.databricks_shared import _coerce_bool
from backend.services.scoring import NBO_PRODUCT_LABELS


def _required_int(row: dict[str, object], field: str) -> int:
    value = row.get(field)
    if value is None or isinstance(value, bool):
        raise ValueError(f"offer input {field} is missing or invalid")
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"offer input {field} is missing or invalid") from exc
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise ValueError(f"offer input {field} is missing or invalid")
    return int(numeric)


def _required_text(row: dict[str, object], field: str) -> str:
    value = str(row.get(field) or "").strip()
    if not value:
        raise ValueError(f"offer input {field} is missing or invalid")
    return value


class DatabricksOfferRepository:
    """Read one atomic dossier row for offer recommendation and audit proof."""

    def __init__(self, client: DatabricksSqlClient) -> None:
        self._client = client

    _SQL = (
        "SELECT "
        "  clip, borrower_id, confidence, evidence_ids, refreshed_at, "
        "  rate_spread_bps, equity_pct, has_permit, has_heloc_propensity_trigger, "
        "  heloc_propensity_score, has_refi_propensity_trigger, refi_propensity_score, "
        "  listed_for_sale, "
        "  is_investor, is_current_customer, is_competitor_lien, "
        "  recommended_offer_code, min_spread_bps_applied, min_equity_pct_applied, "
        "  heloc_equity_min_applied, cashout_equity_min_applied, "
        "  retention_min_spread_applied "
        f"FROM {qualify('gold', 'borrower_dossier')} "
        "WHERE borrower_id = :borrower_id "
        "LIMIT 1"
    )

    def get_offer_inputs(self, borrower_id: str) -> dict[str, object] | None:
        row = self._client.execute_one(self._SQL, {"borrower_id": borrower_id})
        if row is None:
            return None
        code = _required_text(row, "recommended_offer_code")
        if code not in NBO_PRODUCT_LABELS:
            raise ValueError("offer input recommended_offer_code is invalid")
        return {
            "clip_id": _required_text(row, "clip"),
            "borrower_id": _required_text(row, "borrower_id"),
            "confidence": _required_int(row, "confidence"),
            "evidence_ids": validate_offer_evidence_ids(row.get("evidence_ids")),
            "source_refreshed_at": validate_offer_source_refreshed_at(row.get("refreshed_at")),
            "rate_spread_bps": _required_int(row, "rate_spread_bps"),
            "equity_pct": _required_int(row, "equity_pct"),
            "has_permit": _coerce_bool(row.get("has_permit")),
            "has_heloc_propensity_trigger": _coerce_bool(
                row.get("has_heloc_propensity_trigger")
            ),
            "heloc_propensity_score": _required_int(row, "heloc_propensity_score"),
            "has_refi_propensity_trigger": _coerce_bool(
                row.get("has_refi_propensity_trigger")
            ),
            "refi_propensity_score": _required_int(row, "refi_propensity_score"),
            "listed_for_sale": _coerce_bool(row.get("listed_for_sale")),
            "is_investor": _coerce_bool(row.get("is_investor")),
            "is_current_customer": _coerce_bool(row.get("is_current_customer")),
            "is_competitor_lien": _coerce_bool(row.get("is_competitor_lien")),
            "offer_code": code,
            "min_spread_bps": _required_int(row, "min_spread_bps_applied"),
            "min_equity_pct": _required_int(row, "min_equity_pct_applied"),
            "heloc_equity_min_pct": _required_int(row, "heloc_equity_min_applied"),
            "cashout_equity_min_pct": _required_int(row, "cashout_equity_min_applied"),
            "retention_min_spread_bps": _required_int(
                row, "retention_min_spread_applied"
            ),
        }
