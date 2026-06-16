"""Governed metadata reader for trusted Module 0 Unity Catalog assets.

The asset detail surface is a proof aid, not a catalog browser. Callers may
request only code-owned asset keys from this registry, and the service returns
sanitized, non-PII metadata only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from backend.config.settings import settings
from backend.schemas.assets import (
    AssetColumn,
    AssetFreshness,
    AssetLineageNode,
    AssetMetadataResponse,
    AssetProperty,
    AssetTag,
)
from backend.services.databricks_sql_helpers import qualify
from backend.services.resilience import TTLCache


class AssetNotFoundError(KeyError):
    """Raised when a caller asks for a non-registered asset key."""


@dataclass(frozen=True)
class AssetDescriptor:
    key: str
    title: str
    description: str
    schema_name: str
    object_name: str
    object_type: str = "table"
    readiness_source_name: str | None = None
    aliases: tuple[str, ...] = ()

    @property
    def fqn(self) -> str:
        return qualify(self.schema_name, self.object_name)

    @property
    def logical_fqn(self) -> str:
        return f"mip.{self.schema_name}.{self.object_name}"


def _descriptor(
    schema_name: str,
    object_name: str,
    *,
    title: str,
    description: str,
    object_type: str = "table",
    readiness_source_name: str | None = None,
    aliases: tuple[str, ...] = (),
) -> AssetDescriptor:
    return AssetDescriptor(
        key=object_name,
        title=title,
        description=description,
        schema_name=schema_name,
        object_name=object_name,
        object_type=object_type,
        readiness_source_name=readiness_source_name,
        aliases=aliases,
    )


_DESCRIPTORS: tuple[AssetDescriptor, ...] = (
    _descriptor(
        "gold",
        "lead_population",
        title="Gold Lead Queue Population",
        description=(
            "Ranked, quality-filtered borrowers eligible for the lead queue. "
            "This asset carries borrower-safe display fields, segment codes, "
            "evidence references, ranking, and approval state."
        ),
        readiness_source_name="UC Gold Lead Population",
    ),
    _descriptor(
        "gold",
        "borrower_360",
        title="Gold Borrower 360",
        description=(
            "Borrower-level dossier table used by Borrower 360, offers, and "
            "analytics. It joins governed Cotality-derived signals with Module 0 "
            "scoring outputs."
        ),
        readiness_source_name="UC Gold Borrower 360",
    ),
    _descriptor(
        "gold",
        "lead_scores",
        title="Gold Lead Scores",
        description=(
            "Deterministic score table with the five scoring sub-scores, "
            "opportunity score, refinance-economics flag, and primary offer code."
        ),
        readiness_source_name="UC Gold Lead Scores",
    ),
    _descriptor(
        "gold",
        "segment_population",
        title="Gold Segment Population",
        description="Segment rollup table that powers Segment Intelligence and analytics.",
        readiness_source_name="UC Gold Segment Population",
    ),
    _descriptor(
        "gold",
        "borrower_dossier",
        title="Gold Borrower Dossier",
        description="Pre-joined borrower dossier used by proof, offer, and 360 read paths.",
        readiness_source_name="UC Gold Borrower Dossier",
    ),
    _descriptor(
        "gold",
        "evidence_events",
        title="Gold Evidence Events",
        description=(
            "Append-only evidence stream. Evidence rows explain which governed "
            "source signal supported a score, offer, or queue decision."
        ),
    ),
    _descriptor(
        "gold",
        "source_readiness",
        title="Gold Source Readiness",
        description=(
            "Non-PII source status summary used by data estate and admin surfaces "
            "to prove what data is live, pending, or synthetic."
        ),
    ),
    _descriptor(
        "silver",
        "listing_activity",
        title="Silver MLS Listing Activity",
        description=(
            "CLIP-keyed Cotality MLS listing lift. Excludes raw street address, "
            "public remarks, agent/contact, and lockbox fields; active/current "
            "rows drive listed-for-sale triggers."
        ),
        readiness_source_name="MLS Listings",
        aliases=("mls_listing", "listings"),
    ),
    _descriptor(
        "silver",
        "heloc_propensity",
        title="Silver HELOC Propensity",
        description=(
            "CLIP-keyed Cotality HELOC propensity model scores. This is a "
            "model propensity feed, not a filed building-permit source."
        ),
        readiness_source_name="Cotality HELOC Propensity",
        aliases=("heloc_intent",),
    ),
    _descriptor(
        "silver",
        "refi_propensity",
        title="Silver Refi Propensity",
        description="CLIP-keyed Cotality refinance propensity model scores.",
        readiness_source_name="Cotality Refi Propensity",
        aliases=("refinance_propensity",),
    ),
    _descriptor(
        "gold",
        "lockin_cohort",
        title="Gold Lock-in Cohort",
        description="Borrower cohort table used to identify retention and lock-in opportunities.",
    ),
    _descriptor(
        "gold",
        "funnel_snapshot_daily",
        title="Gold Funnel Snapshot Daily",
        description="Daily aggregate funnel counts used by executive analytics.",
    ),
    _descriptor(
        "gold",
        "county_rollup",
        title="Gold County Rollup",
        description="County-level rollup for geographic analytics and drilldowns.",
    ),
    _descriptor(
        "gold",
        "zip_rollup",
        title="Gold ZIP Rollup",
        description="ZIP-level rollup for geographic analytics and drilldowns.",
        aliases=("zip_code_rollup",),
    ),
    _descriptor(
        "semantics",
        "lead_generation_metric_view",
        title="Lead Generation Metric View",
        description="Business-friendly KPI view used by the home page and Genie answers.",
        object_type="view",
    ),
    _descriptor(
        "semantics",
        "segment_performance_metric_view",
        title="Segment Performance Metric View",
        description="Semantic segment rollup used by analytics segment pages.",
        object_type="view",
    ),
    _descriptor(
        "semantics",
        "borrower_opportunity_metric_view",
        title="Borrower Opportunity Metric View",
        description=(
            "Business-friendly semantic view for borrower economics, geography, "
            "segments, and opportunity score analysis."
        ),
        object_type="view",
    ),
    _descriptor(
        "ref",
        "offer_rules_config",
        title="Offer Rules Configuration",
        description="Governed threshold table for refinance-economics and primary offer rules.",
        aliases=("offer_rules",),
    ),
    _descriptor(
        "ref",
        "lender_dictionary",
        title="Lender Dictionary",
        description="Public lender alias dictionary used for non-PII target-lender labeling.",
    ),
)

_SENSITIVE_RE = re.compile(
    r"(owner.*name|owner_link_id|address|street|mailing|situs|email|phone|ssn|"
    r"raw[_-]|source_table|current_servicer|location|path|token|secret|password|"
    r"credential|principal|grant|clip_ref|(^|_)clip($|_))",
    re.IGNORECASE,
)
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
            object_type=descriptor.object_type,  # type: ignore[arg-type]
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
            catalog_explorer_url=_catalog_explorer_url(catalog, schema_name, object_name),
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
        try:
            rows = self._sql.execute(f"DESCRIBE DETAIL {_quoted_fqn(descriptor)}")
        except Exception as exc:  # noqa: BLE001
            gaps.append(f"Delta detail unavailable: {_clip_error(exc)}")
            return {}
        return rows[0] if rows else {}

    def _load_count(self, descriptor: AssetDescriptor, gaps: list[str]) -> int | None:
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
                nodes.append(
                    AssetLineageNode(
                        direction=direction,  # type: ignore[arg-type]
                        asset_path=related_descriptor.fqn,
                        label=related_descriptor.title,
                        object_type=related_descriptor.object_type,
                        event_time=(
                            f"{event_time} · {event_count} observed event(s)"
                            if event_time and event_count is not None
                            else event_time
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
    if cleaned.startswith(("mip.silver.", "mip.first_party.")):
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


def _freshness_bucket(value: str | None) -> AssetFreshness:
    if not value:
        return "unavailable"
    parsed = _parse_timestamp(value)
    if parsed is None:
        return "unavailable"
    age = datetime.now(UTC) - parsed
    if age <= timedelta(days=7):
        return "fresh"
    if age <= timedelta(days=30):
        return "aging"
    return "stale"


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T", 1)):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    return None


def _format_bytes(value: int | None) -> str | None:
    if value is None:
        return None
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{value} B"


def _catalog_explorer_url(catalog: str, schema_name: str, object_name: str) -> str | None:
    host = (settings.databricks_host or "").strip()
    if not host:
        return None
    if not host.startswith("http"):
        host = f"https://{host}"
    return (
        f"{host.rstrip('/')}/explore/data/"
        f"{quote(catalog)}/{quote(schema_name)}/{quote(object_name)}"
    )


def _is_sensitive(value: Any) -> bool:
    if value is None:
        return False
    return bool(_SENSITIVE_RE.search(str(value)))


def _safe_text(value: Any) -> str | None:
    text = _opt_str(value)
    if text is None or _is_sensitive(text):
        return None
    return text[:500]


def _safe_data_type(value: Any) -> tuple[str | None, bool]:
    data_type = _opt_str(value)
    if data_type is None:
        return None, False
    if not _is_sensitive(data_type):
        return data_type, False
    lowered = data_type.lower()
    if lowered.startswith("array<"):
        return "ARRAY<STRUCT<redacted_fields: STRING>>", True
    if lowered.startswith("struct<"):
        return "STRUCT<redacted_fields: STRING>", True
    if lowered.startswith("map<"):
        return "MAP<STRING, STRING>", True
    return "STRING", True


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


def _escape_sql_comment(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("'", "''")[:200]


def _clip_error(exc: Exception) -> str:
    """Return a non-sensitive error summary for API-facing metadata gaps.

    Databricks dependency errors can include statement ids, storage paths,
    principals, relation names, or credentials-related context. The asset
    proof surface needs to tell operators which metadata section degraded
    without echoing raw warehouse exception text.
    """

    return "warehouse metadata unavailable"


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s else None


def _opt_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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
