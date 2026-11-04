"""An enabled Growth Agent monitor scheduler refuses, planned, live or declared (M2-PR5).

Until a non-approver scheduler role exists the job stays PAUSED:

* the validator: bundle_env refuses every bundle call, before the CLI, when
  databricks.yml or a target override declares it enabled;
* the plan: a ``new_state`` that enables it refuses;
* live: a workspace copy that is enabled (by name, a dev-prefixed one
  included, or by the plan's bound id) refuses, and a live read that fails
  refuses rather than passes;
* scripts/deploy.sh runs the check beside the plan gate at both positions,
  and refusal lines never carry a job id or name.
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import bundle_env
from tools.databricks import monitor_scheduler_gate as gate

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_TEXT = (ROOT / "databricks.yml").read_text(encoding="utf-8")
KEY = f"resources.jobs.{gate.JOB_KEY}"
PAUSED = {"quartz_cron_expression": "0 30 8 ? * MON-FRI *", "timezone_id": "America/Chicago", "pause_status": "PAUSED"}
_AMBIENT_PREFIXES = ("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_", "LAKEBASE_", "APP_ENV")


def _plan(value: dict[str, Any] | None, *, action: str = "update", job_id: str = "4242") -> str:
    entries: dict[str, Any] = {}
    if value is not None:
        entry: dict[str, Any] = {"action": action, "new_state": {"value": {"name": gate.JOB_KEY, **value}}}
        if action != "create":
            entry["id"] = job_id
        entries[KEY] = entry
    return json.dumps({"plan_version": 2, "cli_version": "1.10.0", "lineage": "l", "serial": 1, "plan": entries})


class _Jobs:
    def __init__(self, jobs: dict[int, tuple[str, dict[str, Any]]]) -> None:
        self.jobs = jobs
        self.calls: list[str] = []

    def list(self) -> list[Any]:
        self.calls.append("list")
        return [
            types.SimpleNamespace(job_id=job_id, settings=types.SimpleNamespace(name=name))
            for job_id, (name, _settings) in self.jobs.items()
        ]

    def get(self, job_id: int) -> Any:
        self.calls.append(f"get:{job_id}")
        return types.SimpleNamespace(settings=self.jobs[job_id][1])


def _workspace(jobs: dict[int, tuple[str, dict[str, Any]]]) -> Any:
    return types.SimpleNamespace(jobs=_Jobs(jobs))


@pytest.mark.parametrize(
    ("settings", "enabled"),
    [
        ({"schedule": PAUSED}, False),
        ({}, False),
        ({"schedule": {**PAUSED, "pause_status": "UNPAUSED"}}, True),
        ({"schedule": {k: v for k, v in PAUSED.items() if k != "pause_status"}}, True),
        ({"schedule": PAUSED, "trigger": {"periodic": {"interval": 1, "unit": "HOURS"}}}, True),
        ({"schedule": PAUSED, "trigger": {"pause_status": "PAUSED"}}, False),
        ({"continuous": {"pause_status": "UNPAUSED"}}, True),
        ({"schedule": types.SimpleNamespace(pause_status=types.SimpleNamespace(value="PAUSED"))}, False),
        ({"schedule": types.SimpleNamespace(pause_status=types.SimpleNamespace(value="UNPAUSED"))}, True),
    ],
)
def test_enabled_is_the_jobs_apis_meaning(settings: dict[str, Any], enabled: bool) -> None:
    assert bool(gate.enabled_reasons(settings)) is enabled


def test_the_committed_bundle_keeps_it_paused() -> None:
    assert gate.bundle_refusals(BUNDLE_TEXT) == []
    assert gate.SCHEDULER_ROLE_EXISTS is False, "flipped only by the reviewed scheduler-role change"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("        pause_status: PAUSED\n      environments:\n        - environment_key: py_default\n          spec:\n            environment_version: \"5\"\n            dependencies:\n              - databricks-sdk>=0.30.0\n      tasks:\n        - task_key: run_due_monitor_drafts",
         "        pause_status: UNPAUSED\n      environments:\n        - environment_key: py_default\n          spec:\n            environment_version: \"5\"\n            dependencies:\n              - databricks-sdk>=0.30.0\n      tasks:\n        - task_key: run_due_monitor_drafts"),
        ("    presets:\n      tags:\n        mip_client: ${var.client_key}\n    resources:\n",
         "    presets:\n      tags:\n        mip_client: ${var.client_key}\n    resources:\n      jobs:\n        mip_growth_agent_monitor_scheduler:\n          schedule:\n            quartz_cron_expression: \"0 0 * ? * * *\"\n            timezone_id: UTC\n            pause_status: UNPAUSED\n"),
    ],
    ids=["resource", "target-override"],
)
def test_the_validator_refuses_a_bundle_that_enables_it_before_the_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], old: str, new: str
) -> None:
    assert BUNDLE_TEXT.count(old) == 1
    enabled = BUNDLE_TEXT.replace(old, new)
    assert gate.bundle_refusals(enabled)
    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)
    root = tmp_path / "bundle-root"
    root.mkdir()
    (root / "databricks.yml").write_text(enabled, encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_BUNDLE_ROOT", str(root))
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile()), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    calls: list[list[str]] = []
    monkeypatch.setattr(bundle_env.subprocess, "run", lambda command, **_kwargs: calls.append(command))
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])

    assert bundle_env.main() == 2
    assert calls == []
    assert f"refusing jobs.{gate.JOB_KEY}: its schedule is not PAUSED" in capsys.readouterr().err


def test_the_validator_reads_the_root_the_cli_loads(tmp_path: Path) -> None:
    assert gate.validator_refusals(tmp_path) == [], "no configuration: the CLI refuses on its own"
    (tmp_path / "databricks.yml").write_text(BUNDLE_TEXT, encoding="utf-8")
    assert gate.validator_refusals(tmp_path) == []
    (tmp_path / "databricks.yaml").write_text(BUNDLE_TEXT.replace("pause_status: PAUSED\n      environments:\n        - environment_key: py_default\n          spec:\n            environment_version: \"5\"\n            dependencies:\n              - databricks-sdk>=0.30.0\n      tasks:\n        - task_key: run_due_monitor_drafts", "pause_status: UNPAUSED\n      environments:\n        - environment_key: py_default\n          spec:\n            environment_version: \"5\"\n            dependencies:\n              - databricks-sdk>=0.30.0\n      tasks:\n        - task_key: run_due_monitor_drafts"), encoding="utf-8")
    assert gate.validator_refusals(tmp_path) == ["its schedule is not PAUSED"]
    (tmp_path / "databricks.yaml").write_bytes(b"\xff\xfe")
    assert gate.validator_refusals(tmp_path) == ["the bundle configuration is unreadable (UnicodeDecodeError)"]


def test_a_bundle_without_the_job_refuses_too() -> None:
    root = BUNDLE_TEXT.replace(f"    {gate.JOB_KEY}:\n      name: {gate.JOB_KEY}\n", "    renamed_monitor:\n      name: renamed\n")
    assert "no longer declares it" in " ".join(gate.bundle_refusals(root))


def test_a_paused_plan_and_paused_live_copies_pass() -> None:
    workspace = _workspace({4242: (gate.JOB_KEY, {"schedule": PAUSED}), 7: ("unrelated", {"schedule": {"pause_status": "UNPAUSED"}})})
    assert gate.check(_plan({"schedule": PAUSED}), workspace_factory=lambda: workspace) == []
    assert workspace.jobs.calls == ["list", "get:4242"], "only this job is read, by name and bound id"


def test_a_plan_that_enables_it_refuses() -> None:
    workspace = _workspace({})
    reasons = gate.check(_plan({"schedule": {**PAUSED, "pause_status": "UNPAUSED"}}, action="create"), workspace_factory=lambda: workspace)
    assert reasons == ["the plan enables it: its schedule is not PAUSED"]


@pytest.mark.parametrize(
    ("jobs", "bound"),
    [
        ({11: (f"[dev someone] {gate.JOB_KEY}", {"schedule": {**PAUSED, "pause_status": "UNPAUSED"}})}, "4242"),
        ({4242: ("renamed by hand", {"schedule": {**PAUSED, "pause_status": "UNPAUSED"}})}, "4242"),
        ({12: (gate.JOB_KEY, {"trigger": {"periodic": {"interval": 1, "unit": "HOURS"}}})}, "4242"),
    ],
    ids=["dev-prefixed-name", "bound-id", "trigger"],
)
def test_an_unpaused_live_monitor_job_refuses(jobs: dict[int, tuple[str, dict[str, Any]]], bound: str) -> None:
    if 4242 not in jobs:
        jobs = {**jobs, 4242: (gate.JOB_KEY, {"schedule": PAUSED})}
    reasons = gate.check(_plan({"schedule": PAUSED}, job_id=bound), workspace_factory=lambda: _workspace(jobs))
    assert reasons and all(reason.startswith("a live copy: ") for reason in reasons)


def test_an_unreadable_live_state_or_plan_refuses() -> None:
    def broken() -> Any:
        raise PermissionError("no")

    assert gate.check(_plan({"schedule": PAUSED}), workspace_factory=broken) == [
        "its live state could not be read (PermissionError)"
    ]
    assert gate.check("{", workspace_factory=broken)[0].startswith("the bundle plan could not be read")


def test_the_cli_exits_2_with_value_free_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(_plan({"schedule": PAUSED}, job_id="98765"), encoding="utf-8")
    jobs = {98765: (f"[dev someone] {gate.JOB_KEY}", {"schedule": {**PAUSED, "pause_status": "UNPAUSED"}})}
    assert gate.main(["check", "--plan", str(plan)], workspace_factory=lambda: _workspace(jobs)) == 2
    err = capsys.readouterr().err
    assert f"REFUSED jobs.{gate.JOB_KEY}: a live copy: its schedule is not PAUSED" in err
    assert "98765" not in err and "someone" not in err
    paused = {98765: (gate.JOB_KEY, {"schedule": PAUSED})}
    assert gate.main(["check", "--plan", str(plan)], workspace_factory=lambda: _workspace(paused)) == 0


def test_the_deploy_runs_it_beside_the_plan_gate_at_both_positions() -> None:
    text = deploy_entrypoint_text()
    gate_call = 'run "$PYTHON" -m tools.databricks.monitor_scheduler_gate check --plan "$plan_json" || monitor_rc=$?'
    assert text.count(gate_call) == 1, "inside run_bundle_plan_gate, which both positions call"
    body = text[text.index("run_bundle_plan_gate() {") :]
    cleanup = '|| rm -f "$plan_json"\n  if [[ "$gate_rc" -ne 0 ]]'
    assert body.index("bundle_plan_gate check") < body.index(gate_call) < body.index(cleanup)
    assert 'if [[ "$monitor_rc" -ne 0 ]]; then' in body
    assert text.count("run_bundle_plan_gate pre-lease") == 1 and text.count("run_bundle_plan_gate pre-apply") == 1
