"""The bundle plan gate refuses deletes, recreates, collisions and unadopted installs (M0-PR4).

Every plan here is a sanitized fixture that mirrors the Databricks CLI
v1.10.0 plan structs (tests/fixtures/bundle_plan_fixtures.py); a live capture
is owed at the first governed plan phase. The lookups run against a recording
workspace, so each test also proves the gate made only GET and list calls.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from databricks.sdk.errors import PermissionDenied

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks import bundle_plan_lookups as lookups
from tools.databricks.bundle_plan_shape import parse_plan

APP = f"resources.apps.{fx.APP_KEY}"
JOB = "resources.jobs.mip_scheduled_refresh"
INSTANCE = "resources.database_instances.mip_app_state"


def _evaluate(
    doc: dict[str, Any],
    workspace: fx.RecordingWorkspace | None = None,
    *,
    ack: str = "",
    position: str = "pre-lease",
) -> tuple[gate.PlanReport, fx.RecordingWorkspace, list[int]]:
    workspace = workspace or fx.RecordingWorkspace()
    built: list[int] = []

    def factory() -> fx.RecordingWorkspace:
        built.append(1)
        return workspace

    report = gate.evaluate(
        parse_plan(fx.dumps(doc)),
        position=position,
        app_key=fx.APP_KEY,
        acknowledgement=ack,
        workspace_factory=factory,
    )
    assert workspace.mutating_calls() == [], "the gate made a call outside the read-only set"
    return report, workspace, built


# --- passing plans --------------------------------------------------------------


def test_a_create_free_adopted_plan_passes_without_building_a_client() -> None:
    report, workspace, built = _evaluate(fx.adopted())

    assert report.codes == ()
    assert report.app_bound is True
    assert built == [] and workspace.calls == [], "no create, so no lookup"
    assert report.counts["skip"] == len(fx.declared_resources())


def test_in_place_updates_pass() -> None:
    doc = fx.with_entries(
        fx.adopted(),
        {
            JOB: fx.entry(
                "update",
                value=fx.resolved_name("jobs", "mip_scheduled_refresh"),
                changes={"name": {"action": "update", "old": "[dev x] job", "new": "job"}},
            ),
            "resources.pipelines.mip_feature_pipeline": fx.entry(
                "update", value={"name": "synthetic-mip_feature_pipeline"}
            ),
            APP: fx.entry("update", value=fx.resolved_name("apps", fx.APP_KEY)),
        },
    )

    report, _workspace, built = _evaluate(doc)

    assert report.codes == ()
    assert report.counts["update"] == 3
    assert built == []


def test_a_first_install_whose_names_are_free_passes_after_one_lookup_per_create() -> None:
    report, workspace, built = _evaluate(fx.first_install())

    assert report.codes == ()
    assert report.app_bound is False
    assert built == [1], "one client for every lookup"
    assert len(workspace.lookup_calls()) == len(fx.declared_resources())
    assert [call[:2] for call in workspace.calls].count(("current_user", "me")) == 1
    assert {(service, method) for service, method, _ in workspace.calls} == fx.READ_ONLY_CALLS
    assert {record.outcome for record in report.lookups} == {"absent"}


# --- refusals -------------------------------------------------------------------


def test_a_create_of_an_existing_name_on_an_adopted_install_is_a_collision() -> None:
    doc = fx.with_entries(
        fx.adopted(),
        {JOB: fx.entry("create", value=fx.resolved_name("jobs", "mip_scheduled_refresh"))},
    )
    workspace = fx.RecordingWorkspace({"jobs": {"SYNTHETIC-mip_scheduled_refresh"}})

    report, workspace, _built = _evaluate(doc, workspace)

    assert report.codes == ("collision",)
    refusal = report.refusals[0]
    assert refusal.resource == "jobs.mip_scheduled_refresh"
    assert refusal.detail["names"] == ["synthetic-mip_scheduled_refresh"]
    assert refusal.detail["lookup"] == "jobs.list"
    assert [call[:2] for call in workspace.calls] == [("current_user", "me"), ("jobs", "list")]


@pytest.mark.parametrize("gone", (False, True))
def test_a_delete_refuses_even_when_the_object_is_already_gone(gone: bool) -> None:
    stale = "resources.jobs.retired_job"
    doc = fx.with_entries(fx.adopted(), {stale: fx.entry("delete", gone=gone)})

    report, _workspace, _built = _evaluate(doc)

    assert report.codes == ("delete",)
    assert report.refusals[0].resource == "jobs.retired_job"
    assert report.refusals[0].detail == {"gone": gone}


def test_a_recreate_refuses() -> None:
    doc = fx.with_entries(
        fx.adopted(),
        {
            "resources.database_catalogs.mip_app_state_catalog": fx.entry(
                "recreate",
                value=fx.resolved_name("database_catalogs", "mip_app_state_catalog"),
                changes={"database_name": {"action": "recreate", "reason": "immutable"}},
            )
        },
    )

    report, _workspace, _built = _evaluate(doc)

    assert report.codes == ("recreate",)
    assert report.counts["recreate"] == 1


def test_an_identity_rename_refuses() -> None:
    doc = fx.with_entries(fx.adopted(), {APP: fx.entry("update_id", value={"name": "renamed"})})

    report, _workspace, _built = _evaluate(doc)

    assert report.codes == ("id_change",)


@pytest.mark.parametrize(
    "key",
    (
        "resources.secret_scopes.extra",
        "resources.jobs.mip_ref_seed.permissions",
        "resources.genie_spaces.extra",
    ),
)
def test_an_unknown_kind_refuses_whatever_its_action(key: str) -> None:
    doc = fx.with_entries(fx.adopted(), {key: fx.entry("skip")})

    report, _workspace, built = _evaluate(doc)

    assert report.codes == ("unknown_kind",)
    assert built == []


def test_an_unacknowledged_instance_update_refuses_and_prints_its_digest() -> None:
    doc = fx.with_entries(fx.adopted(), {INSTANCE: fx.instance_update()})

    report, _workspace, _built = _evaluate(doc)

    assert report.codes == ("instance_update_unacked",)
    assert len(report.instance_update_sha256) == 64
    assert report.refusals[0].detail == {
        "digest": report.instance_update_sha256,
        "acknowledged": False,
    }
    assert report.instance_update[0]["changes"] == {
        "capacity": {
            "action": "update",
            "reason": "",
            "old": "CU_1",
            "new": "CU_2",
            "remote": "CU_1",
        }
    }, "skip changes stay out of the acknowledged diff"


def test_the_exact_acknowledgement_admits_the_instance_update_and_only_that_diff() -> None:
    reviewed = fx.with_entries(fx.adopted(), {INSTANCE: fx.instance_update("CU_2")})
    digest = gate.instance_update_digest(parse_plan(fx.dumps(reviewed)))

    passed, _workspace, _built = _evaluate(reviewed, ack=digest)
    assert passed.codes == ()

    # The pre-apply gate recomputes the diff: a changed diff no longer matches.
    changed = fx.with_entries(fx.adopted(), {INSTANCE: fx.instance_update("CU_4")})
    refused, _workspace, _built = _evaluate(changed, ack=digest, position="pre-apply")
    assert refused.codes == ("instance_update_unacked",)
    assert refused.refusals[0].detail["acknowledged"] is True

    wrong, _workspace, _built = _evaluate(reviewed, ack="0" * 64)
    assert wrong.codes == ("instance_update_unacked",)


def test_an_instance_resize_needs_the_acknowledgement_too() -> None:
    body = fx.instance_update()
    body["action"] = "resize"
    doc = fx.with_entries(fx.adopted(), {INSTANCE: body})

    report, _workspace, _built = _evaluate(doc)

    assert report.codes == ("instance_update_unacked",)


# --- the not-adopted fold (attack O14) ---------------------------------------------


def test_an_empty_state_plan_against_an_existing_install_is_exactly_one_not_adopted() -> None:
    workspace = fx.RecordingWorkspace(fx.existing_install())

    report, workspace, _built = _evaluate(fx.first_install(), workspace)

    assert report.codes == ("not_adopted",)
    refusal = report.refusals[0]
    assert refusal.resource == f"apps.{fx.APP_KEY}"
    assert sorted(refusal.related) == sorted(
        f"{kind}.{key}" for kind, key in fx.declared_resources()
    )
    assert len(refusal.detail["collisions"]) == len(fx.declared_resources())
    assert {item["kind"] for item in refusal.detail["collisions"]} == {
        kind for kind, _ in fx.declared_resources()
    }
    assert len(workspace.lookup_calls()) == len(fx.declared_resources())


def test_a_partly_existing_install_still_folds_into_one_not_adopted() -> None:
    workspace = fx.RecordingWorkspace({"apps": {"synthetic-mip_app"}})

    report, _workspace, _built = _evaluate(fx.first_install(), workspace)

    assert report.codes == ("not_adopted",)
    assert report.refusals[0].related == ("apps.mip_app",)


@pytest.mark.parametrize(
    ("service", "found"),
    (
        ("jobs", "synthetic-mip_ref_seed"),
        ("warehouses", "synthetic-mip_serverless_sql"),
        ("experiments", "synthetic-mip_lead_scoring"),
    ),
)
def test_a_stray_same_name_object_on_a_first_install_is_a_plain_collision(
    service: str, found: str
) -> None:
    # No App, so no install to adopt: the actionable collision remedy stays.
    workspace = fx.RecordingWorkspace({service: {found}})

    report, _workspace, _built = _evaluate(fx.first_install(), workspace)

    assert report.codes == ("collision",)
    refusal = report.refusals[0]
    assert refusal.related == () and refusal.remedy == gate.REMEDIES["collision"]
    assert refusal.detail["names"] == [found]
    app = next(record for record in report.lookups if record.resource == "apps.mip_app")
    assert app.outcome == "absent"


def test_collisions_do_not_fold_when_the_state_binds_the_app() -> None:
    doc = fx.with_entries(
        fx.first_install(),
        {APP: fx.entry("skip", remote={"name": "synthetic-mip_app"})},
    )
    workspace = fx.RecordingWorkspace(fx.existing_install())

    report, _workspace, _built = _evaluate(doc, workspace)

    assert set(report.codes) == {"collision"}
    assert len(report.codes) == len(fx.declared_resources()) - 1


def test_collisions_do_not_fold_beside_another_refusal() -> None:
    workspace = fx.RecordingWorkspace(
        {"apps": {"synthetic-mip_app"}},
        failures={"jobs": PermissionDenied("no jobs read")},
    )

    report, _workspace, _built = _evaluate(fx.first_install(), workspace)

    assert "not_adopted" not in report.codes
    assert report.codes.count("collision") == 1
    assert set(report.codes) == {"collision", "lookup_failed"}


# --- lookups fail closed ----------------------------------------------------------------


def test_a_lookup_error_is_never_read_as_absent() -> None:
    doc = fx.with_entries(
        fx.adopted(),
        {JOB: fx.entry("create", value=fx.resolved_name("jobs", "mip_scheduled_refresh"))},
    )
    workspace = fx.RecordingWorkspace(failures={"jobs": PermissionDenied("denied")})

    report, _workspace, _built = _evaluate(doc, workspace)

    assert report.codes == ("lookup_failed",)
    assert report.refusals[0].detail["reason"] == "failed: PermissionDenied"
    assert report.lookups[0].outcome == "failed: PermissionDenied"


@pytest.mark.parametrize("service", sorted({service for service, _ in fx.READ_ONLY_CALLS}))
def test_every_kinds_lookup_error_refuses_and_only_not_found_reads_absent(service: str) -> None:
    # Each GET treats only NotFound as "absent"; a list errors as a whole.
    workspace = fx.RecordingWorkspace(failures={service: PermissionDenied("denied")})

    report, workspace, _built = _evaluate(fx.first_install(), workspace)

    failed = [record for record in report.lookups if record.outcome.startswith("failed")]
    assert failed and {record.outcome for record in failed} == {"failed: PermissionDenied"}
    assert {call[0] for call in workspace.calls if call[0] == service}, "the failing lookup ran"
    assert set(report.codes) == {"lookup_failed"}, "never a pass, never a fold"
    assert len(report.codes) == len(failed)


def test_a_client_that_cannot_be_built_refuses_every_create() -> None:
    def factory() -> Any:
        raise RuntimeError("workspace is not the install's")

    report = gate.evaluate(
        parse_plan(fx.dumps(fx.first_install())),
        position="pre-lease",
        app_key=fx.APP_KEY,
        acknowledgement="",
        workspace_factory=factory,
    )

    assert set(report.codes) == {"lookup_failed"}
    assert len(report.codes) == len(fx.declared_resources())


@pytest.mark.parametrize(
    "body",
    (
        fx.entry(
            "create",
            value={"name": "${resources.jobs.other.name}"},
            refs={"name": "${resources.jobs.other.name}"},
        ),
        fx.entry("create", value={"name": ""}),
        fx.entry("create"),
    ),
    ids=("unresolved-reference", "empty-name", "no-new-state"),
)
def test_a_name_the_plan_has_not_resolved_refuses_without_a_lookup(body: dict[str, Any]) -> None:
    doc = fx.with_entries(fx.adopted(), {JOB: body})

    report, workspace, built = _evaluate(doc)

    assert report.codes == ("lookup_failed",)
    assert built == [] and workspace.calls == []


def test_a_dashboard_is_looked_up_by_its_workspace_path() -> None:
    key = "resources.dashboards.mip_executive_dashboard"
    doc = fx.with_entries(
        fx.adopted(),
        {key: fx.entry("create", value=fx.resolved_name("dashboards", "mip_executive_dashboard"))},
    )
    parent, display = fx.names_of("dashboards", "mip_executive_dashboard")
    workspace = fx.RecordingWorkspace({"workspace": {f"{parent}/{display}.lvdash.json"}})

    report, workspace, _built = _evaluate(doc, workspace)

    assert report.codes == ("collision",)
    assert workspace.lookup_calls()[0][2]["path"] == f"{parent}/{display}.lvdash.json"


def test_a_pipeline_lookup_compares_names_exactly_after_the_like_filter() -> None:
    key = "resources.pipelines.mip_feature_pipeline"
    doc = fx.with_entries(
        fx.adopted(), {key: fx.entry("create", value={"name": "mip_feature_pipeline"})}
    )
    # LIKE treats "_" as a wildcard, so the filter may return a near-miss.
    workspace = fx.RecordingWorkspace({"pipelines": {"mipXfeatureXpipeline"}})

    report, workspace, _built = _evaluate(doc, workspace)

    assert report.codes == ()
    assert workspace.lookup_calls()[0][2]["filter"] == "name LIKE 'mip_feature_pipeline'"


def test_the_app_entry_must_be_in_the_plan() -> None:
    doc = fx.adopted()
    del doc["plan"][APP]

    report, _workspace, _built = _evaluate(doc)

    assert report.codes == ("malformed_plan",)
    assert report.app_bound is False


# --- the lookups are read-only by construction -----------------------------------------


def test_every_sdk_call_in_the_gate_is_a_reviewed_get_or_list() -> None:
    tree = ast.parse(
        "\n".join(
            Path(module.__file__).read_text(encoding="utf-8") for module in (gate, lookups)
        )
    )
    calls = {
        (node.func.value.attr, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "workspace"
    }
    assert calls == fx.READ_ONLY_CALLS
    assert {spec.method for spec in gate.KIND_LOOKUPS.values()} | {lookups.VISIBILITY_METHOD} == {
        f"{s}.{m}" for s, m in fx.READ_ONLY_CALLS
    }
