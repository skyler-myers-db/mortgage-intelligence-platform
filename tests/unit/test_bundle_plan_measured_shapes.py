"""The parser, the adopt map and the gate on the shapes spikes S9 and S10 measured (wave 7f).

The documents are synthetic rebuilds (tests/fixtures/bundle_plans/): S10's
fresh-root plan of the whole bundle, the first document 10B-1 parses, and
S9's experiment documents. Each test pins what the spike measured on CLI
v1.10.0: the parser accepts every one, the census of S10's document, the adopt
map's names, and the gate's verdicts with the recording workspace double.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures import bundle_plan_fixtures as fx
from tests.fixtures import bundle_plans as shapes
from tools.databricks import adopt_legacy_map
from tools.databricks import bundle_plan_gate as gate
from tools.databricks import monitor_scheduler_gate
from tools.databricks.bundle_plan_adopt import POSITION, AdoptExpectation
from tools.databricks.bundle_plan_instance_ack import instance_update_digest
from tools.databricks.bundle_plan_lookups import KIND_LOOKUPS
from tools.databricks.bundle_plan_shape import parse_plan

REPO = Path(__file__).resolve().parents[2]
REFERENCE = re.compile(r"\$\{resources\.[a-z_]+\.[A-Za-z0-9_-]+\.[a-z_]+\}")
INSTANCE_NAME = "${resources.database_instances.mip_app_state.name}"
EXPERIMENT = "experiments.mip_lead_scoring"
EXPERIMENT_NAME = "/Shared/mip-lead-scoring"
APP_NAME = "synthetic-mip-app"
# S10's calls, as evaluate made them pre-lease with nothing existing (admin).
S10_LOOKUP_CALLS = {
    ("current_user", "me"): 1,
    ("apps", "get"): 1,
    ("catalogs", "get"): 1,
    ("database", "get_database_instance"): 1,
    ("experiments", "get_by_name"): 1,
    ("jobs", "list"): 10,
    ("pipelines", "list_pipelines"): 1,
    ("warehouses", "list"): 1,
    ("workspace", "get_status"): 2,
}


def _at(value: Any, path: str) -> Any:
    """The value at a ``new_state.vars`` path (``tasks[1].run_job_task.job_id``)."""

    for part in path.split("."):
        name, _, rest = part.partition("[")
        value = value[name]
        for index in re.findall(r"(\d+)\]", "[" + rest if rest else ""):
            value = value[int(index)]
    return value


def _fresh() -> dict[str, Any]:
    return shapes.load("fresh_root_create")


@pytest.mark.parametrize("name", shapes.NAMES)
def test_the_parser_accepts_every_measured_shape(name: str) -> None:
    plan = parse_plan(shapes.text(name))

    assert (plan.plan_version, plan.cli_version) == (2, "1.10.0")
    assert all("." not in entry.kind for entry in plan.entries), "no child permissions or grants node"


# --- S10: the whole bundle on a fresh root ------------------------------------------------


def test_the_fresh_root_plan_is_18_creates_of_the_declared_keys() -> None:
    plan = parse_plan(shapes.text("fresh_root_create"))

    assert (plan.lineage, plan.serial) == ("", 0) and set(_fresh()) == {"plan_version", "cli_version", "plan"}
    assert {entry.action for entry in plan.entries} == {"create"}
    assert Counter(entry.kind for entry in plan.entries) == {
        "jobs": 10, "dashboards": 2, "apps": 1, "database_instances": 1, "database_catalogs": 1,
        "sql_warehouses": 1, "pipelines": 1, "experiments": 1,
    }  # fmt: skip
    assert {entry.kind for entry in plan.entries} == set(KIND_LOOKUPS)
    # S10 measured the plan's keys equal to databricks.yml's; a new declared key needs this fixture rebuilt.
    assert sorted(entry.symbolic for entry in plan.entries) == sorted(f"{k}.{key}" for k, key in fx.declared_resources())


def test_depends_on_is_22_full_form_labels_on_11_entries_from_the_bundles_references() -> None:
    plan = _fresh()["plan"]
    edges = [(key, edge) for key, entry in plan.items() for edge in entry.get("depends_on", [])]

    assert len(edges) == 22 and len({key for key, _ in edges}) == 11
    for _key, edge in edges:
        assert set(edge) == {"node", "label"} and edge["node"] in plan
        assert re.fullmatch(re.escape("${" + edge["node"] + ".") + r"(id|name)\}", edge["label"]), edge
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")
    assert {edge["label"] for _key, edge in edges} == set(REFERENCE.findall(bundle))
    assert len({edge["label"] for _key, edge in edges}) == 9


def test_vars_hold_the_reference_in_a_string_field_and_0_in_an_int_field() -> None:
    plan = _fresh()["plan"]
    refs = {key: entry["new_state"].get("vars", {}) for key, entry in plan.items()}
    with_vars = {key: paths for key, paths in refs.items() if paths}

    assert len(with_vars) == 10 and sum(map(len, with_vars.values())) == 71
    assert len({ref for paths in with_vars.values() for ref in paths.values()}) == 8
    ints = []
    for key, paths in with_vars.items():
        labels = {edge["label"] for edge in plan[key].get("depends_on", [])}
        for path, ref in paths.items():
            assert ref in labels, (key, path)
            held = _at(plan[key]["new_state"]["value"], path)
            assert held == ref or (held == 0 and path.endswith("run_job_task.job_id")), (key, path)
            if held == 0:
                ints.append(f"{key}:{path}")
    assert sorted(ints) == [f"resources.jobs.mip_scheduled_refresh:tasks[{i}].run_job_task.job_id" for i in range(1, 5)]
    # The instance name resolves at plan time: two edges, no vars path.
    holders = sorted(key for key, entry in plan.items() if INSTANCE_NAME in {e["label"] for e in entry.get("depends_on", [])})
    assert holders == ["resources.apps.mip_app", "resources.database_catalogs.mip_app_state_catalog"]
    assert all(INSTANCE_NAME not in paths.values() for paths in refs.values())
    bare = sorted(key for key, entry in plan.items() if "depends_on" not in entry and not refs[key])
    assert bare == [
        "resources.database_instances.mip_app_state",
        "resources.experiments.mip_lead_scoring",
        "resources.jobs.mip_growth_agent_monitor_scheduler",
        "resources.jobs.mip_kpi_snapshot",
        "resources.jobs.mip_lakebase_migrate",
        "resources.pipelines.mip_feature_pipeline",
        "resources.sql_warehouses.mip_serverless_sql",
    ]


def test_the_app_and_dashboard_values_are_the_measured_fields() -> None:
    plan = _fresh()["plan"]
    app = plan["resources.apps.mip_app"]["new_state"]["value"]

    assert set(app) == {"description", "name", "resources", "source_code_path"} and len(app["resources"]) == 14
    for key in ("resources.dashboards.mip_executive_dashboard", "resources.dashboards.mip_segment_dashboard"):
        assert set(plan[key]["new_state"]["value"]) == {
            "display_name", "embed_credentials", "parent_path", "published", "serialized_dashboard", "warehouse_id",
        }  # fmt: skip


def test_the_adopt_map_resolves_every_name_of_the_fresh_root_plan() -> None:
    plan = parse_plan(shapes.text("fresh_root_create"))

    names = {entry.symbolic: adopt_legacy_map._expected_names(entry) for entry in plan.entries}

    assert all(reason == "" and resolved for resolved, reason in names.values()), names
    assert names[EXPERIMENT] == ((EXPERIMENT_NAME,), "")
    assert names["sql_warehouses.mip_serverless_sql"] == (("mip_serverless_sql",), "")
    assert names["pipelines.mip_feature_pipeline"] == (("mip_feature_pipeline",), "")
    assert names["jobs.mip_init_catalog_schemas"] == (("mip_init_catalog_schemas",), "")
    for key in ("mip_executive_dashboard", "mip_segment_dashboard"):
        assert names[f"dashboards.{key}"] == ((f"{fx.DEPLOYER_ROOT}/resources", key), "")
    assert names["apps.mip_app"] == ((APP_NAME,), "")


def _pre_lease(plan: Any, workspace: fx.RecordingWorkspace) -> Any:
    return gate.evaluate(plan, position="pre-lease", app_key="mip_app", acknowledgement="", workspace_factory=lambda: workspace)


def test_the_pre_lease_gate_passes_a_fresh_root_read_only_as_s10_measured() -> None:
    plan = parse_plan(shapes.text("fresh_root_create"))
    workspace = fx.RecordingWorkspace(admin=True)

    report = _pre_lease(plan, workspace)

    assert report.refusals == () and not report.app_bound
    assert len(report.lookups) == 18 and {record.outcome for record in report.lookups} == {"absent"}
    assert Counter((service, method) for service, method, _ in workspace.calls) == S10_LOOKUP_CALLS
    assert workspace.mutating_calls() == []
    assert instance_update_digest(plan) == ""


def test_the_pre_lease_gate_folds_an_existing_app_into_not_adopted() -> None:
    plan = parse_plan(shapes.text("fresh_root_create"))
    workspace = fx.RecordingWorkspace({"apps": {APP_NAME}}, admin=True)

    report = _pre_lease(plan, workspace)

    assert [(r.code, r.resource) for r in report.refusals] == [("not_adopted", "apps.mip_app")]
    assert workspace.mutating_calls() == []


def test_a_caller_that_is_not_a_workspace_admin_proves_no_name_free() -> None:
    plan = parse_plan(shapes.text("fresh_root_create"))

    report = _pre_lease(plan, fx.RecordingWorkspace(admin=False))

    assert Counter(report.codes) == {"lookup_failed": 17}, "the metastore-scoped catalog lookup needs no admin proof"


def test_the_monitor_scheduler_stays_paused_in_the_fresh_root_plan() -> None:
    text = shapes.text("fresh_root_create")

    assert monitor_scheduler_gate.planned_refusals(parse_plan(text)) == []
    assert monitor_scheduler_gate.check(text, workspace_factory=lambda: fx.RecordingWorkspace(admin=True)) == []


# --- S9: one experiment, bound and unbound -------------------------------------------------


def _with_app(name: str) -> Any:
    """S9's one-resource bundle had no App; the gate needs the App entry, so splice a bound one."""

    doc = shapes.load(name)
    doc["plan"]["resources.apps.mip_app"] = {"action": "skip"}
    return parse_plan(fx.dumps(doc))


