"""The gate's kind-to-lookup table is exactly the kinds databricks.yml declares.

A resource kind with no reviewed existence lookup refuses as ``unknown_kind``,
so a new kind in databricks.yml must arrive with its lookup (and a test that
lookup is read-only), and a kind the bundle drops must leave the table. The
child nodes the CLI plans for ``permissions`` and ``grants`` (bundle/direct/
bundle_plan.go at v1.10.0) are kinds too: none is declared today, so any
child node in a plan refuses.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks.bundle_plan_gate import KIND_LOOKUPS

REPO = Path(__file__).resolve().parents[2]


def _declared_node_kinds() -> set[str]:
    body = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    sections = [body.get("resources") or {}]
    sections += [
        (target or {}).get("resources") or {} for target in (body.get("targets") or {}).values()
    ]
    kinds: set[str] = set()
    for section in sections:
        for kind, entries in section.items():
            kinds.add(kind)
            for config in (entries or {}).values():
                kinds.update(
                    f"{kind}.{child}"
                    for child in ("permissions", "grants")
                    if child in (config or {})
                )
    if body.get("permissions"):
        # A top-level permissions block gives every resource a child node.
        kinds.update(f"{kind}.permissions" for kind in list(kinds) if "." not in kind)
    return kinds


def test_every_declared_kind_has_a_lookup_and_the_table_holds_nothing_else() -> None:
    declared = _declared_node_kinds()

    assert declared, "databricks.yml declares resources"
    assert set(KIND_LOOKUPS) == declared


def test_the_declared_kinds_are_the_eight_reviewed_kinds_today() -> None:
    # A positive control for the parse above: the bundle declares these, and
    # the not-adopted fixtures exercise one lookup per kind.
    assert _declared_node_kinds() == {
        "apps",
        "database_catalogs",
        "database_instances",
        "dashboards",
        "experiments",
        "jobs",
        "pipelines",
        "sql_warehouses",
    }
    assert {kind for kind, _ in fx.declared_resources()} == _declared_node_kinds()


def test_each_lookup_names_its_fields_scope_and_read_only_call() -> None:
    for kind, spec in KIND_LOOKUPS.items():
        assert spec.kind == kind
        assert spec.name_fields and all(field.isidentifier() for field in spec.name_fields)
        assert spec.unique_within
        service, method = spec.method.split(".")
        assert (service, method) in fx.READ_ONLY_CALLS, spec.method
    assert KIND_LOOKUPS["dashboards"].name_fields == ("parent_path", "display_name")
