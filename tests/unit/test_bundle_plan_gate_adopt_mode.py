"""The bundle plan gate's adopt mode, on sanitized plans (M2-PR6).

Right after an adoption binds the legacy map, the plan must hold no delete,
recreate or Lakebase instance change, exactly the expected creates, no
dashboard move, and only renames to names proven free.
"""

from __future__ import annotations

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tools.databricks import bundle_plan_gate as gate
from tools.databricks.bundle_plan_adopt import AdoptExpectation
from tools.databricks.bundle_plan_shape import parse_plan

NEW = ("jobs.mip_growth_agent_monitor_scheduler", "jobs.mip_scheduled_refresh")
JOB = "resources.jobs.mip_ref_seed"
DASHBOARD = "resources.dashboards.mip_executive_dashboard"
INSTANCE = "resources.database_instances.mip_app_state"


def _declared() -> frozenset[str]:
    return frozenset(f"{kind}.{key}" for kind, key in fx.declared_resources())


def _adopted_with_two_creates() -> dict[str, object]:
    return fx.with_entries(
        fx.adopted(),
        {f"resources.{key}": fx.entry("create", value=fx.resolved_name(*key.split("."))) for key in NEW},
    )


def _evaluate(doc: dict[str, object], workspace: fx.RecordingWorkspace | None = None, *, ack: str = "") -> gate.PlanReport:
    workspace = workspace or fx.RecordingWorkspace()
    return gate.evaluate(
        parse_plan(fx.dumps(doc)),
        position="post-bind",
        app_key=fx.APP_KEY,
        acknowledgement=ack,
        workspace_factory=lambda: workspace,
        adopt=AdoptExpectation(_declared() - set(NEW), frozenset(NEW)),
    )


def _rename(new_name: str) -> dict[str, object]:
    return fx.entry(
        "update",
        value={"name": new_name},
        changes={"name": {"action": "update", "old": "[dev example] x", "new": new_name, "remote": "[dev example] x"}},
    )


def test_exactly_the_expected_creates_pass_and_are_looked_up() -> None:
    workspace = fx.RecordingWorkspace()

    report = _evaluate(_adopted_with_two_creates(), workspace)

    assert report.codes == ()
    assert report.counts["create"] == 2
    assert report.public_summary()["adopt"] == {
        "bound": 16,
        "expected_creates": list(NEW),
        "dashboard_parent_path": "pinned_permanent",
    }
    assert workspace.mutating_calls() == []


@pytest.mark.parametrize(
    ("entries", "extra", "missing"),
    (
        ({}, [], []),
        ({"resources.jobs.mip_ref_seed": fx.entry("create", value={"name": "synthetic-mip_ref_seed"})}, ["jobs.mip_ref_seed"], []),
        ({"resources.jobs.mip_scheduled_refresh": fx.entry("skip")}, [], ["jobs.mip_scheduled_refresh"]),
        ({"resources.jobs.mip_ref_seed.permissions": fx.entry("create", value={"object_id": "/jobs/1"})}, ["jobs.mip_ref_seed.permissions"], []),
    ),
    ids=("exact", "an-unbound-legacy-key", "a-missing-create", "a-grant-create"),
)
def test_the_creates_must_be_exactly_the_bundle_minus_the_bound_keys(
    entries: dict[str, object], extra: list[str], missing: list[str]
) -> None:
    report = _evaluate(fx.with_entries(_adopted_with_two_creates(), entries))

    if not extra and not missing:
        assert report.codes == ()
        return
    mismatch = [r for r in report.refusals if r.code == "create_set_mismatch"]
    assert len(mismatch) == 1 and mismatch[0].resource == "bundle.plan"
    assert mismatch[0].detail["extra"] == extra and mismatch[0].detail["missing"] == missing


def test_a_dashboard_recreate_refuses_as_its_own_code() -> None:
    report = _evaluate(fx.with_entries(_adopted_with_two_creates(), {DASHBOARD: fx.entry("recreate")}))

    assert report.codes == ("dashboard_recreate",)
    assert "spike S4" in report.refusals[0].remedy.replace("Spike", "spike")


