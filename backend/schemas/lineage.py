"""Typed contracts for the governed lineage manifest.

Two layers of models:

* ``LineageManifestFile`` (+ family/node) — the exact shape of the
  repo-committed ``backend/resources/lineage_manifest.json``. Nodes name
  UC objects as ``(catalog, schema, object)`` parts; ``catalog: null``
  means "the deployed default catalog" so a multi-catalog customer's
  manifest stays valid without editing the file.
* ``LineageManifestResponse`` (+ family/node) — the API payload served
  by ``backend/api/lineage.py``. Nodes carry the resolved three-part
  ``fqn`` and a Catalog Explorer deep link built from the configured
  workspace host (never a hardcoded host; ``null`` when no host is
  configured, e.g. unit tests).

The manifest is the single source of truth for the EvidenceDrawer
Lineage tab. If a product surface has no family here, the UI shows an
explicit "lineage not mapped" state rather than inventing nodes.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LineageLayer = Literal[
    "raw_share",
    "silver",
    "gold",
    "uc_function",
    "metric_view",
    "reference",
]

LineageObjectType = Literal["table", "view", "function"]

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NODE_ID_RE = re.compile(r"^[a-z0-9_]+$")


class LineageManifestNode(BaseModel):
    """One UC object in a family chain, as written in the manifest file."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str
    layer: LineageLayer
    object_type: LineageObjectType
    catalog: str | None = None
    schema_name: str = Field(alias="schema")
    object_name: str = Field(alias="object")
    label: str
    note: str | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _NODE_ID_RE.match(value):
            raise ValueError(f"node id must be lower_snake_case, got {value!r}")
        return value

    @field_validator("catalog", "schema_name", "object_name")
    @classmethod
    def _valid_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _IDENT_RE.match(value):
            raise ValueError(f"invalid UC identifier: {value!r}")
        return value


class LineageManifestFamily(BaseModel):
    """One KPI/score/recommendation family and its ordered chain."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    nodes: list[LineageManifestNode] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _NODE_ID_RE.match(value):
            raise ValueError(f"family id must be lower_snake_case, got {value!r}")
        return value

    @field_validator("nodes")
    @classmethod
    def _unique_node_ids(
        cls, nodes: list[LineageManifestNode]
    ) -> list[LineageManifestNode]:
        seen: set[str] = set()
        for node in nodes:
            if node.id in seen:
                raise ValueError(f"duplicate node id within family: {node.id!r}")
            seen.add(node.id)
        return nodes


class LineageManifestFile(BaseModel):
    """Root shape of ``backend/resources/lineage_manifest.json``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1)
    description: str
    families: list[LineageManifestFamily] = Field(min_length=1)

    @field_validator("families")
    @classmethod
    def _unique_family_ids(
        cls, families: list[LineageManifestFamily]
    ) -> list[LineageManifestFamily]:
        seen: set[str] = set()
        for family in families:
            if family.id in seen:
                raise ValueError(f"duplicate family id: {family.id!r}")
            seen.add(family.id)
        return families


class LineageNode(BaseModel):
    """API node: resolved FQN + workspace deep link."""

    id: str
    layer: LineageLayer
    object_type: LineageObjectType
    fqn: str
    label: str
    note: str | None = None
    catalog_explorer_url: str | None = None


class LineageFamily(BaseModel):
    id: str
    title: str
    description: str
    nodes: list[LineageNode]


class LineageManifestResponse(BaseModel):
    schema_version: int
    manifest_path: str
    families: list[LineageFamily]
