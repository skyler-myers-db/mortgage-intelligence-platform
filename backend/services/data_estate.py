"""Build source-readiness and data-estate disclosure payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote

from backend.config.settings import settings
from backend.schemas.data_estate import DataEstateAsset, DataEstateLane, DataEstateResponse
from backend.services.admin_rules import SourceRow
from backend.services.asset_metadata_utils import workspace_origin
from backend.services.databricks_sql_helpers import qualify


def _catalog_explorer_url(uc_object: str | None) -> str | None:
    if not uc_object or uc_object.endswith(".*"):
        return None
    parts = uc_object.split(".")
    if len(parts) != 3:
        return None
    host = workspace_origin(settings.databricks_host)
    if host is None:
        return None
    catalog, schema_name, object_name = parts
    return (
        f"{host.rstrip('/')}/explore/data/"
        f"{quote(catalog)}/{quote(schema_name)}/{quote(object_name)}"
    )


def _status_rank(status: str) -> int:
    if status == "live":
        return 0
    if status in {"demo_synthetic", "configured_empty"}:
        return 1
    if status in {"not_configured", "roadmap"}:
        return 2
    return 3


def _lane_status(assets: list[DataEstateAsset]) -> str:
    if not assets:
        return "not_configured"
    statuses = [asset.status for asset in assets]
    if all(status == "live" for status in statuses):
        return "live"
    return sorted(statuses, key=_status_rank)[-1]


def _source_asset(
    row: SourceRow | None,
    *,
    name: str,
    label: str,
    uc_object: str | None,
    fallback_status: str = "not_configured",
    fallback_note: str = "",
) -> DataEstateAsset:
    if row is None:
        return DataEstateAsset(
            name=name,
            label=label,
            status=fallback_status,  # type: ignore[arg-type]
            uc_object=uc_object,
            catalog_explorer_url=_catalog_explorer_url(uc_object),
            note=fallback_note,
            synthetic_demo=False,
        )
    status = str(row.status or fallback_status)
    return DataEstateAsset(
        name=row.name,
        label=label,
        status=status,  # type: ignore[arg-type]
        uc_object=uc_object,
        catalog_explorer_url=_catalog_explorer_url(uc_object),
        row_count=row.rows,
        last_updated=row.last_updated,
        note=row.note,
        synthetic_demo=row.synthetic_demo,
    )


def _runtime_asset(
    runtime_statuses: dict[str, bool] | None,
    *,
    key: str,
    name: str,
    label: str,
    uc_object: str | None,
    live_note: str,
    down_note: str,
) -> DataEstateAsset:
    if runtime_statuses is None or key not in runtime_statuses:
        return DataEstateAsset(
            name=name,
            label=label,
            status="not_configured",
            uc_object=uc_object,
            catalog_explorer_url=_catalog_explorer_url(uc_object),
            note="Runtime health was not checked for this proof response.",
            synthetic_demo=False,
        )
    is_up = bool(runtime_statuses[key])
    return DataEstateAsset(
        name=name,
        label=label,
        status="live" if is_up else "error",
        uc_object=uc_object,
        catalog_explorer_url=_catalog_explorer_url(uc_object),
        note=live_note if is_up else down_note,
        synthetic_demo=False,
    )


def build_data_estate_response(
    source_rows: tuple[SourceRow, ...],
    *,
    runtime_statuses: dict[str, bool] | None = None,
) -> DataEstateResponse:
    """Build the Apr-30 data-estate proof surface.

    This is intentionally a metadata surface, not another data source. It
    reads the same non-PII source-readiness rows the Admin panel consumes and
    groups them into the four lanes Databricks/Cotality/Entrada asked to see:
    lender first-party inputs, Cotality enrichment, Databricks governed assets,
    and Entrada transformations. Empty first-party tables are represented as
    not_configured/configured_empty, never as live synthetic enrichment.
    """

    by_name: dict[str, SourceRow] = {row.name: row for row in source_rows}
    first_party_assets = [
        _source_asset(
            by_name.get("First-party LOS / Applications"),
            name="First-party LOS / Applications",
            label="Loan origination and application events",
            uc_object=qualify("first_party", "loan_applications", catalog="mip"),
            fallback_note="Customer LOS/application feed has not been connected.",
        ),
        _source_asset(
            by_name.get("First-party Servicing Portfolio"),
            name="First-party Servicing Portfolio",
            label="Current servicing book",
            uc_object=qualify("first_party", "servicing_portfolio", catalog="mip"),
            fallback_note="Customer servicing feed has not been connected.",
        ),
        _source_asset(
            by_name.get("First-party CRM / Campaigns"),
            name="First-party CRM / Campaigns",
            label="Campaign history and suppression state",
            uc_object=qualify("first_party", "crm_campaign_membership", catalog="mip"),
            fallback_note="Customer CRM/campaign feed has not been connected.",
        ),
        _source_asset(
            by_name.get("First-party Customer Interactions"),
            name="First-party Customer Interactions",
            label="Call center and digital engagement",
            uc_object=qualify("first_party", "customer_interactions", catalog="mip"),
            fallback_note="Customer interaction feed has not been connected.",
        ),
        _source_asset(
            by_name.get("First-party Product Balances"),
            name="First-party Product Balances",
            label="Deposit, card, and banking balances",
            uc_object=qualify("first_party", "product_balances", catalog="mip"),
            fallback_note="Customer product-balance feed has not been connected.",
        ),
    ]
    cotality_assets = [
        _source_asset(by_name.get("Cotality Public Records"), name="Cotality Public Records", label="Property master", uc_object=qualify("silver", "property_master", catalog="mip")),
        _source_asset(by_name.get("Voluntary Lien"), name="Voluntary Lien", label="Current lien and rate stack", uc_object=qualify("silver", "lien_current", catalog="mip")),
        _source_asset(by_name.get("MMA Mortgage Analytics"), name="MMA Mortgage Analytics", label="Mortgage event history", uc_object=qualify("silver", "mortgage_events", catalog="mip")),
        _source_asset(by_name.get("CLIP"), name="CLIP", label="Mastered property identifier", uc_object=qualify("silver", "property_master", catalog="mip")),
        _source_asset(by_name.get("Owner Link"), name="Owner Link", label="Owner-property graph", uc_object=qualify("silver", "owner_property_bridge", catalog="mip")),
        _source_asset(by_name.get("AVM"), name="AVM", label="Valuation and equity", uc_object=qualify("silver", "lien_current", catalog="mip")),
        _source_asset(by_name.get("FRED Market Rates"), name="FRED Market Rates", label="MORTGAGE30US market rate", uc_object=qualify("silver", "market_rates_weekly", catalog="mip")),
        _source_asset(
            by_name.get("MLS Listings"),
            name="MLS Listings",
            label="Listings overlay",
            uc_object=qualify("silver", "listing_activity", catalog="mip"),
            fallback_status="roadmap",
        ),
        _source_asset(
            by_name.get("Cotality HELOC Propensity"),
            name="Cotality HELOC Propensity",
            label="HELOC-intent overlay",
            uc_object=qualify("silver", "heloc_propensity", catalog="mip"),
            fallback_status="roadmap",
        ),
        _source_asset(
            by_name.get("Cotality Refi Propensity"),
            name="Cotality Refi Propensity",
            label="Refi propensity overlay",
            uc_object=qualify("silver", "refi_propensity", catalog="mip"),
            fallback_status="roadmap",
        ),
        _source_asset(by_name.get("Building Permits"), name="Building Permits", label="Permit overlay", uc_object=None, fallback_status="roadmap"),
    ]
    databricks_assets = [
        _source_asset(
            by_name.get("UC Gold Borrower 360"),
            name="UC Gold Borrower 360",
            label="Borrower 360 governed table",
            uc_object=qualify("gold", "borrower_360", catalog="mip"),
            fallback_status="not_configured",
            fallback_note="Gold borrower profile refresh has not been validated.",
        ),
        _source_asset(
            by_name.get("UC Gold Lead Population"),
            name="UC Gold Lead Population",
            label="Ranked lead queue table",
            uc_object=qualify("gold", "lead_population", catalog="mip"),
            fallback_status="not_configured",
            fallback_note="Gold lead queue refresh has not been validated.",
        ),
        _runtime_asset(
            runtime_statuses,
            key="genie",
            name="Databricks Genie",
            label="Mortgage Lead Intelligence space",
            uc_object=qualify("semantics", "lead_generation_metric_view", catalog="mip"),
            live_note="Genie space health check succeeded.",
            down_note="Genie space health check failed; answers should degrade.",
        ),
        _runtime_asset(
            runtime_statuses,
            key="lakebase",
            name="Lakebase",
            label="Saved leads, drafts, cohorts, and audit",
            uc_object="mip_app.*",
            live_note="Lakebase health check succeeded.",
            down_note="Lakebase health check failed; state-changing actions must fail closed.",
        ),
    ]
    entrada_assets = [
        _source_asset(
            by_name.get("UC Gold Lead Scores"),
            name="Entrada scoring primitives",
            label="Opportunity score and primary-offer SQL functions",
            uc_object=qualify("gold", "lead_scores", catalog="mip"),
            fallback_status="not_configured",
            fallback_note="Scoring table refresh has not been validated.",
        ),
        _source_asset(
            by_name.get("UC Gold Borrower Dossier"),
            name="Entrada app workflow",
            label="Build, segment, rank, explain, approve, audit",
            uc_object=qualify("gold", "borrower_dossier", catalog="mip"),
            fallback_status="not_configured",
            fallback_note="Borrower workflow pre-join has not been validated.",
        ),
        _source_asset(
            by_name.get("UC Gold Segment Population"),
            name="Entrada segment rollups",
            label="Segment cards and geography breakdowns",
            uc_object=qualify("gold", "segment_population", catalog="mip"),
            fallback_status="not_configured",
            fallback_note="Segment rollup refresh has not been validated.",
        ),
    ]

    lanes = [
        DataEstateLane(
            id="first_party",
            title="First-party lender data",
            description="Customer-owned LOS, servicing, CRM, interaction, and product-balance feeds. These refine targeting only when connected.",
            status=_lane_status(first_party_assets),  # type: ignore[arg-type]
            assets=first_party_assets,
        ),
        DataEstateLane(
            id="cotality",
            title="Cotality and market enrichment",
            description="Public-record property, lien, valuation, CLIP, Owner Link, and public market-rate signals used by Module 0 today.",
            status=_lane_status(cotality_assets),  # type: ignore[arg-type]
            assets=cotality_assets,
        ),
        DataEstateLane(
            id="databricks",
            title="Databricks governance layer",
            description="Unity Catalog, semantic views, Genie, Lakebase state, and auditability.",
            status=_lane_status(databricks_assets),  # type: ignore[arg-type]
            assets=databricks_assets,
        ),
        DataEstateLane(
            id="entrada",
            title="Entrada transformations",
            description="Mortgage-specific joins, scoring functions, offer logic, redaction, and app workflow orchestration.",
            status=_lane_status(entrada_assets),  # type: ignore[arg-type]
            assets=entrada_assets,
        ),
    ]
    gaps: list[str] = []
    if any(asset.synthetic_demo for asset in first_party_assets):
        gaps.append("First-party lender feeds use demo/synthetic rows in this workspace.")
    if any(asset.status not in {"live", "demo_synthetic"} for asset in first_party_assets):
        gaps.append("Customer first-party data feeds are not connected in this demo workspace.")
    if (
        by_name.get("MLS Listings") is None
        or by_name.get("MLS Listings", SourceRow("MLS Listings", "roadmap", None, None, "")).status != "live"
    ):
        gaps.append("Cotality MLS/Listings Delta Share is pending.")
    if by_name.get("Building Permits") is None or by_name.get("Building Permits", SourceRow("Building Permits", "roadmap", None, None, "")).status != "live":
        gaps.append("Cotality Building Permits Delta Share is pending.")
    if any(asset.status != "live" for asset in databricks_assets + entrada_assets):
        gaps.append("Deployment/runtime proof is incomplete until gold refresh, Genie, and Lakebase checks are live.")
    proof_assets = [
        asset.uc_object
        for lane in lanes
        for asset in lane.assets
        if asset.uc_object
    ]
    return DataEstateResponse(
        generated_at=datetime.now(UTC),
        lender_name=settings.mip_lender_name,
        public_demo_masking=True,
        lanes=lanes,
        known_data_gaps=gaps,
        proof_assets=proof_assets,
    )
