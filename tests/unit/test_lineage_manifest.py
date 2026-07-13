"""Unit tests for the governed lineage manifest and its API contract.

Three layers under test:

1. The committed ``backend/resources/lineage_manifest.json`` validates
   against the Pydantic file schema and pins the S5 acceptance chain —
   the ``in_the_money`` family must reach ``fn_in_the_money`` and the
   raw Cotality share.
2. The schema validators reject malformed manifests (duplicate ids,
   junk identifiers) so drift fails loudly instead of rendering
   invented lineage.
3. The ``/api/lineage/manifest`` endpoint serves the resolved manifest
   with per-node FQNs and Catalog Explorer deep links built from the
   configured workspace host (never a hardcoded host).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.config.settings import settings
from backend.main import app
from backend.schemas.lineage import LineageManifestFamily, LineageManifestFile
from backend.services.lineage_manifest import (
    MANIFEST_REPO_PATH,
    build_manifest_response,
    catalog_explorer_url_for,
    load_manifest_file,
)

client = TestClient(app)

RAW_COTALITY_CATALOG = "cotality_mortgage_data"


def _node_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "gold_borrower_360",
        "layer": "gold",
        "object_type": "table",
        "catalog": None,
        "schema": "gold",
        "object": "borrower_360",
        "label": "Gold borrower 360",
    }
    payload.update(overrides)
    return payload


class TestCommittedManifest:
    def test_manifest_parses_and_is_versioned(self) -> None:
        manifest = load_manifest_file()
        assert manifest.schema_version >= 1
        assert manifest.families

    def test_every_family_traces_raw_share_to_gold_or_metric_view(self) -> None:
        manifest = load_manifest_file()
        readiness_only = {"source_readiness", "permit_readiness"}
        for family in manifest.families:
            layers = [node.layer for node in family.nodes]
            if family.id in readiness_only:
                assert layers[0] == "gold", (
                    f"readiness family {family.id!r} must start at a real gold status asset"
                )
            else:
                assert layers[0] == "raw_share", (
                    f"family {family.id!r} must start at the raw Cotality share"
                )
            assert layers[-1] in {"gold", "metric_view"}, (
                f"family {family.id!r} must end at a gold table or metric view"
            )

    def test_raw_share_nodes_name_the_cotality_catalog_explicitly(self) -> None:
        manifest = load_manifest_file()
        for family in manifest.families:
            for node in family.nodes:
                if node.layer == "raw_share":
                    assert node.catalog == RAW_COTALITY_CATALOG
                else:
                    # Module 0 objects stay catalog-relative so a multi-
                    # catalog deploy resolves them via MIP_DEFAULT_CATALOG.
                    assert node.catalog is None

    def test_in_the_money_family_pins_the_acceptance_chain(self) -> None:
        """S5 acceptance: from the home Refi-economics KPI the drawer's
        Lineage tab must surface fn_in_the_money AND the raw Cotality
        share as chips. That only works if both are manifest nodes."""
        manifest = load_manifest_file()
        family = next(f for f in manifest.families if f.id == "in_the_money")
        by_object = {node.object_name: node for node in family.nodes}
        assert by_object["fn_in_the_money"].layer == "uc_function"
        assert by_object["fn_in_the_money"].object_type == "function"
        raw = [n for n in family.nodes if n.layer == "raw_share"]
        assert raw and raw[0].catalog == RAW_COTALITY_CATALOG

    def test_home_kpi_score_and_offer_families_exist(self) -> None:
        manifest = load_manifest_file()
        ids = {family.id for family in manifest.families}
        assert {
            "marketable_population",
            "in_the_money",
            "opportunity_score",
            "next_best_offer",
            "rate_spread",
            "lead_queue_rank",
            "segment_population",
        } <= ids

    def test_uc_functions_cited_by_manifest_exist_in_repo_sql(self) -> None:
        """Every uc_function node must be backed by a committed
        sql/uc_functions/<name>.sql definition — the manifest may not
        claim a scoring primitive the repo does not ship."""
        from pathlib import Path

        manifest = load_manifest_file()
        sql_dir = Path(__file__).resolve().parents[2] / "sql" / "uc_functions"
        for family in manifest.families:
            for node in family.nodes:
                if node.layer == "uc_function":
                    assert (sql_dir / f"{node.object_name}.sql").is_file(), (
                        f"{node.object_name} cited in family {family.id!r} "
                        "has no sql/uc_functions definition"
                    )


class TestManifestSchemaValidation:
    def test_duplicate_family_ids_rejected(self) -> None:
        family = {
            "id": "fam",
            "title": "Fam",
            "description": "d",
            "nodes": [_node_payload()],
        }
        with pytest.raises(ValidationError, match="duplicate family id"):
            LineageManifestFile.model_validate(
                {
                    "schema_version": 1,
                    "description": "d",
                    "families": [family, family],
                }
            )

    def test_duplicate_node_ids_within_family_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate node id"):
            LineageManifestFamily.model_validate(
                {
                    "id": "fam",
                    "title": "Fam",
                    "description": "d",
                    "nodes": [_node_payload(), _node_payload()],
                }
            )

    def test_junk_identifiers_rejected(self) -> None:
        with pytest.raises(ValidationError, match="invalid UC identifier"):
            LineageManifestFamily.model_validate(
                {
                    "id": "fam",
                    "title": "Fam",
                    "description": "d",
                    "nodes": [_node_payload(object="borrower_360; DROP TABLE x")],
                }
            )

    def test_empty_families_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LineageManifestFile.model_validate(
                {"schema_version": 1, "description": "d", "families": []}
            )


class TestExplorerUrls:
    def test_urls_build_from_configured_host_never_hardcoded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "databricks_host", "dbc-unit.cloud.databricks.com")
        assert catalog_explorer_url_for("table", "mip", "gold", "borrower_360") == (
            "https://dbc-unit.cloud.databricks.com/explore/data/mip/gold/borrower_360"
        )
        assert catalog_explorer_url_for("function", "mip", "gold", "fn_in_the_money") == (
            "https://dbc-unit.cloud.databricks.com/explore/data/functions/mip/gold/fn_in_the_money"
        )

    def test_urls_are_none_without_a_configured_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "databricks_host", None)
        assert catalog_explorer_url_for("table", "mip", "gold", "borrower_360") is None

    @pytest.mark.parametrize(
        "host",
        [
            "httpx://evil.example",
            "javascript://evil.example",
            "https://user:secret@evil.example",
            "https://dbc-unit.cloud.databricks.com/path",
            "https://dbc-unit.cloud.databricks.com?redirect=evil",
        ],
    )
    def test_urls_reject_non_workspace_origins(
        self, monkeypatch: pytest.MonkeyPatch, host: str
    ) -> None:
        monkeypatch.setattr(settings, "databricks_host", host)
        assert catalog_explorer_url_for("table", "mip", "gold", "borrower_360") is None

    def test_response_resolves_default_catalog_and_links(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "databricks_host", "https://dbc-unit.cloud.databricks.com")
        monkeypatch.setattr(settings, "mip_default_catalog", "mip_custom")
        response = build_manifest_response()
        family = next(f for f in response.families if f.id == "in_the_money")
        fqns = {node.fqn for node in family.nodes}
        assert "mip_custom.gold.fn_in_the_money" in fqns
        assert (
            f"{RAW_COTALITY_CATALOG}.corelogic."
            "entrada_eval_voluntary_lien_status_marketing_v2"
        ) in fqns
        fn_node = next(n for n in family.nodes if n.id == "fn_in_the_money")
        assert fn_node.catalog_explorer_url == (
            "https://dbc-unit.cloud.databricks.com/explore/data/functions/"
            "mip_custom/gold/fn_in_the_money"
        )


class TestLineageApiContract:
    def test_manifest_endpoint_serves_resolved_manifest(self) -> None:
        response = client.get("/api/lineage/manifest")
        assert response.status_code == 200
        body = response.json()
        assert body["schema_version"] >= 1
        assert body["manifest_path"] == MANIFEST_REPO_PATH
        assert body["families"]
        node = body["families"][0]["nodes"][0]
        assert set(node) >= {
            "id",
            "layer",
            "object_type",
            "fqn",
            "label",
            "note",
            "catalog_explorer_url",
        }
        assert node["fqn"].count(".") == 2

    def test_manifest_endpoint_available_on_canonical_prefix(self) -> None:
        assert client.get("/api/v1/lineage/manifest").status_code == 200
