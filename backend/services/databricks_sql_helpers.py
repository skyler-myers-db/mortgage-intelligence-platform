"""Unity Catalog name-qualification helpers.

Multi-catalog customers deploy the app with a non-default ``uc_catalog``
(e.g. ``mip_prod``) and every query that hardcoded ``mip.gold.*`` would
silently resolve against the wrong catalog -- or 42601 on a clean
workspace.

This module centralises the ``{catalog}.{schema}.{table}`` construction
so Python callers only name ``(schema, table)`` pairs. The catalog is
resolved from ``backend.config.settings.settings.mip_default_catalog``
(env var ``MIP_DEFAULT_CATALOG``) at call time, so a single env-var flip
reroutes the whole app at boot.

Scope note: only the Python side is covered here. SQL files under
``sql/transformations/`` and ``sql/ddl/`` still hardcode ``mip.*`` --
that is a documented known-limitation (see
``docs/runbook-multi-catalog.md``). The Python API layer is the
mechanically safe slice to ship in isolation; SQL pre-processing is a
separate follow-up with its own risk surface.
"""
from __future__ import annotations

from backend.config.settings import settings


def qualify(schema: str, table: str, *, catalog: str | None = None) -> str:
    """Return ``{catalog}.{schema}.{table}``.

    ``catalog`` defaults to ``settings.mip_default_catalog`` so callers
    only pass schema + table. Pass an explicit ``catalog`` to target a
    non-default workspace (e.g. cross-workspace read of a lender's own
    catalog).

    The function performs no validation -- Unity Catalog identifier
    rules (dots, reserved words) are upstream of this helper and each
    caller already vends short stable string literals rather than
    user-input.
    """
    cat = catalog if catalog is not None else settings.mip_default_catalog
    return f"{cat}.{schema}.{table}"
