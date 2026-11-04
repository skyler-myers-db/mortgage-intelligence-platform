"""The jobs identity gets exactly the workspace ACLs its jobs use, additively (M2-PR5).

tools/databricks/jobs_identity_acl.py, run by deploy step 4 before the first
job runs as the jobs identity:

* derives the grants from databricks.yml: the bundle files folder (CAN_READ),
  the warehouse every SQL task names (CAN_USE), the pipeline a pipeline task
  starts (CAN_RUN), each job a run_job_task starts (CAN_MANAGE_RUN) and the
  Lakebase instance a ``--lakebase-instance`` parameter names (CAN_USE). Every
  ``${resources.*}`` reference in a jobs-identity job is covered; an unreviewed
  reference or task kind, or a deployer-run child, refuses;
* resolves each to its live object from the bundle summary, and refuses a gap
  or a summary whose jobs do not run as ``--jobs-principal``;
* adds only what the identity lacks, by PATCH with one entry, never a set:
  other principals' grants (the App's CAN_MANAGE_RUN, the verifier's CAN_USE)
  are never sent, a higher level already held is never lowered, and a grant
  the postflight cannot see fails the step. ``plan`` never mutates, and no
  output names a principal or an object id.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import jobs_identity_acl as acl

ROOT = Path(__file__).resolve().parents[2]
BUNDLE: dict[str, Any] = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
JOBS_SP = "00000000-0000-4000-8000-0000000000c2"
APP_SP = "00000000-0000-4000-8000-0000000000aa"
FILES_PATH = "/Workspace/Users/deployer-placeholder/.bundle/mip/client-placeholder/files"
EXPECTED = {
    acl.Grant("workspace", "file_path", "CAN_READ"),
    acl.Grant("sql_warehouses", "mip_serverless_sql", "CAN_USE"),
    acl.Grant("pipelines", "mip_feature_pipeline", "CAN_RUN"),
    acl.Grant("jobs", "mip_fred_rates_ingest", "CAN_MANAGE_RUN"),
    acl.Grant("jobs", "mip_refresh_scores", "CAN_MANAGE_RUN"),
    acl.Grant("jobs", "mip_sync_lifecycle_state", "CAN_MANAGE_RUN"),
    acl.Grant("jobs", "mip_kpi_snapshot", "CAN_MANAGE_RUN"),
    acl.Grant("database_instances", "mip_app_state", "CAN_USE"),
}


def _on_jobs(kind: str) -> dict[str, Any]:
    return {key: r for key, r in BUNDLE["resources"][kind].items() if r.get("run_as") == acl.JOBS_IDENTITY}


# ------------------------------------------------------------- the grant set


def test_the_bundle_needs_exactly_the_reviewed_grants() -> None:
    assert acl.required_grants(BUNDLE) == EXPECTED


def test_every_resource_a_jobs_identity_job_or_pipeline_references_is_granted() -> None:
    """An independent walk: each ``${resources.*}`` reference, and every source file, has its grant."""

    granted = {(grant.kind, grant.key) for grant in EXPECTED}
    reference = re.compile(r"\$\{resources\.([a-z_]+)\.([A-Za-z0-9_]+)\.[a-z_]+\}")
    for kind in ("jobs", "pipelines"):
        for key, resource in _on_jobs(kind).items():
            for found in reference.findall(json.dumps(resource)):
                assert found in granted, f"{kind}.{key} references {found}"
    lakebase = [
        key
        for key, job in _on_jobs("jobs").items()
        for task in job["tasks"]
        if acl.LAKEBASE_PARAMETER in (task.get("spark_python_task") or {}).get("parameters", [])
    ]
    assert sorted(lakebase) == ["mip_kpi_snapshot", "mip_sync_lifecycle_state"]
    assert ("workspace", "file_path") in granted, "every task's source sits in the deployer's folder"


def _mutated(edit: Any) -> dict[str, Any]:
    bundle = copy.deepcopy(BUNDLE)
    edit(bundle["resources"])
    return bundle


def _add_task(job: str, task: dict[str, Any]) -> Any:
    return lambda resources: resources["jobs"][job]["tasks"].append(task)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            _add_task("mip_scheduled_refresh", {"task_key": "x", "run_job_task": {
                "job_id": "${resources.jobs.mip_lakebase_migrate.id}"}}),
            "starts jobs.mip_lakebase_migrate, which does not run as the jobs identity",
        ),
        (
            lambda r: r["pipelines"]["mip_feature_pipeline"].update(
                run_as={"service_principal_name": "${var.deployer_principal}"}),
            "starts pipelines.mip_feature_pipeline, which does not run as the jobs identity",
        ),
        (
            _add_task("mip_refresh_scores", {"task_key": "x", "notebook_task": {"notebook_path": "n"}}),
            "a task kind this step has not reviewed",
        ),
        (
            _add_task("mip_refresh_scores", {"task_key": "x", "dashboard_task": {
                "dashboard_id": "${resources.dashboards.mip_executive_dashboard.id}"}}),
            "a task kind this step has not reviewed",
        ),
        (
            lambda r: r["jobs"]["mip_fred_rates_ingest"].update(
                description="${resources.dashboards.mip_executive_dashboard.id}"),
            "references dashboards.mip_executive_dashboard, which this step has no grant for",
        ),
        (
            _add_task("mip_refresh_scores", {"task_key": "x", "sql_task": {"warehouse_id": "abc123", "file": {
                "path": "q.sql"}}}),
            "names a sql_warehouses that is not a bundle resource",
        ),
        (
            _add_task("mip_kpi_snapshot", {"task_key": "x", "spark_python_task": {
                "python_file": "jobs/x.py", "parameters": ["--lakebase-instance=another-instance"]}}),
            "names a Lakebase instance this step cannot resolve",
        ),
    ],
    ids=["deployer-child-job", "deployer-pipeline", "notebook", "dashboard-task", "stray-reference",
         "literal-warehouse", "other-instance"],
)
def test_an_unreviewed_dependency_refuses(edit: Any, message: str) -> None:
    with pytest.raises(acl.Refused, match=re.escape(message)):
        acl.required_grants(_mutated(edit))


# -------------------------------------------------------------- resolution


def _summary(**overrides: Any) -> dict[str, Any]:
    resources: dict[str, Any] = {
        "sql_warehouses": {"mip_serverless_sql": {"id": "wh-placeholder"}},
        "pipelines": {"mip_feature_pipeline": {"id": "pl-placeholder", "run_as": {"service_principal_name": JOBS_SP}}},
        "database_instances": {"mip_app_state": {"name": "mip-app-state"}},
        "jobs": {
            key: {"id": f"{index}", "run_as": job["run_as"]}
            for index, (key, job) in enumerate(sorted(BUNDLE["resources"]["jobs"].items()), start=100)
        },
    }
    for job in resources["jobs"].values():
        if job["run_as"] == acl.JOBS_IDENTITY:
            job["run_as"] = {"service_principal_name": JOBS_SP}
    summary = {"workspace": {"file_path": FILES_PATH}, "resources": resources}
    for path, value in overrides.items():
        node = summary
        *parents, leaf = path.split("/")
        for parent in parents:
            node = node[parent]
        node[leaf] = value
    return summary


def _targets(summary: dict[str, Any] | None = None) -> list[acl.Target]:
    return acl.resolve(EXPECTED, summary or _summary(), jobs_principal=JOBS_SP, bundle=BUNDLE)


def test_each_grant_resolves_to_its_live_object() -> None:
    by_label = {target.grant.label: (target.object_type, target.reference) for target in _targets()}
    assert by_label["workspace.file_path CAN_READ"] == ("directories", FILES_PATH)
    assert by_label["sql_warehouses.mip_serverless_sql CAN_USE"] == ("warehouses", "wh-placeholder")
    assert by_label["pipelines.mip_feature_pipeline CAN_RUN"] == ("pipelines", "pl-placeholder")
    assert by_label["database_instances.mip_app_state CAN_USE"] == ("database-instances", "mip-app-state")
    assert by_label["jobs.mip_kpi_snapshot CAN_MANAGE_RUN"][0] == "jobs"


@pytest.mark.parametrize(
    ("overrides", "principal", "message"),
    [
        ({"resources/sql_warehouses/mip_serverless_sql": {}}, JOBS_SP, "no concrete object for sql_warehouses"),
        ({"workspace": {"file_path": "${workspace.root_path}/files"}}, JOBS_SP, "no concrete object for workspace"),
        ({"resources/pipelines/mip_feature_pipeline/run_as": {"service_principal_name": APP_SP}}, JOBS_SP,
         "pipelines.mip_feature_pipeline does not run as --jobs-principal"),
        ({}, APP_SP, "does not run as --jobs-principal"),
        ({}, "00000000-0000-0000-0000-000000000000", "not a placeholder"),
        ({}, "  ", "not a placeholder"),
    ],
    ids=["no-id", "unresolved-path", "pipeline-identity", "other-principal", "zero-placeholder", "blank"],
)
def test_a_summary_gap_or_mismatch_refuses(overrides: dict[str, Any], principal: str, message: str) -> None:
    with pytest.raises(acl.Refused, match=re.escape(message)):
        acl.resolve(EXPECTED, _summary(**overrides), jobs_principal=principal, bundle=BUNDLE)


# --------------------------------------------------------------- convergence


def _entry(principal: str, *levels: str, inherited: bool = False, kind: str = "service_principal_name") -> Any:
    return SimpleNamespace(
        **{kind: principal},
        all_permissions=[SimpleNamespace(permission_level=level, inherited=inherited) for level in levels],
    )


class FakeWorkspace:
    """The permissions API over in-memory ACLs; ``update`` merges like PATCH and is the only write."""

    def __init__(self, acls: dict[tuple[str, str], list[Any]], *, sticky: bool = True) -> None:
        self.acls, self.sticky = acls, sticky
        self.updates: list[tuple[str, str, list[Any]]] = []
        self.permissions = SimpleNamespace(get=self._get, update=self._update, set=self._forbidden)
        self.workspace = SimpleNamespace(get_status=lambda path: SimpleNamespace(object_id="dir-1"))

    def _get(self, object_type: str, object_id: str) -> Any:
        return SimpleNamespace(access_control_list=list(self.acls.get((object_type, object_id), [])))

    def _update(self, object_type: str, object_id: str, *, access_control_list: list[Any]) -> Any:
        self.updates.append((object_type, object_id, access_control_list))
        if self.sticky:
            for request in access_control_list:
                level = request.permission_level.value
                self.acls.setdefault((object_type, object_id), []).append(
                    _entry(request.service_principal_name, level)
                )
        return self._get(object_type, object_id)

    def _forbidden(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a set would replace the object's whole ACL")


def _live_acls() -> dict[tuple[str, str], list[Any]]:
    jobs = {target.grant.key: target.reference for target in _targets() if target.object_type == "jobs"}
    return {
        ("directories", "dir-1"): [_entry("deployer-placeholder", "CAN_MANAGE", kind="user_name")],
        # The App's binding and the verifier's grant: never sent, never touched.
        ("warehouses", "wh-placeholder"): [_entry(APP_SP, "CAN_USE"), _entry("verifier", "CAN_USE")],
        # The pipeline's run_as already owns it: nothing to add, and never lowered.
        ("pipelines", "pl-placeholder"): [_entry(JOBS_SP.upper(), "IS_OWNER")],
        ("jobs", jobs["mip_refresh_scores"]): [_entry(APP_SP, "CAN_MANAGE_RUN")],
        # An inherited view level does not include a run.
        ("jobs", jobs["mip_kpi_snapshot"]): [_entry(JOBS_SP, "CAN_VIEW", inherited=True)],
    }


def test_apply_adds_only_the_missing_grants_with_one_entry_each() -> None:
    client = FakeWorkspace(_live_acls())
    granted = acl.converge(client, _targets(), JOBS_SP)

    assert sorted(target.grant.label for target in granted) == sorted(
        grant.label for grant in EXPECTED if grant.key != "mip_feature_pipeline"
    )
    for object_type, _object_id, entries in client.updates:
        assert len(entries) == 1, object_type
        (entry,) = entries
        assert entry.service_principal_name == JOBS_SP and entry.user_name is None and entry.group_name is None
        assert entry.permission_level.value in acl.SATISFIES[object_type]
    assert ("pipelines", "pl-placeholder") not in {(t, i) for t, i, _ in client.updates}
    # A second apply is a no-op.
    client.updates.clear()
    assert acl.converge(client, _targets(), JOBS_SP) == [] and client.updates == []


def test_plan_reads_only() -> None:
    client = FakeWorkspace(_live_acls())
    gaps = acl.missing(client, _targets(), JOBS_SP)
    assert len(gaps) == len(EXPECTED) - 1 and client.updates == []


def test_a_grant_the_postflight_cannot_see_fails_the_step() -> None:
    client = FakeWorkspace(_live_acls(), sticky=False)
    with pytest.raises(RuntimeError, match="postflight found grants still missing: .*jobs.mip_kpi_snapshot"):
        acl.converge(client, _targets(), JOBS_SP)


def test_main_prints_bundle_keys_only(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(_summary()), encoding="utf-8")
    monkeypatch.chdir(ROOT)
    args = ["--bundle-summary", str(summary), "--jobs-principal", JOBS_SP]

    client = FakeWorkspace(_live_acls())
    assert acl.main(["plan", *args], client=client) == 0 and client.updates == []
    assert acl.main(["apply", *args], client=client) == 0
    assert acl.main(["apply", *args], client=FakeWorkspace(_live_acls(), sticky=False)) == 3
    out = capsys.readouterr()
    text = out.out + out.err
    assert "granted jobs.mip_kpi_snapshot CAN_MANAGE_RUN" in text
    for value in (JOBS_SP, APP_SP, "wh-placeholder", "pl-placeholder", "dir-1", FILES_PATH):
        assert value not in text, value

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_summary(**{"resources/sql_warehouses/mip_serverless_sql": {}})), encoding="utf-8")
    assert acl.main(["apply", "--bundle-summary", str(bad), "--jobs-principal", JOBS_SP], client=client) == 2
    assert "nothing was granted" in capsys.readouterr().err


# ------------------------------------------------------------ the deploy step


def test_deploy_grants_them_after_the_bundle_apply_and_before_any_jobs_identity_run() -> None:
    text = deploy_entrypoint_text()
    step = "-m tools.databricks.jobs_identity_acl apply"
    assert text.count(step) == 1
    position = text.index(step)
    assert text.index("bundle_env deploy -t \"$TARGET\"") < position
    assert '--jobs-principal "$CLIENT_JOBS_PRINCIPAL"' in text[position : position + 200]
    for key in sorted(_on_jobs("jobs")):
        runs = [m.start() for m in re.finditer(rf"bundle run {key}\b", text)]
        assert all(position < start for start in runs), f"{key} runs before its grants"
    assert re.search(r"bundle run mip_refresh_scores\b", text), "the positive control: a jobs-identity run exists"
