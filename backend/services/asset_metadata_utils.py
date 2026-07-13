"""Small, reusable sanitizers for the governed asset metadata surface."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from backend.config.settings import settings
from backend.schemas.assets import AssetFreshness, AssetObjectType

_SENSITIVE_RE = re.compile(
    r"(owner.*name|owner_link_id|address|street|mailing|situs|email|phone|ssn|"
    r"raw[_-]|source_table|current_servicer|location|path|token|secret|password|"
    r"credential|principal|grant|clip_ref|(^|_)clip($|_))",
    re.IGNORECASE,
)


def freshness_bucket(value: str | None) -> AssetFreshness:
    if not value:
        return "unavailable"
    parsed = parse_timestamp(value)
    if parsed is None:
        return "unavailable"
    age = datetime.now(UTC) - parsed
    if age <= timedelta(days=7):
        return "fresh"
    if age <= timedelta(days=30):
        return "aging"
    return "stale"


def parse_timestamp(value: str) -> datetime | None:
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


def format_bytes(value: int | None) -> str | None:
    if value is None:
        return None
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{value} B"


def catalog_explorer_url(
    catalog: str,
    schema_name: str,
    object_name: str,
    *,
    object_type: AssetObjectType = "table",
) -> str | None:
    host = (settings.databricks_host or "").strip()
    if not host:
        return None
    if not host.startswith("http"):
        host = f"https://{host}"
    explorer_path = "explore/data/functions/" if object_type == "function" else "explore/data/"
    return (
        f"{host.rstrip('/')}/{explorer_path}"
        f"{quote(catalog)}/{quote(schema_name)}/{quote(object_name)}"
    )


def is_sensitive(value: Any) -> bool:
    if value is None:
        return False
    return bool(_SENSITIVE_RE.search(str(value)))


def safe_text(value: Any) -> str | None:
    text = opt_str(value)
    if text is None or is_sensitive(text):
        return None
    return text[:500]


def safe_data_type(value: Any) -> tuple[str | None, bool]:
    data_type = opt_str(value)
    if data_type is None:
        return None, False
    if not is_sensitive(data_type):
        return data_type, False
    lowered = data_type.lower()
    if lowered.startswith("array<"):
        return "ARRAY<STRUCT<redacted_fields: STRING>>", True
    if lowered.startswith("struct<"):
        return "STRUCT<redacted_fields: STRING>", True
    if lowered.startswith("map<"):
        return "MAP<STRING, STRING>", True
    return "STRING", True


def escape_sql_comment(value: str | None) -> str:
    if value is None:
        return ""
    return value.replace("'", "''")[:200]


def clipped_metadata_error(_exc: Exception) -> str:
    """Return a fixed summary without leaking dependency exception details."""

    return "warehouse metadata unavailable"


def opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
