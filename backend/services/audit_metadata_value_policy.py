"""Small value validators for audit metadata.

Kept outside ``audit_store.py`` so the central audit choke point stays under
the repository monolith threshold while still enforcing reviewed metadata
contracts.
"""

from __future__ import annotations

import re
from typing import Any

_SOURCE_ASSET_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\.(?:gold|semantics|ref|mip_app)\.[A-Za-z_][A-Za-z0-9_]*$"
    r"|^mip_app\.[A-Za-z_][A-Za-z0-9_]*$"
)
_SQL_HASH_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
_MAX_SOURCE_ASSETS = 20
_MAX_ROW_COUNT = 10_000_000


def validate_source_assets(value: Any) -> None:
    if not isinstance(value, list):
        raise ValueError("must be a bounded list of UC assets")
    if len(value) > _MAX_SOURCE_ASSETS:
        raise ValueError(f"must contain at most {_MAX_SOURCE_ASSETS} assets")
    for asset in value:
        text = str(asset)
        lowered = text.lower()
        if not _SOURCE_ASSET_PATTERN.fullmatch(text):
            raise ValueError("contains an unreviewed source asset name")
        if (
            lowered.startswith(("system.", "information_schema.", "hive_metastore.", "cotality_mortgage_data."))
            or ".silver." in lowered
            or ".first_party." in lowered
        ):
            raise ValueError("contains a forbidden source asset")


def validate_sql_hash(value: Any) -> None:
    if not _SQL_HASH_PATTERN.fullmatch(str(value)):
        raise ValueError("must be a lowercase hex SQL hash")


def validate_row_count(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > _MAX_ROW_COUNT:
        raise ValueError("must be a bounded non-negative integer")


# VIEW_LEADS server paging (D-audit-reads-a). The sort tokens are
# repositories/databricks_lead_order.LEAD_SORTS (pinned equal in
# tests/unit/test_leads_paging_audit.py); the page bounds are
# schemas/lead_query.LEAD_MAX_PAGE_INDEX.
_LEAD_VIEW_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_LEAD_VIEW_SORTS = frozenset({"rank", "score", "equity", "rate", "confidence"})
_LEAD_VIEW_SORT_DIRS = frozenset({"asc", "desc"})
_MAX_LEAD_PAGE_INDEX = 9


def _bounded_int(value: Any, low: int, high: int) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def validate_lead_view_values(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    """The (field, reason) violations among the Lead Queue view keys."""

    violations: list[tuple[str, str]] = []
    for key, value in metadata.items():
        field = key.lower()
        if value is None:
            continue
        if field in {"view_id", "declared_lead_view_id"} and not _LEAD_VIEW_ID_PATTERN.fullmatch(str(value)):
            violations.append((key, "must be a server-minted 32-hex lead view id"))
        elif field == "page_index" and not _bounded_int(value, 0, _MAX_LEAD_PAGE_INDEX):
            violations.append((key, "must be a page index from 0 to 9"))
        elif field == "pages_loaded" and not _bounded_int(value, 1, _MAX_LEAD_PAGE_INDEX + 1):
            violations.append((key, "must be a page count from 1 to 10"))
        elif field == "sort" and str(value) not in _LEAD_VIEW_SORTS:
            violations.append((key, "must be a governed lead sort"))
        elif field == "sort_dir" and str(value) not in _LEAD_VIEW_SORT_DIRS:
            violations.append((key, "must be asc or desc"))
        elif field == "total_matching":
            try:
                validate_row_count(value)
            except ValueError as exc:
                violations.append((key, str(exc)))
    return violations
