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
  output names a principal or an object id;
* before any grant, reads each jobs-identity job's and pipeline's LIVE
  run_as and refuses one that runs as anyone else (``live_run_as_drift``) or
  cannot be read (``live_run_as_unreadable``): the summary is configuration,
  and the direct-engine plan cannot see a pipeline's run_as (spike S4).

``FakeWorkspace`` answers as the APIs did in spike S4: ``jobs.get`` and
``pipelines.get`` carry the effective ``run_as_user_name`` (a pipeline's
``spec`` never carries run_as), and a permissions ``set`` replaces the whole
ACL, which the measured ``jobs set-permissions`` did.
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
from databricks.sdk.errors import NotFound

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


def _run_as_ids() -> dict[tuple[str, str], str]:
    """Every jobs-identity job and pipeline id of ``_summary()``, running as the jobs SP."""

    return {(kind, live_id): JOBS_SP for kind, _key, live_id in acl.run_as_targets(_summary(), bundle=BUNDLE)}


class FakeWorkspace:
    """The permissions API over in-memory ACLs, and the job and pipeline GETs, as spike S4 measured them.

    ``update`` merges like PATCH. ``set`` replaces the whole ACL, as the
    measured ``jobs set-permissions`` did; it is recorded in ``sets`` and no
    caller here may use it. ``run_as`` maps ``(kind, id)`` to the effective
    ``run_as_user_name`` its GET answers; a pipeline's ``spec`` carries none.
    """

    def __init__(self, acls: dict[tuple[str, str], list[Any]], *, sticky: bool = True,
                 run_as: dict[tuple[str, str], str] | None = None) -> None:
        self.acls, self.sticky = acls, sticky
        self.updates: list[tuple[str, str, list[Any]]] = []
        self.sets: list[tuple[str, str, list[Any]]] = []
        self.run_as = _run_as_ids() if run_as is None else run_as
        self.permissions = SimpleNamespace(get=self._get, update=self._update, set=self._set)
        self.workspace = SimpleNamespace(get_status=lambda path: SimpleNamespace(object_id="dir-1"))
        self.jobs = SimpleNamespace(get=self._job)
        self.pipelines = SimpleNamespace(get=self._pipeline)

    def _identity(self, kind: str, live_id: str) -> str:
        if (kind, live_id) not in self.run_as:
            raise NotFound(f"{kind} not found")
        return self.run_as[(kind, live_id)]

    def _job(self, *, job_id: int) -> Any:
        identity = self._identity("jobs", str(job_id))
        return SimpleNamespace(
            job_id=job_id,
            run_as_user_name=identity,
            settings=SimpleNamespace(run_as=SimpleNamespace(service_principal_name=identity, user_name=None)),
        )

    def _pipeline(self, *, pipeline_id: str) -> Any:
        identity = self._identity("pipelines", pipeline_id)
        return SimpleNamespace(pipeline_id=pipeline_id, run_as_user_name=identity, spec=SimpleNamespace(name="p"))

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

    def _set(self, object_type: str, object_id: str, *, access_control_list: list[Any]) -> Any:
        self.sets.append((object_type, object_id, access_control_list))
        self.acls[(object_type, object_id)] = [
            _entry(request.service_principal_name, request.permission_level.value) for request in access_control_list
        ]
        return self._get(object_type, object_id)


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
    assert client.sets == [], "a set would replace each object's whole ACL"

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


# ------------------------------------------------- the live run_as postflight

PIPELINE = ("pipelines", "pl-placeholder")
OTHER = "someone-else@example.invalid"


def _job_id(key: str) -> str:
    return {k: live_id for kind, k, live_id in acl.run_as_targets(_summary(), bundle=BUNDLE) if kind == "jobs"}[key]


def test_every_jobs_identity_job_and_pipeline_is_read_live_and_none_is_left_out() -> None:
    targets = acl.run_as_targets(_summary(), bundle=BUNDLE)
    assert sorted(f"{kind}.{key}" for kind, key, _ in targets) == sorted(
        [f"jobs.{key}" for key in _on_jobs("jobs")] + [f"pipelines.{key}" for key in _on_jobs("pipelines")]
    )
    assert "pipelines.mip_feature_pipeline" in {f"{k}.{key}" for k, key, _ in targets}, "positive control"
    assert acl.live_run_as_drift(FakeWorkspace({}), targets, JOBS_SP.upper()) == [], "one principal, any case"


