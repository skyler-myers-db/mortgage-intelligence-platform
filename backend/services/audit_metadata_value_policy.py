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
