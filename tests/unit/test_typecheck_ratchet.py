"""Pin the mypy ratchet so "shrink only" is enforced, not honor-system.

Re-audit 2026-06-11: nothing guarded the [tool.mypy] override list — an
exemption could be added (or a wildcard slipped in) and CI stayed green,
silently un-checking modules. This pin freezes the adoption-time list:

* removing an entry passes (that IS the ratchet working);
* adding an entry, or any wildcard, fails loudly with instructions.

When you fix a module's type errors, delete its line from pyproject.toml
AND from EXEMPT below in the same commit.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# The adoption-time exemption set (33 modules, 116 pre-existing errors at
# 2026-06-11 — 21 services/schemas + the 12 dirty api routers found when
# the re-audit's wildcard removal exposed them). SHRINK ONLY.
EXEMPT = {
    "backend.api.activation",
    "backend.api.admin",
    "backend.api.analytics",
    "backend.api.borrowers",
    "backend.api.campaigns",
    "backend.api.config",
    "backend.api.genie",
    "backend.api.geo",
    "backend.api.health",
    "backend.api.leads",
    "backend.api.sales",
    "backend.api.segments",
    "backend.schemas.portfolio",
    "backend.services.activation_state",
    "backend.services.asset_metadata",
    "backend.services.audit_lakebase_store",
    "backend.services.audit_store",
    "backend.services.databricks_sql",
    "backend.services.genie_client",
    "backend.services.genie_prompt_guardrails",
    "backend.services.lakebase",
    "backend.services.repositories.databricks_analytics",
    "backend.services.repositories.databricks_genie",
    "backend.services.repositories.databricks_genie_numeric",
    "backend.services.repositories.databricks_genie_policy",
    "backend.services.repositories.databricks_genie_trust",
    "backend.services.repositories.databricks_genie_visualization",
    "backend.services.repositories.databricks_geo",
    "backend.services.repositories.databricks_leads",
    "backend.services.repositories.protocols",
    "backend.services.sales_state",
    "backend.services.state_footprint",
    "backend.services.workspace_store",
}


def _override_modules() -> list[str]:
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    overrides = config.get("tool", {}).get("mypy", {}).get("overrides", [])
    modules: list[str] = []
    for block in overrides:
        if block.get("ignore_errors"):
            value = block.get("module", [])
            modules.extend([value] if isinstance(value, str) else value)
    return modules


def test_mypy_exemptions_may_only_shrink() -> None:
    current = set(_override_modules())
    added = current - EXEMPT
    assert added == set(), (
        f"mypy exemptions GREW: {sorted(added)}. The ratchet is shrink-only — "
        "fix the module's type errors instead of exempting it. If exemption "
        "is genuinely unavoidable, that is a reviewed policy change: update "
        "EXEMPT here with a dated rationale in the same commit."
    )


def test_mypy_exemptions_contain_no_wildcards() -> None:
    wildcards = [m for m in _override_modules() if "*" in m]
    assert wildcards == [], (
        f"wildcard mypy exemptions are banned: {wildcards} — a wildcard "
        "auto-exempts every FUTURE module under that package (re-audit "
        "2026-06-11 found backend.api.* exempting ~18 clean modules)."
    )


def test_exempt_constant_matches_pyproject() -> None:
    """Keep the test's own ledger honest: pyproject must not silently drop
    entries the ledger still carries (that would mean the ratchet advanced
    without updating the pin — fine for safety, but the ledger should be
    tightened in the same commit so the next addition can't hide)."""
    current = set(_override_modules())
    stale_ledger = EXEMPT - current
    assert stale_ledger == set(), (
        f"ratchet advanced — also remove from EXEMPT in this test: {sorted(stale_ledger)}"
    )
