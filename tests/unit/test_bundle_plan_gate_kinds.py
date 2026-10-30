"""The gate's kind-to-lookup table is exactly the kinds databricks.yml declares.

A resource kind with no reviewed existence lookup refuses as ``unknown_kind``,
so a new kind in databricks.yml must arrive with its lookup (and a test that
lookup is read-only), and a kind the bundle drops must leave the table. The
child nodes the CLI plans for ``permissions`` and ``grants`` (bundle/direct/
bundle_plan.go at v1.10.0) are kinds too: none is declared today, so any
child node in a plan refuses.

A workspace-scoped lookup answers with what the caller may see, so its
absence counts only after the caller is proven a workspace admin; the
metastore-scoped catalog lookup is the one kind that skips that proof.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from databricks.sdk.errors import PermissionDenied

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_gate import KIND_LOOKUPS
from tools.databricks.bundle_plan_shape import parse_plan

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


# --- a workspace-scoped absence needs a workspace admin ---------------------------


def _first_install(workspace: fx.RecordingWorkspace) -> gate.PlanReport:
    report = gate.evaluate(
        parse_plan(fx.dumps(fx.first_install())),
        position="pre-lease",
        app_key=fx.APP_KEY,
        acknowledgement="",
        workspace_factory=lambda: workspace,
    )
    assert workspace.mutating_calls() == []
    return report


def test_only_the_metastore_scoped_lookup_skips_the_admin_proof() -> None:
    for kind, spec in KIND_LOOKUPS.items():
        assert spec.requires_admin_visibility is (spec.unique_within != "metastore"), kind
    assert [k for k, s in KIND_LOOKUPS.items() if not s.requires_admin_visibility] == [
        "database_catalogs"
    ]


def test_a_caller_that_is_not_a_workspace_admin_proves_no_workspace_name_free() -> None:
    workspace = fx.RecordingWorkspace(admin=False)

    report = _first_install(workspace)

    outcomes = {record.kind: record.outcome for record in report.lookups}
    assert outcomes.pop("database_catalogs") == "absent"
    assert set(outcomes.values()) == {"failed: VisibilityUnproven"}
    assert set(report.codes) == {"lookup_failed"}
    assert len(report.codes) == len(fx.declared_resources()) - 1
    # The lists and GETs never ran: only the identity read and the catalog GET.
    assert sorted({call[:2] for call in workspace.calls}) == [
        ("catalogs", "get"),
        ("current_user", "me"),
    ]
    assert [call[:2] for call in workspace.calls].count(("current_user", "me")) == 1


@pytest.mark.parametrize("admin", (True, False))
def test_a_warehouse_the_caller_cannot_see_is_never_read_as_free(admin: bool) -> None:
    # warehouses.list lists only what the caller may use: an empty list is not
    # "absent" unless the caller is an admin, who sees every warehouse.
    key = "resources.sql_warehouses.mip_serverless_sql"
    doc = fx.with_entries(
        fx.adopted(),
        {key: fx.entry("create", value=fx.resolved_name("sql_warehouses", "mip_serverless_sql"))},
    )
    workspace = fx.RecordingWorkspace(admin=admin)

    report = gate.evaluate(
        parse_plan(fx.dumps(doc)),
        position="pre-lease",
        app_key=fx.APP_KEY,
        acknowledgement="",
        workspace_factory=lambda: workspace,
    )

    if admin:
        assert report.codes == () and report.lookups[0].outcome == "absent"
        assert ("warehouses", "list") in {call[:2] for call in workspace.calls}
    else:
        assert report.codes == ("lookup_failed",)
        assert report.refusals[0].detail["reason"] == "failed: VisibilityUnproven"
        assert "not a workspace admin" in report.refusals[0].detail["error"]
        assert [call[:2] for call in workspace.calls] == [("current_user", "me")]


def test_an_identity_read_error_is_never_read_as_an_admin() -> None:
    workspace = fx.RecordingWorkspace(failures={"current_user": PermissionDenied("denied")})

    report = _first_install(workspace)

    failed = {r.kind for r in report.lookups if r.outcome == "failed: PermissionDenied"}
    assert failed == {kind for kind, _ in fx.declared_resources()} - {"database_catalogs"}
    assert set(report.codes) == {"lookup_failed"}
