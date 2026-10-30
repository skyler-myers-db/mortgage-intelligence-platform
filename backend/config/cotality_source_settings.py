"""The App's Cotality source settings: ``mip_cotality_source_*``.

``Settings`` (``backend/config/settings.py``) inherits these fields, so they
read the same ``MIP_COTALITY_SOURCE_*`` names the deploy controls do. Each
defaults to the canonical share, so an App deployed without them reads the
share it always has. Each validates through ``backend/config/source_names.py``
and nothing else: blank means canonical, a template value refuses, the shapes
are source_names', and "no table prefix" is ``-`` (``NO_TABLE_PREFIX``). A
refusal is a field error, so it quotes only that field's own input, never the
rest of the settings.

Readers: the asset registry and the lineage manifest (the configured share's
tables under their canonical ``source_key``) and, through the provider
``settings.py`` registers, the prompt guards (the configured share's
``source_names.qualified_forms``).
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from backend.config import source_names


class CotalitySourceSettings(BaseModel):
    """The Cotality share this install reads, stored as configured."""

    mip_cotality_source_catalog: str = source_names.CANONICAL.catalog
    mip_cotality_source_schema: str = source_names.CANONICAL.schema
    mip_cotality_source_table_prefix: str = source_names.CANONICAL.table_prefix_setting

    @field_validator("mip_cotality_source_catalog")
    @classmethod
    def _validate_cotality_source_catalog(cls, value: str) -> str:
        return source_names.resolve(catalog=value).catalog

    @field_validator("mip_cotality_source_schema")
    @classmethod
    def _validate_cotality_source_schema(cls, value: str) -> str:
        return source_names.resolve(schema=value).schema

    @field_validator("mip_cotality_source_table_prefix")
    @classmethod
    def _validate_cotality_source_table_prefix(cls, value: str) -> str:
        return source_names.resolve(table_prefix=value).table_prefix_setting

    def cotality_source(self) -> source_names.CotalitySource:
        """The share the App names: ``source_names.CANONICAL`` unless configured."""
        return source_names.resolve(
            catalog=self.mip_cotality_source_catalog,
            schema=self.mip_cotality_source_schema,
            table_prefix=self.mip_cotality_source_table_prefix,
        )
