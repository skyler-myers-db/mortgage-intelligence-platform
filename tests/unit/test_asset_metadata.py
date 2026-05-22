from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.asset_metadata import (
    AssetMetadataService,
    AssetNotFoundError,
    get_asset_metadata_service,
    resolve_asset_descriptor,
)

client = TestClient(app)


class _AssetSqlClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.calls.append((statement, parameters))
        s = statement.upper()
        if "SYSTEM.INFORMATION_SCHEMA.TABLES" in s:
            return [
                {
                    "table_catalog": "mip",
                    "table_schema": "gold",
                    "table_name": "lead_population",
                    "table_type": "MANAGED",
                    "comment": "Ranked lead population",
                }
            ]
        if "GOLD.SOURCE_READINESS" in s:
            return [
                {
                    "source_name": "UC Gold Lead Population",
                    "status": "live",
                    "row_count": 123,
                    "last_updated": "2026-05-20 12:00:00",
                    "checked_at": "2026-05-20 12:15:00",
                    "note": "Ranked lead queue table · refreshed",
                    "source_table": "mip.gold.lead_population",
                }
            ]
        if s.startswith("DESCRIBE DETAIL"):
            return [
                {
                    "numRecords": 456,
                    "numFiles": 7,
                    "sizeInBytes": 2048,
                    "lastModified": "2026-05-20T12:10:00.000Z",
                    "location": "dbfs:/must-not-leak",
                }
            ]
        if "SYSTEM.INFORMATION_SCHEMA.COLUMNS" in s:
            return [
                {
                    "column_name": "borrower_id",
                    "ordinal_position": 1,
                    "full_data_type": "STRING",
                    "data_type": "STRING",
                    "comment": "Masked borrower id",
                },
                {
                    "column_name": "opportunity_score",
                    "ordinal_position": 2,
                    "full_data_type": "INT",
                    "data_type": "INT",
                    "comment": "Deterministic score",
                },
                {
                    "column_name": "evidence_events",
                    "ordinal_position": 3,
                    "full_data_type": "ARRAY<STRUCT<evidence_id:STRING,source_table:STRING,display_text:STRING>>",
                    "data_type": "ARRAY",
                    "comment": "Display-safe evidence rows",
                },
                {
                    "column_name": "owner_name_hash",
                    "ordinal_position": 4,
                    "full_data_type": "STRING",
                    "data_type": "STRING",
                    "comment": "Sensitive owner material",
                },
                {
                    "column_name": "source_table",
                    "ordinal_position": 5,
                    "full_data_type": "STRING",
                    "data_type": "STRING",
                    "comment": "Raw source table",
                },
            ]
        if "SYSTEM.INFORMATION_SCHEMA.TABLE_TAGS" in s:
            return [
                {"tag_name": "data_classification", "tag_value": "masked"},
                {"tag_name": "owner_email", "tag_value": "person@example.com"},
            ]
        if s.startswith("SHOW TBLPROPERTIES"):
            return [
                {"key": "quality", "value": "gold"},
                {"key": "location", "value": "dbfs:/must-not-leak"},
                {"key": "secret", "value": "token"},
            ]
        if "SYSTEM.ACCESS.TABLE_LINEAGE" in s:
            return [
                {
                    "source_table_full_name": "mip.gold.borrower_360",
                    "target_table_full_name": "mip.gold.lead_population",
                    "event_time": "2026-05-20 12:10:00",
                    "event_count": 2,
                },
                {
                    "source_table_full_name": "mip.silver.lien_current",
                    "target_table_full_name": "mip.gold.lead_population",
                    "event_time": "2026-05-20 12:10:00",
                    "event_count": 2,
                },
            ]
        raise AssertionError(f"unexpected SQL: {statement}")


class _SensitiveErrorSqlClient:
    def execute(self, _statement: str, _parameters: Any = None) -> list[dict[str, Any]]:
        raise RuntimeError(
            "PERMISSION_DENIED: path=dbfs:/secret s3://bucket/private "
            "statement_id=abc token=xyz secret=abc principal=user@example.com"
        )


class _SensitiveReadinessNoteSqlClient(_AssetSqlClient):
    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        if "GOLD.SOURCE_READINESS" in statement.upper():
            return [
                {
                    "source_name": "UC Gold Lead Population",
                    "status": "live",
                    "row_count": 123,
                    "last_updated": "2026-05-20 12:00:00",
                    "checked_at": "2026-05-20 12:15:00",
                    "note": (
                        "source_table=mip.silver.raw path=dbfs:/secret token=xyz "
                        "principal=user@example.com mailing_address=123 Main"
                    ),
                    "source_table": "mip.gold.lead_population",
                }
            ]
        return super().execute(statement, parameters)


def test_asset_metadata_route_returns_typed_sanitized_metadata() -> None:
    response = client.get("/api/admin/assets/lead_population/metadata")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Gold Lead Queue Population"
    assert body["uc_object"].endswith(".gold.lead_population")
    assert body["row_count"] == 5156184
    assert body["row_count_source"] == "source_readiness"
    assert body["freshness"] in {"fresh", "aging", "stale", "unavailable"}
    assert body["num_files"] == 42
    assert body["size_label"]
    assert body["catalog_explorer_url"] is None or "/explore/data/" in body["catalog_explorer_url"]

    rendered = json.dumps(body).lower()
    assert "owner_name_hash" not in rendered
    assert "dbfs:/" not in rendered
    assert "source_table" not in rendered


def test_asset_metadata_degraded_errors_do_not_echo_dependency_details() -> None:
    payload = AssetMetadataService(_SensitiveErrorSqlClient()).get_asset("lead_population")

    assert payload.known_data_gaps
    rendered = payload.model_dump_json().lower()
    for forbidden in (
        "dbfs:/",
        "s3://",
        "token",
        "secret",
        "principal",
        "user@example.com",
        "statement_id",
        "permission_denied",
    ):
        assert forbidden not in rendered
    assert "warehouse metadata unavailable" in rendered