@pytest.mark.parametrize(
    ("where", "label"),
    ((lambda: PIPELINE, "pipelines.mip_feature_pipeline"), (lambda: ("jobs", _job_id("mip_kpi_snapshot")), "jobs.mip_kpi_snapshot")),
    ids=("pipeline", "job"),
)
def test_a_live_run_as_drift_refuses_though_the_summary_says_jobs_principal(where: Any, label: str) -> None:
    """Spike S4: the summary (configuration) still names the jobs SP; only the live GET shows the drift."""

    summary = _summary()
    _targets(summary)  # the configuration check passes
    client = FakeWorkspace(_live_acls())
    client.run_as[where()] = OTHER
    assert acl.live_run_as_drift(client, acl.run_as_targets(summary, bundle=BUNDLE), JOBS_SP) == [
        (acl.LIVE_RUN_AS_DRIFT, label)
    ]


@pytest.mark.parametrize(
    "damage",
    (lambda run_as: run_as.__setitem__(PIPELINE, ""), lambda run_as: run_as.pop(PIPELINE)),
    ids=("blank", "gone"),
)
def test_an_unreadable_live_run_as_refuses_and_is_never_read_as_the_creator(damage: Any) -> None:
    client = FakeWorkspace({})
    damage(client.run_as)
    assert acl.live_run_as_drift(client, acl.run_as_targets(_summary(), bundle=BUNDLE), JOBS_SP) == [
        (acl.LIVE_RUN_AS_UNREADABLE, "pipelines.mip_feature_pipeline")
    ]


def test_the_fake_answers_run_as_as_the_apis_did_in_spike_s4() -> None:
    client = FakeWorkspace(_live_acls())
    pipeline = client.pipelines.get(pipeline_id=PIPELINE[1])
    assert pipeline.run_as_user_name == JOBS_SP and not hasattr(pipeline.spec, "run_as"), "spec never carries run_as"
    assert client.jobs.get(job_id=int(_job_id("mip_kpi_snapshot"))).run_as_user_name == JOBS_SP
    # A set replaces the whole ACL: setting IS_OWNER dropped the App's CAN_MANAGE_RUN.
    from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

    job = ("jobs", _job_id("mip_refresh_scores"))
    owner = AccessControlRequest(service_principal_name=JOBS_SP, permission_level=PermissionLevel("IS_OWNER"))
    client.permissions.set(*job, access_control_list=[owner])
    assert [entry.service_principal_name for entry in client.acls[job]] == [JOBS_SP]


def test_main_refuses_a_drift_before_any_grant_and_names_bundle_keys_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(_summary()), encoding="utf-8")
    monkeypatch.chdir(ROOT)
    client = FakeWorkspace(_live_acls())
    client.run_as[PIPELINE] = OTHER
    for command in ("plan", "apply"):
        assert acl.main([command, "--bundle-summary", str(summary), "--jobs-principal", JOBS_SP], client=client) == 4
    assert client.updates == [] and client.sets == []
    err = capsys.readouterr().err
    assert "REFUSED live_run_as_drift pipelines.mip_feature_pipeline" in err and "nothing was granted" in err
    for value in (OTHER, JOBS_SP, "pl-placeholder"):
        assert value not in err, value
    client.run_as[PIPELINE] = JOBS_SP
    assert acl.main(["apply", "--bundle-summary", str(summary), "--jobs-principal", JOBS_SP], client=client) == 0
    assert client.sets == [] and client.updates, "the grants follow once every live run_as is the jobs SP"


def test_a_live_read_error_fails_the_step_without_a_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(_summary()), encoding="utf-8")
    monkeypatch.chdir(ROOT)
    client = FakeWorkspace(_live_acls())
    client.pipelines = SimpleNamespace(get=lambda **_: (_ for _ in ()).throw(PermissionError("denied")))
    assert acl.main(["apply", "--bundle-summary", str(summary), "--jobs-principal", JOBS_SP], client=client) == 3
    assert client.updates == []


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
