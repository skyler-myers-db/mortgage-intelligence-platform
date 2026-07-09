"""Unity Catalog name-qualification helpers.

Multi-catalog customers deploy the app with a non-default ``uc_catalog``
(e.g. ``mip_prod``) and every query that hardcoded ``mip.gold.*`` would
silently resolve against the wrong catalog -- or 42601 on a clean
workspace.

This module centralises the ``{catalog}.{schema}.{table}`` construction
so Python callers only name ``(schema, table)`` pairs. The catalog is
resolved from ``backend.config.settings.settings.mip_default_catalog``
(env var ``MIP_DEFAULT_CATALOG``) at call time, so a single env-var flip
reroutes the whole app at boot.

Scope note: only the Python side is covered here. SQL files under
``sql/transformations/`` and ``sql/ddl/`` still hardcode ``mip.*`` --
that is a documented known-limitation (see
``docs/runbook-multi-catalog.md``). The Python API layer is the
mechanically safe slice to ship in isolation; SQL pre-processing is a
separate follow-up with its own risk surface.
"""
from __future__ import annotations

import re

from backend.config.settings import settings

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_ALLOWED_RELATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("first_party", "crm_campaign_membership"),
        ("first_party", "customer_interactions"),
        ("first_party", "loan_applications"),
        ("first_party", "product_balances"),
        ("first_party", "servicing_portfolio"),
        ("gold", "address_lookup"),
        ("gold", "borrower_360"),
        ("gold", "borrower_dossier"),
        ("gold", "borrower_lifecycle_state"),
        ("gold", "county_rollup"),
        ("gold", "evidence_events"),
        ("gold", "fn_build_cohort"),
        ("gold", "fn_bounded_mortgage_rate"),
        ("gold", "fn_estimated_upb"),
        ("gold", "fn_estimated_upb_confidence_band"),
        ("gold", "fn_in_the_money"),
        ("gold", "fn_lead_score"),
        ("gold", "fn_lead_queue_url"),
        ("gold", "fn_next_best_offer"),
        ("gold", "fn_rate_spread"),
        ("gold", "fn_segment_counts"),
        ("gold", "funnel_snapshot_daily"),
        ("gold", "lead_population"),
        ("gold", "lead_scores"),
        ("gold", "lockin_cohort"),
        ("gold", "segment_population"),
        ("gold", "source_readiness"),
        ("gold", "state_top_segment"),
        ("gold", "zip_rollup"),
        ("ref", "lender_dictionary"),
        ("ref", "offer_rules_config"),
        ("ref", "state_footprint"),
        ("semantics", "borrower_opportunity_metric_view"),
        ("semantics", "certified_borrower_opportunity_metric_view"),
        ("semantics", "certified_lead_generation_metric_view"),
        ("semantics", "certified_segment_performance_metric_view"),
        ("semantics", "lead_generation_metric_view"),
        ("semantics", "segment_performance_metric_view"),
        ("silver", "lien_current"),
        ("silver", "listing_activity"),
        ("silver", "heloc_propensity"),
        ("silver", "market_rates_weekly"),
        ("silver", "mortgage_events"),
        ("silver", "owner_property_bridge"),
        ("silver", "property_master"),
        ("silver", "refi_propensity"),
    }
)


def qualify(schema: str, table: str, *, catalog: str | None = None) -> str:
    """Return ``{catalog}.{schema}.{table}``.

    ``catalog`` defaults to ``settings.mip_default_catalog`` so callers
    only pass schema + table. Pass an explicit ``catalog`` to target a
    non-default workspace (e.g. cross-workspace read of a lender's own
    catalog).

    Each identifier part must be a plain Unity Catalog identifier and
    every schema/table pair must be one of the public Module 0 assets
    the app is allowed to query.
    """
    cat = catalog if catalog is not None else settings.mip_default_catalog
    _validate_identifier("catalog", cat)
    _validate_identifier("schema", schema)
    _validate_identifier("table", table)
    if (schema.lower(), table.lower()) not in _ALLOWED_RELATIONS:
        raise ValueError(f"Unknown Unity Catalog relation: {schema}.{table}")
    return f"{cat}.{schema}.{table}"


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid Unity Catalog {label} identifier: {value!r}")