def _post_bind(plan: Any, workspace: fx.RecordingWorkspace) -> Any:
    expectation = AdoptExpectation(frozenset({EXPERIMENT, "apps.mip_app"}), frozenset())
    return gate.evaluate(
        plan, position=POSITION, app_key="mip_app", acknowledgement="", workspace_factory=lambda: workspace, adopt=expectation
    )


def test_the_experiment_documents_carry_the_measured_changes() -> None:
    created = parse_plan(shapes.text("experiment_create"))
    skipped = parse_plan(shapes.text("experiment_skip")).entry(f"resources.{EXPERIMENT}")
    updated = parse_plan(shapes.text("experiment_update")).entry(f"resources.{EXPERIMENT}")
    recreated = parse_plan(shapes.text("experiment_recreate")).entry(f"resources.{EXPERIMENT}")
    unbound = parse_plan(shapes.text("experiment_unbound"))

    assert (created.lineage, created.serial) == ("", 0) and unbound.lineage and unbound.serial == 6
    assert skipped is not None and skipped.new_value is None
    assert {path: change.reason for path, change in skipped.changes.items()} == {
        "artifact_location": "backend_default",
        "tags": "terraform_compat",
        "tags[0].value": "terraform_compat",
    }
    assert {change.action for change in skipped.changes.values()} == {"skip"}
    assert updated is not None and (updated.changes["name"].action, updated.changes["name"].reason) == ("update", "")
    assert recreated is not None
    assert (recreated.changes["artifact_location"].action, recreated.changes["artifact_location"].reason) == ("recreate", "immutable")
    for plan in (created, unbound):
        assert adopt_legacy_map._expected_names(plan.entries[0]) == ((EXPERIMENT_NAME,), "")


