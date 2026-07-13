"""Loader for the repo-committed, versioned lineage manifest.

``backend/resources/lineage_manifest.json`` is the single source of
truth for what the EvidenceDrawer Lineage tab shows: per KPI/score/
recommendation family, the ordered chain raw Cotality share -> silver
lift -> UC scoring function -> gold table -> metric view.

This module reads and validates the file (Pydantic, fail-loud on a
malformed manifest), resolves ``catalog: null`` nodes against the
deployed default catalog, and attaches Catalog Explorer deep links
built from the configured workspace host. No warehouse call is made —
the manifest is static product truth; the live integration test
(tests/integration/test_lineage_manifest_live.py) is what checks the
named objects against Unity Catalog.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from backend.config.settings import settings
from backend.schemas.lineage import (
    LineageFamily,
    LineageManifestFile,
    LineageManifestNode,
    LineageManifestResponse,
    LineageNode,
)
from backend.services.asset_metadata_utils import workspace_origin

MANIFEST_REPO_PATH = "backend/resources/lineage_manifest.json"
_MANIFEST_FILE = Path(__file__).resolve().parents[1] / "resources" / "lineage_manifest.json"

_cached_file: LineageManifestFile | None = None


def load_manifest_file(path: Path | None = None) -> LineageManifestFile:
    """Parse and validate the committed manifest. Raises on any drift
    from the schema — a broken manifest must fail tests/deploy loudly,
    never render invented lineage."""
    raw = (path or _MANIFEST_FILE).read_text(encoding="utf-8")
    return LineageManifestFile.model_validate(json.loads(raw))


def _manifest_file_cached() -> LineageManifestFile:
    """The committed file cannot change under a running process, so the
    parse+validate cost is paid once. Host/catalog settings are NOT
    baked in here — the response is built per request so it always
    reflects current settings (and tests never leak monkeypatches)."""
    global _cached_file
    if _cached_file is None:
        _cached_file = load_manifest_file()
    return _cached_file


def catalog_explorer_url_for(
    object_type: str,
    catalog: str,
    schema_name: str,
    object_name: str,
) -> str | None:
    """Workspace deep link for a UC object, or None when no host is
    configured. Functions live under ``/explore/data/functions/``;
    tables and views share the plain ``/explore/data/`` path."""
    host = workspace_origin(settings.databricks_host)
    if host is None:
        return None
    segment = "functions/" if object_type == "function" else ""
    return (
        f"{host.rstrip('/')}/explore/data/{segment}"
        f"{quote(catalog)}/{quote(schema_name)}/{quote(object_name)}"
    )


def resolve_node_fqn(node: LineageManifestNode) -> tuple[str, str, str]:
    """Return ``(catalog, schema, object)`` with the default catalog
    applied to catalog-relative (``catalog: null``) manifest nodes."""
    catalog = node.catalog or settings.mip_default_catalog
    return catalog, node.schema_name, node.object_name


def build_manifest_response() -> LineageManifestResponse:
    manifest = _manifest_file_cached()
    families: list[LineageFamily] = []
    for family in manifest.families:
        nodes: list[LineageNode] = []
        for node in family.nodes:
            catalog, schema_name, object_name = resolve_node_fqn(node)
            nodes.append(
                LineageNode(
                    id=node.id,
                    layer=node.layer,
                    object_type=node.object_type,
                    fqn=f"{catalog}.{schema_name}.{object_name}",
                    label=node.label,
                    note=node.note,
                    catalog_explorer_url=catalog_explorer_url_for(
                        node.object_type, catalog, schema_name, object_name
                    ),
                )
            )
        families.append(
            LineageFamily(
                id=family.id,
                title=family.title,
                description=family.description,
                nodes=nodes,
            )
        )
    return LineageManifestResponse(
        schema_version=manifest.schema_version,
        manifest_path=MANIFEST_REPO_PATH,
        families=families,
    )


def get_lineage_manifest_response() -> LineageManifestResponse:
    return build_manifest_response()


def _reset_lineage_manifest_for_tests() -> None:
    global _cached_file
    _cached_file = None