def test_a_dashboard_parent_path_update_refuses_as_a_move() -> None:
    move = fx.entry(
        "update",
        value={"parent_path": "/Workspace/Users/deployer@example.invalid/x/resources", "display_name": "synthetic-d"},
        changes={"parent_path": {"action": "update", "old": "/a", "new": "/b", "remote": "/a"}},
    )

    report = _evaluate(fx.with_entries(_adopted_with_two_creates(), {DASHBOARD: move}))

    assert report.codes == ("dashboard_move",)


def test_an_instance_change_refuses_even_with_the_release_ack() -> None:
    doc = fx.with_entries(_adopted_with_two_creates(), {INSTANCE: fx.instance_update()})
    digest = gate.instance_update_digest(parse_plan(fx.dumps(doc)))

    report = _evaluate(doc, ack=digest)

    assert report.codes == ("instance_change",)
    assert report.instance_update_sha256 == digest, "the diff is still reported"


@pytest.mark.parametrize("action", ("delete", "recreate", "update_id"))
def test_deletes_recreates_and_identity_renames_still_refuse(action: str) -> None:
    report = _evaluate(fx.with_entries(_adopted_with_two_creates(), {JOB: fx.entry(action)}))

    assert report.codes == ({"update_id": "id_change"}.get(action, action),)


def test_a_rename_is_looked_up_first_and_refuses_onto_an_existing_name() -> None:
    free = fx.RecordingWorkspace()
    taken = fx.RecordingWorkspace(existing={"jobs": {"synthetic-mip_ref_seed"}})
    doc = fx.with_entries(_adopted_with_two_creates(), {JOB: _rename("synthetic-mip_ref_seed")})

    assert _evaluate(doc, free).codes == ()
    report = _evaluate(doc, taken)

    assert report.codes == ("rename_collision",)
    lookups = {record.resource: record.outcome for record in report.lookups}
    assert lookups["jobs.mip_ref_seed"] == "exists"
    assert ("jobs", "list", {"args": (), "name": "synthetic-mip_ref_seed"}) in taken.calls


def test_a_rename_that_cannot_be_proven_free_refuses() -> None:
    unresolved = fx.entry(
        "update",
        value={"name": "${var.x}"},
        refs={"name": "${var.x}"},
        changes={"name": {"action": "update", "old": "a", "new": "b", "remote": "a"}},
    )
    not_admin = fx.RecordingWorkspace(admin=False)

    assert _evaluate(fx.with_entries(_adopted_with_two_creates(), {JOB: unresolved})).codes == ("lookup_failed",)
    assert _evaluate(fx.with_entries(_adopted_with_two_creates(), {JOB: _rename("y")}), not_admin).codes == (
        "lookup_failed",
        "lookup_failed",
        "lookup_failed",
    ), "the two creates and the rename"


def test_adopt_mode_never_folds_into_not_adopted() -> None:
    app_create = {"resources.apps.mip_app": fx.entry("create", value=fx.resolved_name("apps", fx.APP_KEY))}
    workspace = fx.RecordingWorkspace(existing={"apps": {fx.names_of("apps", fx.APP_KEY)[0]}})

    report = _evaluate(fx.with_entries(_adopted_with_two_creates(), app_create), workspace)

    assert "not_adopted" not in report.codes
    assert set(report.codes) == {"collision", "create_set_mismatch"}


def test_adopt_mode_is_exactly_the_post_bind_position() -> None:
    plan = parse_plan(fx.dumps(fx.adopted()))
    expectation = AdoptExpectation(frozenset(), frozenset())
    with pytest.raises(ValueError):
        gate.evaluate(plan, position="pre-lease", app_key="mip_app", acknowledgement="", workspace_factory=object, adopt=expectation)
    with pytest.raises(ValueError):
        gate.evaluate(plan, position="post-bind", app_key="mip_app", acknowledgement="", workspace_factory=object)
