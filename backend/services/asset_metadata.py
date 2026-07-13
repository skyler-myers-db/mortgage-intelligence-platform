"""Governed metadata reader for trusted Module 0 Unity Catalog assets.

The asset detail surface is a proof aid, not a catalog browser. Callers may
request only code-owned asset keys from this registry, and the service returns
sanitized, non-PII metadata only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from backend.config.settings import settings as settings
from backend.schemas.assets import (
    AssetColumn,
    AssetLineageNode,
    AssetMetadataResponse,
    AssetProperty,
    AssetTag,
)
from backend.services.asset_metadata_utils import (
    catalog_explorer_url as _catalog_explorer_url,
)
from backend.services.asset_metadata_utils import (
    clipped_metadata_error as _clip_error,
)
from backend.services.asset_metadata_utils import (
    escape_sql_comment as _escape_sql_comment,
)
from backend.services.asset_metadata_utils import (
    format_bytes as _format_bytes,
)
from backend.services.asset_metadata_utils import (
    freshness_bucket as _freshness_bucket,
)
from backend.services.asset_metadata_utils import (
    is_sensitive as _is_sensitive,
)
from backend.services.asset_metadata_utils import (
    opt_int as _opt_int,
)
from backend.services.asset_metadata_utils import (
    opt_str as _opt_str,
)
from backend.services.asset_metadata_utils import (
    safe_data_type as _safe_data_type,
)
from backend.services.asset_metadata_utils import (
    safe_text as _safe_text,
)
from backend.services.asset_registry import ASSET_DESCRIPTORS, AssetDescriptor
from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache


class AssetNotFoundError(KeyError):
    """Raised when a caller asks for a non-registered asset key."""


_DESCRIPTORS = ASSET_DESCRIPTORS

_SAFE_PROPERTY_RE = re.compile(
    r"^(delta\.(minReaderVersion|minWriterVersion|feature\..+)|"
    r"quality|data_classification|refresh_cadence|source_system|table_type)$",
    re.IGNORECASE,
)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CACHE_KEY_PREFIX = "asset:"


class AssetMetadataService:
    """Read sanitized metadata for a trusted Unity Catalog asset."""

    def __init__(
        self,
        sql_client: Any,
        *,
        cache: TTLCache | None = None,
        cache_ttl_s: float = 300.0,
    ) -> None:
        self._sql = sql_client
        self._cache = cache if cache is not None else TTLCache()
        self._ttl = cache_ttl_s

    def get_asset(self, asset_key: str) -> AssetMetadataResponse:
        descriptor = resolve_asset_descriptor(asset_key)
        cache_key = f"{_CACHE_KEY_PREFIX}{descriptor.fqn.lower()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        payload = self._load_asset(descriptor)
        self._cache.set(cache_key, payload, self._ttl)
        return payload

    def _load_asset(self, descriptor: AssetDescriptor) -> AssetMetadataResponse:
        gaps: list[str] = []
        table_info = self._load_table_info(descriptor, gaps)
        readiness = self._load_readiness(descriptor, gaps)
        detail = self._load_detail(descriptor, gaps)
        columns = self._load_columns(descriptor, gaps)
        tags = self._load_tags(descriptor, gaps)
        properties = self._load_properties(descriptor, gaps)
        lineage = self._load_lineage(descriptor, gaps)

        row_count = _opt_int(readiness.get("row_count")) if readiness else None
        row_count_source = "source_readiness" if row_count is not None else "unavailable"
        if row_count is None and detail:
            row_count = _opt_int(detail.get("numRecords"))
            row_count_source = "delta_stats" if row_count is not None else "unavailable"
        if row_count is None:
            row_count = self._load_count(descriptor, gaps)
            row_count_source = "count" if row_count is not None else "unavailable"

        business_refresh = _opt_str(readiness.get("last_updated")) if readiness else None
        delta_last_modified = _opt_str(detail.get("lastModified")) if detail else None
        freshness_anchor = business_refresh or delta_last_modified
        size_in_bytes = _opt_int(detail.get("sizeInBytes")) if detail else None
        catalog, schema_name, object_name = _split_fqn(descriptor.fqn)
        ddl, ddl_redacted_lines = _reconstruct_sanitized_ddl(descriptor, columns)

        payload = AssetMetadataResponse(
            asset_path=descriptor.fqn,
            title=descriptor.title,
            description=descriptor.description,
            object_type=descriptor.object_type,
            status=(_opt_str(readiness.get("status")) if readiness else None) or "unknown",
            freshness=_freshness_bucket(freshness_anchor),
            catalog=catalog,
            schema_name=schema_name,
            object_name=object_name,
            uc_object=descriptor.fqn,
            generated_at=datetime.now(UTC),
            last_updated=business_refresh,
            delta_last_modified=delta_last_modified,
            row_count=row_count,
            row_count_source=row_count_source,  # type: ignore[arg-type]
            num_files=_opt_int(detail.get("numFiles")) if detail else None,
            size_in_bytes=size_in_bytes,
            size_label=_format_bytes(size_in_bytes),
            catalog_explorer_url=_catalog_explorer_url(
                catalog,
                schema_name,
                object_name,
                object_type=descriptor.object_type,
            ),
            source_note=(
                _safe_text(readiness.get("note"))
                or _safe_text(table_info.get("comment"))
                or descriptor.description
            ),
            checked_at=_opt_str(readiness.get("checked_at")) if readiness else None,
            tags=tags,
            properties=properties,
            columns=columns,
            ddl=ddl,
            ddl_redacted_lines=ddl_redacted_lines,
            lineage=lineage,
            known_data_gaps=gaps,
        )
        return payload

    def _load_table_info(self, descriptor: AssetDescriptor, gaps: list[str]) -> dict[str, Any]:
        if descriptor.object_type == "function":
            return {}
        catalog, schema_name, object_name = _split_fqn(descriptor.fqn)
        try:
            rows = self._sql.execute(
                "SELECT table_catalog, table_schema, table_name, table_type, comment "
                "FROM system.information_schema.tables "
                "WHERE table_catalog = :catalog "
                "AND table_schema = :schema_name "
                "AND table_name = :object_name",
                {
                    "catalog": catalog,
                    "schema_name": schema_name,
                    "object_name": object_name,
                },
            )
        except Exception as exc:  # noqa: BLE001 - metadata proof degrades by section
            gaps.append(f"Table metadata unavailable: {_clip_error(exc)}")
            return {}
        return rows[0] if rows else {}

    def _load_detail(self, descriptor: AssetDescriptor, gaps: list[str]) -> dict[str, Any]:
        if descriptor.object_type != "table":
            return {}
        try:
            rows = self._sql.execute(f"DESCRIBE DETAIL {_quoted_fqn(descriptor)}")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Delta detail unavailable: {_clip_error(exc)}")
            return {}
        return rows[0] if rows else {}

    def _load_count(self, descriptor: AssetDescriptor, gaps: list[str]) -> int | None:
        if descriptor.object_type == "function" or not descriptor.allow_count_fallback:
            return None
        try:
            rows = self._sql.execute(
                f"SELECT COUNT(*) AS row_count FROM {_quoted_fqn(descriptor)}"
            )
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Row count unavailable: {_clip_error(exc)}")
            return None
        return _opt_int(rows[0].get("row_count")) if rows else None

    def _load_readiness(self, descriptor: AssetDescriptor, gaps: list[str]) -> dict[str, Any]:
        try:
            rows = self._sql.execute(
                "SELECT source_name, status, row_count, "
                "CAST(last_updated AS STRING) AS last_updated, note, "
                "CAST(checked_at AS STRING) AS checked_at, source_table "
                f"FROM {qualify('gold', 'source_readiness')} "
                "WHERE lower(source_table) = lower(:fqn) "
                "OR lower(source_name) = lower(:source_name) "
                "LIMIT 1",
                {
                    "fqn": descriptor.logical_fqn,
                    "source_name": descriptor.readiness_source_name or descriptor.title,
                },
            )
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Source-readiness row unavailable: {_clip_error(exc)}")
            return {}
        return rows[0] if rows else {}

    def _load_columns(self, descriptor: AssetDescriptor, gaps: list[str]) -> list[AssetColumn]:
        if descriptor.object_type == "function":
            return []
        catalog, schema_name, object_name = _split_fqn(descriptor.fqn)
        try:
            rows = self._sql.execute(
                "SELECT column_name, ordinal_position, full_data_type, data_type, "
                "is_nullable, comment "
                "FROM system.information_schema.columns "
                "WHERE table_catalog = :catalog "
                "AND table_schema = :schema_name "
                "AND table_name = :object_name "
                "ORDER BY ordinal_position",
                {
                    "catalog": catalog,
                    "schema_name": schema_name,
                    "object_name": object_name,
                },
            )
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Column metadata unavailable: {_clip_error(exc)}")
            return []
        columns: list[AssetColumn] = []
        redacted = 0
        redacted_nested_types = 0
        for row in rows:
            name = str(row.get("column_name") or "")
            if not name:
                continue
            if _is_sensitive(name) or _is_sensitive(row.get("comment")):
                redacted += 1
                continue
            data_type, data_type_redacted = _safe_data_type(
                row.get("full_data_type") or row.get("data_type")
            )
            if data_type_redacted:
                redacted_nested_types += 1
            columns.append(
                AssetColumn(
                    name=name,
                    data_type=data_type,
                    comment=_safe_text(row.get("comment")),
                    ordinal_position=_opt_int(row.get("ordinal_position")),
                    redacted=data_type_redacted,
                )
            )
        if redacted:
            gaps.append(f"{redacted} sensitive column(s) hidden from this proof view.")
        if redacted_nested_types:
            gaps.append(
                f"{redacted_nested_types} complex column type(s) have sensitive nested fields redacted."
            )
        return columns

    def _load_tags(self, descriptor: AssetDescriptor, gaps: list[str]) -> list[AssetTag]:
        if descriptor.object_type == "function":
            return []
        catalog, schema_name, object_name = _split_fqn(descriptor.fqn)
        try:
            rows = self._sql.execute(
                "SELECT tag_name, tag_value "
                "FROM system.information_schema.table_tags "
                "WHERE catalog_name = :catalog "
                "AND schema_name = :schema_name "
                "AND table_name = :object_name "
                "ORDER BY tag_name",
                {
                    "catalog": catalog,
                    "schema_name": schema_name,
                    "object_name": object_name,
                },
            )
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"UC tags unavailable: {_clip_error(exc)}")
            return []
        tags: list[AssetTag] = []
        for row in rows:
            name = _opt_str(row.get("tag_name"))
            value = _safe_text(row.get("tag_value"))
            if not name or _is_sensitive(name) or _is_sensitive(value):
                continue
            tags.append(AssetTag(name=name, value=value))
        return tags[:20]

    def _load_properties(self, descriptor: AssetDescriptor, gaps: list[str]) -> list[AssetProperty]:
        if descriptor.object_type != "table":
            return []
        try:
            rows = self._sql.execute(f"SHOW TBLPROPERTIES {_quoted_fqn(descriptor)}")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Table properties unavailable: {_clip_error(exc)}")
            return []
        properties: list[AssetProperty] = []
        for row in rows:
            name = _opt_str(row.get("key") or row.get("property_key") or row.get("name"))
            value = _safe_text(row.get("value") or row.get("property_value"))
            if not name or not _SAFE_PROPERTY_RE.match(name) or _is_sensitive(value):
                continue
            properties.append(AssetProperty(name=name, value=value))
        return properties[:20]

    def _load_lineage(self, descriptor: AssetDescriptor, gaps: list[str]) -> list[AssetLineageNode]:
        if descriptor.object_type == "function":
            return []
        try:
            rows = self._sql.execute(
                "SELECT source_table_full_name, target_table_full_name, "
                "CAST(MAX(event_time) AS STRING) AS event_time, "
                "COUNT(*) AS event_count "
                "FROM system.access.table_lineage "
                "WHERE (source_table_full_name = :asset OR target_table_full_name = :asset) "
                "AND event_date >= current_date() - INTERVAL 90 DAYS "
                "GROUP BY source_table_full_name, target_table_full_name "
                "ORDER BY event_time DESC "
                "LIMIT 30",
                {"asset": descriptor.fqn},
            )
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Observed lineage unavailable: {_clip_error(exc)}")
            return []
        nodes: list[AssetLineageNode] = []
        for row in rows:
            source = _opt_str(row.get("source_table_full_name"))
            target = _opt_str(row.get("target_table_full_name"))
            event_time = _opt_str(row.get("event_time"))
            event_count = _opt_int(row.get("event_count"))
            for direction, related in (("upstream", source), ("downstream", target)):
                if not related or related == descriptor.fqn:
                    continue
                try:
                    related_key = _normalize_key(related)
                except AssetNotFoundError:
                    continue
                related_descriptor = _DESCRIPTOR_MAP.get(related_key)
                if related_descriptor is None:
                    continue
                related_catalog, related_schema, related_object = _split_fqn(
                    related_descriptor.fqn
                )
                nodes.append(
                    AssetLineageNode(
                        direction=direction,  # type: ignore[arg-type]
                        asset_path=related_descriptor.fqn,
                        label=related_descriptor.title,
                        object_type=related_descriptor.object_type,
                        event_time=event_time,
                        event_count=event_count,
                        catalog_explorer_url=_catalog_explorer_url(
                            related_catalog,
                            related_schema,
                            related_object,
                            object_type=related_descriptor.object_type,
                        ),
                    )
                )
        deduped: dict[tuple[str, str], AssetLineageNode] = {}
        for node in nodes:
            deduped.setdefault((node.direction, node.asset_path.lower()), node)
        return list(deduped.values())[:12]


def resolve_asset_descriptor(asset_key: str) -> AssetDescriptor:
    key = _normalize_key(asset_key)
    descriptor = _DESCRIPTOR_MAP.get(key)
    if descriptor is None:
        raise AssetNotFoundError(asset_key)
    return descriptor


def allowed_asset_keys() -> tuple[str, ...]:
    return tuple(sorted(d.key for d in _DESCRIPTORS))


def allowed_asset_paths() -> tuple[str, ...]:
    return tuple(sorted(d.fqn for d in _DESCRIPTORS))


def _normalize_key(value: str) -> str:
    cleaned = value.strip().strip("`").lower().replace("`", "")
    if not cleaned or any(token in cleaned for token in (";", "--", "/*", "*/")):
        raise AssetNotFoundError(value)
    if cleaned.startswith("system.") or cleaned.startswith("information_schema."):
        raise AssetNotFoundError(value)
    if cleaned.startswith("hive_metastore.") or cleaned.startswith("mip_app."):
        raise AssetNotFoundError(value)
    parts = cleaned.split(".")
    if len(parts) == 3:
        return cleaned
    if len(parts) == 2:
        return f"mip.{parts[0]}.{parts[1]}"
    return cleaned


def _build_descriptor_map() -> dict[str, AssetDescriptor]:
    mapping: dict[str, AssetDescriptor] = {}
    for descriptor in _DESCRIPTORS:
        mapping[descriptor.key.lower()] = descriptor
        mapping[descriptor.logical_fqn.lower()] = descriptor
        mapping[descriptor.fqn.lower()] = descriptor
        mapping[f"{descriptor.schema_name}.{descriptor.object_name}".lower()] = descriptor
        for alias in descriptor.aliases:
            mapping[alias.lower()] = descriptor
            mapping[f"mip.{descriptor.schema_name}.{alias}".lower()] = descriptor
    return mapping


_DESCRIPTOR_MAP = _build_descriptor_map()


def _split_fqn(fqn: str) -> tuple[str, str, str]:
    parts = fqn.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected three-part UC name, got {fqn!r}")
    return parts[0], parts[1], parts[2]


def _quoted_fqn(descriptor: AssetDescriptor) -> str:
    catalog, schema_name, object_name = _split_fqn(descriptor.fqn)
    return ".".join(_quote_ident(part) for part in (catalog, schema_name, object_name))


def _quote_ident(value: str) -> str:
    if not _IDENT_RE.match(value):
        raise AssetNotFoundError(value)
    return f"`{value}`"


def _reconstruct_sanitized_ddl(
    descriptor: AssetDescriptor,
    columns: list[AssetColumn],
) -> tuple[str | None, int]:
    if not columns:
        return None, 0
    redacted = 0
    lines = [
        "-- Sanitized column contract. Storage locations, owners, grants,",
        "-- table properties, and sensitive columns are intentionally omitted.",
        f"CREATE {descriptor.object_type.upper()} {_quoted_fqn(descriptor)} (",
    ]
    safe_columns = []
    for column in columns:
        if _is_sensitive(column.name) or _is_sensitive(column.comment):
            redacted += 1
            continue
        data_type = column.data_type or "STRING"
        comment = f" COMMENT '{_escape_sql_comment(column.comment)}'" if column.comment else ""
        safe_columns.append(f"  {_quote_ident(column.name)} {data_type}{comment}")
    lines.extend(
        f"{line}{',' if i < len(safe_columns) - 1 else ''}"
        for i, line in enumerate(safe_columns)
    )
    lines.append(");")
    return "\n".join(lines), redacted


_SERVICE: AssetMetadataService | None = None


def get_asset_metadata_service() -> AssetMetadataService:
    """Return the process-wide asset metadata service."""

    global _SERVICE
    if _SERVICE is not None:
        return _SERVICE
    from backend.services.databricks_sql import get_sql_client

    _SERVICE = AssetMetadataService(get_sql_client())
    return _SERVICE


def _reset_asset_metadata_service_for_tests() -> None:
    """Test helper to reset the singleton."""

    global _SERVICE
    _SERVICE = None
