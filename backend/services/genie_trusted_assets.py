"""Trusted Unity Catalog assets exposed to Genie responses and action proofs."""
from __future__ import annotations

from backend.services.databricks_sql_helpers import qualify

_TRUSTED_ASSET_PAIRS = (
    ("gold", "lead_population"),
    ("gold", "segment_population"),
    ("gold", "lead_scores"),
    ("gold", "borrower_360"),
    ("gold", "borrower_dossier"),
    ("gold", "evidence_events"),
    ("gold", "source_readiness"),
    ("gold", "lockin_cohort"),
    ("gold", "funnel_snapshot_daily"),
    ("gold", "county_rollup"),
    ("gold", "zip_rollup"),
    ("semantics", "lead_generation_metric_view"),
    ("semantics", "segment_performance_metric_view"),
    ("semantics", "borrower_opportunity_metric_view"),
)


def trusted_assets() -> list[str]:
    assets = [qualify(schema, table) for schema, table in _TRUSTED_ASSET_PAIRS]
    for schema, table in _TRUSTED_ASSET_PAIRS:
        asset = qualify(schema, table, catalog="mip")
        if asset not in assets:
            assets.append(asset)
    return assets
