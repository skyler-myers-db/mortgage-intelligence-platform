"""Loader for the repo-committed, versioned lineage manifest.

``backend/resources/lineage_manifest.json`` is the single source of
truth for what the EvidenceDrawer Lineage tab shows: per KPI/score/
recommendation family, the ordered chain raw Cotality share -> silver
lift -> UC scoring function -> gold table -> metric view.

This module reads and validates the file (Pydantic, fail-loud on a
malformed manifest), resolves ``catalog: null`` nodes against the
deployed default catalog and the raw-share nodes (written with the
canonical Cotality names) against the configured share, keeping each
raw-share node's canonical name as its ``source_key``, and attaches
Catalog Explorer deep links built from the configured workspace host.
No warehouse call is made —
the manifest is static product truth; the live integration test
(tests/integration/test_lineage_manifest_live.py) is what checks the
named objects against Unity Catalog.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from backend.config import source_names
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


def _share_stem(node: LineageManifestNode) -> str | None:
    """The Cotality table stem of a node written with the canonical share names.

    A node in the canonical share catalog that is not one of its tables would
    keep a canonical name on an install that mounts the share elsewhere, so it
    fails loudly here, as a canonical token left by the SQL render does.
    """
    if node.catalog != source_names.CANONICAL.catalog:
        return None
    stem = source_names.canonical_stem(node.catalog, node.schema_name, node.object_name)
    if stem is None:
        raise ValueError(f"lineage node {node.id!r} names no Cotality share table")
    return stem


def resolve_node_fqn(node: LineageManifestNode) -> tuple[str, str, str]:
    """Return ``(catalog, schema, object)`` with the default catalog
    applied to catalog-relative (``catalog: null``) manifest nodes and the
    configured Cotality share to the canonical share's nodes."""
    stem = _share_stem(node)
    if stem is not None:
        source = settings.cotality_source()
        return source.catalog, source.schema, source.table_name(stem)
    catalog = node.catalog or settings.mip_default_catalog
    return catalog, node.schema_name, node.object_name


def node_source_key(node: LineageManifestNode) -> str | None:
    """A raw-share node's canonical three-part name; ``None`` otherwise."""
    stem = _share_stem(node)
    return None if stem is None else source_names.CANONICAL.table(stem)


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
                    source_key=node_source_key(node),
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
