"""Observed lineage on the asset-detail surface reads the deploy-managed view.

The App holds no grant on ``system.access.table_lineage`` (lineage for every
table on a shared metastore). It reads ``<catalog>.audit.asset_lineage``,
the four-column view the deploy publishes and grants (SELECT on that one view),
through the same ``qualify`` catalog source every gold/audit read uses, with
the asset bound as a parameter. A failed read, including an unpublished view,
stays the explicit "Observed lineage unavailable" gap, and every other
metadata section still loads.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.config.settings import settings
from backend.services import asset_metadata
from backend.services.asset_metadata import AssetMetadataService

EDGE_ROWS = [
    {
        "source_table_full_name": "{catalog}.gold.borrower_360",
        "target_table_full_name": "{catalog}.gold.lead_population",
        "event_time": "2026-10-27 08:00:00",
        "event_count": 3,
    },
    {
        "source_table_full_name": "{catalog}.gold.lead_population",
        "target_table_full_name": "other_team.reports.weekly",
        "event_time": "2026-10-26 08:00:00",
        "event_count": 1,
    },
]


class _LineageSqlClient:
    """Answers the lineage read; every other metadata read is empty."""

    def __init__(self, catalog: str, *, lineage_error: Exception | None = None) -> None:
        self.catalog = catalog
        self.lineage_error = lineage_error
        self.calls: list[tuple[str, Any]] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.calls.append((statement, parameters))
        if "ASSET_LINEAGE" in statement.upper():
            if self.lineage_error is not None:
                raise self.lineage_error
            return [
                {key: value.format(catalog=self.catalog) if isinstance(value, str) else value
                 for key, value in row.items()}
                for row in EDGE_ROWS
            ]
        return []


def _lineage_calls(fake: _LineageSqlClient) -> list[tuple[str, Any]]:
    return [(s, p) for s, p in fake.calls if "LINEAGE" in s.upper()]


@pytest.mark.parametrize("catalog", ["mip", "summit_mortgage"])
def test_lineage_reads_the_install_catalog_view_with_a_bound_asset(
    monkeypatch: pytest.MonkeyPatch, catalog: str
) -> None:
    monkeypatch.setattr(settings, "mip_default_catalog", catalog)
    # The registry map is built at import from the configured catalog; rebuild
    # it the way a process started with this catalog would have.
    monkeypatch.setattr(asset_metadata, "_DESCRIPTOR_MAP", asset_metadata._build_descriptor_map())
    fake = _LineageSqlClient(catalog)

    payload = AssetMetadataService(fake).get_asset("lead_population")

    [(statement, params)] = _lineage_calls(fake)
    assert f"FROM {catalog}.audit.asset_lineage " in statement
    assert "system." not in statement.lower()
    assert params == {"asset": f"{catalog}.gold.lead_population"}
    assert "(source_table_full_name = :asset OR target_table_full_name = :asset)" in statement
    assert "lead_population" not in statement  # the asset is bound, never spliced
    assert "AND event_date >= current_date() - INTERVAL 90 DAYS " in statement
    assert [node.asset_path for node in payload.lineage] == [f"{catalog}.gold.borrower_360"]
    node = payload.lineage[0]
    assert node.direction == "upstream"
    assert node.event_count == 3
    assert node.source == (
        f"{catalog}.audit.asset_lineage (view over system.access.table_lineage)"
    )
    assert not any("lineage" in gap.lower() for gap in payload.known_data_gaps)


def test_no_app_statement_names_the_system_lineage_table() -> None:
    fake = _LineageSqlClient("mip")

    AssetMetadataService(fake).get_asset("lead_population")

    assert fake.calls
    for statement, _params in fake.calls:
        assert "system.access" not in statement.lower(), statement


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError(
            "[TABLE_OR_VIEW_NOT_FOUND] The table or view `mip`.`audit`.`asset_lineage` "
            "cannot be found. statement_id=abc"
        ),
        RuntimeError(
            "[INSUFFICIENT_PERMISSIONS] User app@example.com does not have SELECT on "
            "Table 'mip.audit.asset_lineage'."
        ),
    ],
    ids=["unpublished-view", "ungranted-view"],
)
def test_a_failed_lineage_read_is_an_explicit_gap_and_the_rest_still_loads(
    error: Exception,
) -> None:
    fake = _LineageSqlClient("mip", lineage_error=error)

    payload = AssetMetadataService(fake).get_asset("lead_population")

    assert payload.lineage == []
    assert "Observed lineage unavailable: warehouse metadata unavailable" in (
        payload.known_data_gaps
    )
    # The other sections were still read (and degrade on their own terms).
    assert any("INFORMATION_SCHEMA.COLUMNS" in s.upper() for s, _ in fake.calls)
    rendered = payload.model_dump_json().lower()
    for leaked in ("table_or_view_not_found", "insufficient_permissions", "app@example.com"):
        assert leaked not in rendered


def test_function_assets_issue_no_lineage_read() -> None:
    fake = _LineageSqlClient("mip")

    payload = AssetMetadataService(fake).get_asset("fn_in_the_money")

    assert payload.lineage == []
    assert _lineage_calls(fake) == []
