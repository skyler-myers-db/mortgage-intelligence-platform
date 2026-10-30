"""The App names the configured Cotality share, under a canonical source_key.

An install that mounts the share under other names
(``MIP_COTALITY_SOURCE_*``) reads it through App settings:

* the asset registry keeps each share table's canonical key and adds its
  canonical three-part name as ``source_key``, while ``fqn`` (the SQL, the
  Catalog Explorer link and the observed-lineage match) is the configured
  table;
* the lineage manifest is written in the canonical names, and each raw-share
  node resolves to the configured table with the canonical name as its
  ``source_key``;
* every other asset and node is exactly what the canonical install serves.

Each case runs in a fresh process started with exactly its env
(``tests/fixtures/cotality_source_probe.py``): the registry is built at
import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import source_names
from backend.config.source_names import CANONICAL, TABLE_STEMS
from backend.schemas.lineage import LineageManifestNode
from backend.services import lineage_manifest
from tests.fixtures.cotality_source_probe import run_fresh

CONFIGURED = {
    source_names.CATALOG_ENV: "acme_share",
    source_names.SCHEMA_ENV: "mortgage",
    source_names.TABLE_PREFIX_ENV: "acme_",
}
NO_PREFIX = {**CONFIGURED, source_names.TABLE_PREFIX_ENV: "-"}


@pytest.fixture(scope="module")
def snapshots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    cwd = tmp_path_factory.mktemp("cotality-app-names")
    return {
        "canonical": run_fresh({}, "snapshot", cwd),
        "configured": run_fresh(CONFIGURED, "snapshot", cwd),
        "no_prefix": run_fresh(NO_PREFIX, "snapshot", cwd),
    }


@pytest.mark.parametrize(("case", "prefix"), [("configured", "acme_"), ("no_prefix", "")])
def test_the_registry_names_the_configured_share_under_canonical_keys(
    snapshots: dict[str, dict], case: str, prefix: str
) -> None:
    canonical = {row["key"]: row for row in snapshots["canonical"]["registry"]}
    configured = {row["key"]: row for row in snapshots[case]["registry"]}

    assert configured.keys() == canonical.keys()
    for stem in TABLE_STEMS:
        key = CANONICAL.table_name(stem)
        row = configured[key]
        assert row["source_key"] == CANONICAL.table(stem) == canonical[key]["source_key"]
        assert (row["catalog_name"], row["schema_name"], row["object_name"]) == (
            "acme_share",
            "mortgage",
            f"{prefix}{stem}",
        )
        assert row["fqn"] == row["logical_fqn"] == f"acme_share.mortgage.{prefix}{stem}"
        location = ("catalog_name", "schema_name", "object_name", "fqn", "logical_fqn")
        assert {k: v for k, v in row.items() if k not in location} == {
            k: v for k, v in canonical[key].items() if k not in location
        }
    others = [key for key, row in canonical.items() if row["source_key"] is None]
    assert len(others) == len(canonical) - len(TABLE_STEMS)
    assert all(configured[key] == canonical[key] for key in others)


@pytest.mark.parametrize(("case", "prefix"), [("configured", "acme_"), ("no_prefix", "")])
def test_lineage_resolves_raw_share_nodes_to_the_configured_share(
    snapshots: dict[str, dict], case: str, prefix: str
) -> None:
    canonical = snapshots["canonical"]["lineage"]
    configured = snapshots[case]["lineage"]

    assert [f["id"] for f in configured["families"]] == [f["id"] for f in canonical["families"]]
    share_nodes = 0
    for before, after in zip(canonical["families"], configured["families"], strict=True):
        for old, new in zip(before["nodes"], after["nodes"], strict=True):
            if old["layer"] != "raw_share":
                assert new == old
                continue
            share_nodes += 1
            stem = old["source_key"].removeprefix("cotality_mortgage_data.corelogic.entrada_eval_")
            assert stem in TABLE_STEMS
            assert new["source_key"] == old["source_key"] == old["fqn"]
            assert new["fqn"] == f"acme_share.mortgage.{prefix}{stem}"
            assert {k: v for k, v in new.items() if k != "fqn"} == {
                k: v for k, v in old.items() if k != "fqn"
            }
    assert share_nodes >= len(TABLE_STEMS)


def test_observed_lineage_carries_the_canonical_source_key(tmp_path: Path) -> None:
    rows = [
        {
            "source_table_full_name": "acme_share.mortgage.acme_property_domain_v3",
            "target_table_full_name": "mip.silver.property_master",
            "event_time": "2026-10-01 00:00:00",
            "event_count": 2,
        },
        {
            "source_table_full_name": "mip.silver.property_master",
            "target_table_full_name": "mip.gold.borrower_360",
            "event_time": "2026-10-01 00:00:00",
            "event_count": 1,
        },
        {
            # The canonical share is not this install's share: not a registry asset.
            "source_table_full_name": CANONICAL.table("property_domain_v3"),
            "target_table_full_name": "mip.silver.property_master",
            "event_time": "2026-10-01 00:00:00",
            "event_count": 1,
        },
    ]

    nodes = run_fresh(
        CONFIGURED, "observed", tmp_path, {"asset_key": "property_master", "rows": rows}
    )

    assert [(n["direction"], n["asset_path"], n["source_key"]) for n in nodes] == [
        (
            "upstream",
            "acme_share.mortgage.acme_property_domain_v3",
            CANONICAL.table("property_domain_v3"),
        ),
        ("downstream", "mip.gold.borrower_360", None),
    ]


def test_a_canonical_catalog_node_outside_the_share_tables_fails_loudly() -> None:
    node = LineageManifestNode.model_validate(
        {
            "id": "raw_unknown",
            "layer": "raw_share",
            "object_type": "table",
            "catalog": CANONICAL.catalog,
            "schema": CANONICAL.schema,
            "object": "entrada_eval_property_basic",
            "label": "Unknown share table",
        }
    )

    with pytest.raises(ValueError, match="names no Cotality share table"):
        lineage_manifest.resolve_node_fqn(node)


def test_every_committed_canonical_catalog_node_is_a_share_table() -> None:
    manifest = lineage_manifest.load_manifest_file()
    nodes = [node for family in manifest.families for node in family.nodes]
    share = [node for node in nodes if node.catalog == CANONICAL.catalog]

    assert share and all(node.layer == "raw_share" for node in share)
    assert all(lineage_manifest.node_source_key(node) is not None for node in share)
    assert all(
        lineage_manifest.node_source_key(node) is None
        for node in nodes
        if node.catalog != CANONICAL.catalog
    )