def test_asset_metadata_source_note_uses_safe_text_policy() -> None:
    payload = AssetMetadataService(_SensitiveReadinessNoteSqlClient()).get_asset(
        "lead_population"
    )

    assert payload.source_note == "Ranked lead population"
    rendered = payload.model_dump_json().lower()
    for forbidden in (
        "source_table",
        "mip.silver.raw",
        "dbfs:/",
        "token",
        "principal",
        "user@example.com",
        "mailing_address",
    ):
        assert forbidden not in rendered


def test_asset_metadata_route_source_note_uses_safe_text_policy() -> None:
    app.dependency_overrides[get_asset_metadata_service] = lambda: AssetMetadataService(
        _SensitiveReadinessNoteSqlClient()
    )
    try:
        response = client.get("/api/admin/assets/lead_population/metadata")
    finally:
        app.dependency_overrides.pop(get_asset_metadata_service, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_note"] == "Ranked lead population"
    rendered = response.text.lower()
    for forbidden in (
        "source_table",
        "mip.silver.raw",
        "dbfs:/",
        "token",
        "principal",
        "user@example.com",
        "mailing_address",
    ):
        assert forbidden not in rendered


def test_asset_metadata_degraded_route_does_not_echo_dependency_details() -> None:
    app.dependency_overrides[get_asset_metadata_service] = lambda: AssetMetadataService(
        _SensitiveErrorSqlClient()
    )
    try:
        response = client.get("/api/admin/assets/lead_population/metadata")
    finally:
        app.dependency_overrides.pop(get_asset_metadata_service, None)

    assert response.status_code == 200, response.text
    rendered = response.text.lower()
    for forbidden in (
        "dbfs:/",
        "s3://",
        "token",
        "secret",
        "principal",
        "user@example.com",
        "statement_id",
        "permission_denied",
    ):
        assert forbidden not in rendered
    assert "warehouse metadata unavailable" in rendered


def test_asset_metadata_route_rejects_unknown_asset() -> None:
    response = client.get("/api/admin/assets/system.access.table_lineage/metadata")
    assert response.status_code == 404
    assert response.json() == {"detail": "asset not found"}


def test_asset_metadata_route_requires_admin_membership() -> None:
    response = client.get(
        "/api/admin/assets/lead_population/metadata",
        headers={"X-Forwarded-Groups": ""},
    )
    assert response.status_code == 403


def test_service_uses_registry_parameterized_metadata_and_sanitizes_output() -> None:
    fake = _AssetSqlClient()
    payload = AssetMetadataService(fake).get_asset("lead_population")

    assert payload.asset_path.endswith(".gold.lead_population")
    assert payload.row_count == 123
    assert payload.row_count_source == "source_readiness"
    assert payload.num_files == 7
    assert payload.size_label == "2.00 KB"
    assert [column.name for column in payload.columns] == [
        "borrower_id",
        "opportunity_score",
        "evidence_events",
    ]
    evidence_column = next(column for column in payload.columns if column.name == "evidence_events")
    assert evidence_column.redacted is True
    assert evidence_column.data_type == "ARRAY<STRUCT<redacted_fields: STRING>>"
    assert payload.tags == [payload.tags[0]]
    assert payload.tags[0].name == "data_classification"
    assert payload.properties == [payload.properties[0]]
    assert payload.properties[0].name == "quality"
    assert payload.ddl is not None
    assert "owner_name_hash" not in payload.ddl
    assert "source_table" not in payload.ddl
    assert "redacted_fields" in payload.ddl
    assert "LOCATION" not in payload.ddl
    assert [node.asset_path for node in payload.lineage] == ["mip.gold.borrower_360"]

    rendered = payload.model_dump_json().lower()
    assert "dbfs:/" not in rendered
    assert "person@example.com" not in rendered
    assert "mip.silver" not in rendered
    assert "source_table" not in rendered

    statements = [statement for statement, _params in fake.calls]
    assert not any("SELECT *" in statement.upper() for statement in statements)
    assert not any("SHOW CREATE TABLE" in statement.upper() for statement in statements)
    assert any("system.information_schema.tables" in statement for statement in statements)
    assert any("system.information_schema.columns" in statement for statement in statements)
    assert any("DESCRIBE DETAIL `mip`.`gold`.`lead_population`" in statement for statement in statements)

    for statement, params in fake.calls:
        if "system.information_schema" in statement.lower():
            assert params == {
                "catalog": "mip",
                "schema_name": "gold",
                "object_name": "lead_population",
            }


@pytest.mark.parametrize(
    "asset_key",
    [
        "mip.silver.lien_current",
        "mip.first_party.loan_applications",
        "system.access.table_lineage",
        "information_schema.columns",
        "hive_metastore.default.table",
        "mip_app.action_audit",
        "lead_population; DROP TABLE mip.gold.lead_population",
        "lead_population -- comment",
    ],
)
def test_asset_registry_rejects_unsafe_or_unapproved_assets_without_sql(asset_key: str) -> None:
    fake = _AssetSqlClient()
    service = AssetMetadataService(fake)

    with pytest.raises(AssetNotFoundError):
        service.get_asset(asset_key)

    assert fake.calls == []


def test_asset_registry_accepts_known_aliases_only() -> None:
    assert resolve_asset_descriptor("mip.gold.lead_population").key == "lead_population"
    assert resolve_asset_descriptor("lead_population").key == "lead_population"

    with pytest.raises(AssetNotFoundError):
        resolve_asset_descriptor("lien_current")
