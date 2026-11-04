"""The SQL trust policy admits only governed FQNs: the second elevated-App control.

Plan 4.12 makes an elevated App exposure depend on two compensating static
controls: the single-space Genie client (``test_genie_single_space_ast``) and
this one, that the SQL trust policy admits only governed and configured source
FQNs (none of the latter today). ``accepted_exposure`` refuses an elevated
entry while ``sql_trust_scope.violations()`` reports anything.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.databricks.estate.sql_trust_scope import (
    ASSETS_MODULE,
    HELPERS_MODULE,
    REPO,
    TRUST_MODULE,
    violations,
)


def test_the_backend_holds_the_governed_sql_trust_scope() -> None:
    assert violations() == []


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for module in (ASSETS_MODULE, HELPERS_MODULE, TRUST_MODULE):
        target = tmp_path / module
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / module, target)
    return tmp_path


def _edit(root: Path, module: str, old: str, new: str) -> None:
    path = root / module
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{old!r} is not once in {module}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_the_copied_tree_is_clean(tree: Path) -> None:
    assert violations(tree) == []


@pytest.mark.parametrize(
    ("module", "old", "new", "expected"),
    [
        (
            ASSETS_MODULE,
            '    ("gold", "zip_rollup"),\n',
            '    ("gold", "zip_rollup"),\n    ("silver", "property_master"),\n',
            "a trusted asset outside the governed schemas",
        ),
        (
            ASSETS_MODULE,
            '    ("gold", "zip_rollup"),\n',
            '    ("gold", "zip_rollup"),\n    ("other_team", "secrets"),\n',
            "a trusted asset outside the governed schemas",
        ),
        (
            ASSETS_MODULE,
            "    return [qualify(schema, table) for schema, table in _TRUSTED_ASSET_PAIRS]\n",
            '    return [qualify(schema, table) for schema, table in _TRUSTED_ASSET_PAIRS] + ["foreign.s.t"]\n',
            "trusted_assets() must be",
        ),
        (
            ASSETS_MODULE,
            "    return [qualify(schema, table) for schema, table in _TRUSTED_ASSET_PAIRS]\n",
            '    return [qualify(schema, table, catalog="foreign") for schema, table in _TRUSTED_ASSET_PAIRS]\n',
            "trusted_assets() must be",
        ),
        (
            ASSETS_MODULE,
            "from backend.services.databricks_sql_helpers import qualify\n",
            "from backend.services.databricks_sql_helpers import qualify as _q\n\nqualify = str.format\n",
            "qualify must be imported once",
        ),
        (
            ASSETS_MODULE,
            "def trusted_assets() -> list[str]:\n",
            "_TRUSTED_ASSET_PAIRS = ()\n\n\ndef trusted_assets() -> list[str]:\n",
            "_TRUSTED_ASSET_PAIRS must be assigned once",
        ),
        (
            HELPERS_MODULE,
            '    return f"{cat}.{schema}.{table}"\n',
            '    return f"foreign.{schema}.{table}"\n',
            "qualify must return",
        ),
        (
            HELPERS_MODULE,
            "    cat = catalog if catalog is not None else settings.mip_default_catalog\n",
            '    cat = catalog if catalog is not None else "foreign_catalog"\n',
            "qualify must return",
        ),
        (
            TRUST_MODULE,
            "    return frozenset(trusted_assets())\n",
            '    return frozenset(trusted_assets()) | {"foreign.s.t"}\n',
            "_trusted_genie_asset_names() must return frozenset(trusted_assets())",
        ),
        (
            TRUST_MODULE,
            "        and all(asset in allowed_assets for asset in refs)\n",
            "",
            "_trusted_sql_policy_core must require",
        ),
        (
            TRUST_MODULE,
            '        and not _sql_has_unqualified_relations(_scrub_sql_for_policy(sql) or "")\n',
            "",
            "_trusted_sql_policy_core must require",
        ),
        (
            TRUST_MODULE,
            "    allowed_assets = _trusted_genie_asset_names()\n",
            '    allowed_assets = _trusted_genie_asset_names() | {"foreign.s.t"}\n',
            "_trusted_sql_policy_core must require",
        ),
        (
            TRUST_MODULE,
            "def _trusted_sql_policy(sql: str | None, trusted_assets: list[str]) -> bool:\n"
            "    return _trusted_sql_policy_core(\n",
            "def _trusted_sql_policy(sql: str | None, trusted_assets: list[str]) -> bool:\n"
            "    return True or _trusted_sql_policy_core(\n",
            "_trusted_sql_policy must return _trusted_sql_policy_core",
        ),
    ],
    ids=[
        "silver-schema",
        "foreign-schema",
        "extra-fqn",
        "other-catalog",
        "rebound-qualify",
        "rebound-pairs",
        "literal-catalog",
        "default-catalog",
        "widened-names",
        "no-ref-subset",
        "unqualified-relation",
        "widened-allowed",
        "bypassed-wrapper",
    ],
)
def test_a_trust_scope_breach_is_reported(tree: Path, module: str, old: str, new: str, expected: str) -> None:
    _edit(tree, module, old, new)

    found = violations(tree)
    assert any(expected in item for item in found), found


def test_a_missing_module_cannot_hold(tree: Path) -> None:
    (tree / TRUST_MODULE).unlink()

    assert violations(tree) == [f"{TRUST_MODULE}: is missing, so the SQL trust scope cannot hold"]