def test_a_one_resource_plan_has_no_app_entry_to_judge() -> None:
    report = _post_bind(parse_plan(shapes.text("experiment_skip")), fx.RecordingWorkspace())

    assert [(r.code, r.detail["reason"]) for r in report.refusals] == [("malformed_plan", "no apps.mip_app")]


@pytest.mark.parametrize(
    ("name", "existing", "codes"),
    (
        ("experiment_skip", set(), ()),
        ("experiment_update", set(), ()),
        ("experiment_update", {EXPERIMENT_NAME}, ("rename_collision",)),
        ("experiment_recreate", set(), ("recreate",)),
        ("experiment_unbound", {EXPERIMENT_NAME}, ("collision", "create_set_mismatch")),
    ),
    ids=("skip", "update-free", "update-taken", "recreate", "unbound"),
)
def test_the_post_bind_gate_gives_s9s_verdicts(name: str, existing: set[str], codes: tuple[str, ...]) -> None:
    workspace = fx.RecordingWorkspace({"experiments": existing})

    report = _post_bind(_with_app(name), workspace)

    assert report.codes == codes
    assert workspace.mutating_calls() == []


def test_an_unbound_experiment_beside_an_existing_app_is_not_adopted_pre_lease() -> None:
    doc = shapes.load("experiment_unbound")
    doc["plan"]["resources.apps.mip_app"] = {"action": "create", "new_state": {"value": {"name": APP_NAME}}}
    workspace = fx.RecordingWorkspace({"apps": {APP_NAME}, "experiments": {EXPERIMENT_NAME}})

    report = _pre_lease(parse_plan(fx.dumps(doc)), workspace)

    assert [(r.code, r.related) for r in report.refusals] == [("not_adopted", ("apps.mip_app", EXPERIMENT))]
