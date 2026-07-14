"""Databricks-backed offer recommendation inputs."""

from __future__ import annotations

from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.databricks_sql_helpers import qualify
from backend.services.repositories.databricks_shared import _coerce_bool
from backend.services.scoring import NBO_PRODUCT_LABELS


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
        code = row.get("recommended_offer_code") or "nurture"
        if code not in NBO_PRODUCT_LABELS:
            code = "nurture"
        return {
            "clip_id": str(row["clip"]),
            "borrower_id": str(row["borrower_id"]),
            "confidence": int(row.get("confidence") or 0),
            "evidence_ids": [str(value) for value in (row.get("evidence_ids") or [])],
            "source_refreshed_at": str(row.get("refreshed_at") or "") or None,
            "rate_spread_bps": int(row.get("rate_spread_bps") or 0),
            "equity_pct": int(row.get("equity_pct") or 0),
            "has_permit": _coerce_bool(row.get("has_permit")),
            "has_heloc_propensity_trigger": _coerce_bool(
                row.get("has_heloc_propensity_trigger")
            ),
            "heloc_propensity_score": int(row.get("heloc_propensity_score") or 0),
            "has_refi_propensity_trigger": _coerce_bool(
                row.get("has_refi_propensity_trigger")
            ),
            "refi_propensity_score": int(row.get("refi_propensity_score") or 0),
            "listed_for_sale": _coerce_bool(row.get("listed_for_sale")),
            "is_investor": _coerce_bool(row.get("is_investor")),
            "is_current_customer": _coerce_bool(row.get("is_current_customer")),
            "is_competitor_lien": _coerce_bool(row.get("is_competitor_lien")),
            "offer_code": code,
            "min_spread_bps": int(row.get("min_spread_bps_applied") or 75),
            "min_equity_pct": int(row.get("min_equity_pct_applied") or 15),
            "heloc_equity_min_pct": int(row.get("heloc_equity_min_applied") or 35),
            "cashout_equity_min_pct": int(row.get("cashout_equity_min_applied") or 25),
            "retention_min_spread_bps": int(row.get("retention_min_spread_applied") or 50),
        }
