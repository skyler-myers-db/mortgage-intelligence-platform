"""Canonical Lakebase instance-name and alias contract."""

from __future__ import annotations

import re
from collections.abc import Mapping

DEFAULT_LAKEBASE_INSTANCE_NAME = "mip-app-state"
_INSTANCE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def validated_lakebase_instance_name(value: object) -> str:
    """Return one valid lowercase DNS-style Lakebase instance name."""
    name = str(value or "").strip()
    if _INSTANCE_NAME.fullmatch(name) is None:
        raise ValueError("Lakebase instance name must be a lowercase DNS-style name")
    return name


def resolve_lakebase_instance_aliases(
    values: Mapping[str, str],
    *,
    require_both: bool,
) -> str:
    """Resolve the two runtime aliases without permitting target ambiguity."""
    mip_name = str(values.get("MIP_LAKEBASE_INSTANCE") or "").strip()
    runtime_name = str(values.get("LAKEBASE_INSTANCE_NAME") or "").strip()
    if require_both and (not mip_name or not runtime_name):
        raise ValueError(
            "MIP_LAKEBASE_INSTANCE and LAKEBASE_INSTANCE_NAME must both be explicit"
        )
    if mip_name and runtime_name and mip_name != runtime_name:
        raise ValueError("MIP_LAKEBASE_INSTANCE and LAKEBASE_INSTANCE_NAME must match")
    return validated_lakebase_instance_name(
        mip_name or runtime_name or DEFAULT_LAKEBASE_INSTANCE_NAME
    )
