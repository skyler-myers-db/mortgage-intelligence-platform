"""Cross-layer guards for evidence-drawer destinations and governed lineage."""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.asset_metadata import allowed_asset_keys, resolve_asset_descriptor
from backend.services.genie_trusted_assets import trusted_assets
from backend.services.lineage_manifest import load_manifest_file

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWER_SOURCES_PATH = REPO_ROOT / "frontend" / "src" / "lib" / "drawerSources.ts"


def _frontend_asset_map() -> dict[str, str]:
    source = DRAWER_SOURCES_PATH.read_text(encoding="utf-8")
    block = source.split("export const ASSET_KEYS_BY_SOURCE", maxsplit=1)[1].split(
        "\n};", maxsplit=1
    )[0]
    return dict(re.findall(r"^\s*'([^']+)':\s*'([^']+)',?$", block, flags=re.MULTILINE))


def _manifest_assets() -> dict[str, str]:
    assets: dict[str, str] = {}
    for family in load_manifest_file().families:
        for node in family.nodes:
            path = f"{node.catalog or 'mip'}.{node.schema_name}.{node.object_name}"
            prior = assets.setdefault(path, node.object_type)
            assert prior == node.object_type, f"conflicting object types for {path}"
    return assets


def _backend_assets() -> dict[str, tuple[str, str]]:
    assets: dict[str, tuple[str, str]] = {}
    for key in allowed_asset_keys():
        descriptor = resolve_asset_descriptor(key)
        assets[descriptor.logical_fqn] = (descriptor.key, descriptor.object_type)
    return assets


def test_manifest_frontend_map_and_backend_registry_have_exact_asset_parity() -> None:
    manifest = _manifest_assets()
    frontend = _frontend_asset_map()
    backend = _backend_assets()

    assert set(manifest) == set(frontend) == set(backend)
    for path, key in frontend.items():
        backend_key, backend_type = backend[path]
        assert key == backend_key, f"frontend key drift for {path}"
        assert manifest[path] == backend_type, f"object type drift for {path}"


def test_every_genie_emitted_asset_has_a_drawer_destination() -> None:
    manifest = _manifest_assets()
    frontend = _frontend_asset_map()

    for emitted_path in trusted_assets():
        descriptor = resolve_asset_descriptor(emitted_path)
        logical_path = descriptor.logical_fqn
        assert logical_path in manifest, f"Genie asset missing lineage: {logical_path}"
        assert frontend.get(logical_path) == descriptor.key, (
            f"Genie asset missing frontend destination: {logical_path}"
        )


def test_pending_permit_family_uses_only_real_readiness_assets() -> None:
    family = next(
        family for family in load_manifest_file().families if family.id == "permit_readiness"
    )
    paths = {
        f"{node.catalog or 'mip'}.{node.schema_name}.{node.object_name}"
        for node in family.nodes
    }

    assert paths == {"mip.gold.source_readiness", "mip.gold.borrower_360"}
    assert all("permit" not in node.object_name.lower() for node in family.nodes)


def test_lakebase_operational_paths_are_not_uc_assets() -> None:
    assert all(not path.startswith("mip_app.") for path in _manifest_assets())
    assert all(not path.startswith("mip_app.") for path in _frontend_asset_map())


def test_every_manifest_object_is_grounded_in_committed_sql() -> None:
    sql_text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (REPO_ROOT / "sql").rglob("*.sql")
    )

    for path in _manifest_assets():
        assert path.lower() in sql_text, f"manifest object has no committed SQL reference: {path}"
