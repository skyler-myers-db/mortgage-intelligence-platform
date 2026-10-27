"""Cost-control contracts for MIP refresh schedules.

Every child job ships PAUSED. The one scheduled job is the weekly
``mip_scheduled_refresh`` orchestrator (data-freshness design 2026-10-26): it
chains FRED -> gold -> lifecycle -> KPI by ``run_job_task``, its pause status is
a variable only scripts/deploy.sh sets (UNPAUSED for the dev live install
only), and it mails failures only on that install.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import bundle_env
from tools.databricks import scheduled_refresh_posture as posture

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "lib" / "deploy_step_preflight_gates.sh"
ORCHESTRATOR = "mip_scheduled_refresh"
CHAIN = (
    ("refresh_market_rates", "mip_fred_rates_ingest"),
    ("refresh_gold_scores", "mip_refresh_scores"),
    ("sync_lifecycle_state", "mip_sync_lifecycle_state"),
    ("snapshot_headline_kpis", "mip_kpi_snapshot"),
)


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_mip_refresh_schedules_ship_paused_in_active_bundle() -> None:
    bundle = _load_yaml(ROOT / "databricks.yml")
    jobs = bundle["resources"]["jobs"]

    for job_key in (
        "mip_sync_lifecycle_state",
        "mip_fred_rates_ingest",
        "mip_kpi_snapshot",
        "mip_growth_agent_monitor_scheduler",
    ):
        schedule = jobs[job_key]["schedule"]
        assert schedule["pause_status"] == "PAUSED"
    scheduled = {key for key, job in jobs.items() if "schedule" in job or "trigger" in job or "continuous" in job}
    assert scheduled == {
        "mip_sync_lifecycle_state",
        "mip_fred_rates_ingest",
        "mip_kpi_snapshot",
        "mip_growth_agent_monitor_scheduler",
        ORCHESTRATOR,
    }, "a new schedule needs its own reviewed pause posture"


def test_no_bundle_mirror_files_reappear() -> None:
    """2026-06-11 audit P2-9: resources/*.yml and jobs/*.yml mirrors were
    dead (databricks.yml has no `include:`) and had already drifted from
    the inline definitions. They were deleted; this guard keeps the
    single-source-of-truth posture from regressing — a new mirror would
    silently diverge again. Job task SOURCE files (jobs/*.py) are fine."""
    assert not (ROOT / "resources").exists(), (
        "resources/ bundle mirrors came back — databricks.yml is the only "
        "place bundle resources may be declared (audit P2-9)"
    )
    yml_mirrors = sorted((ROOT / "jobs").glob("*.yml")) + sorted((ROOT / "jobs").glob("*.yaml"))
    assert yml_mirrors == [], f"jobs/ YAML mirrors reappeared: {yml_mirrors}"


def test_operator_docs_explain_app_refresh_default_and_paused_schedules() -> None:
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    onboarding = (ROOT / "docs" / "se-onboarding.md").read_text(encoding="utf-8")
    dashboards = (ROOT / "docs" / "dashboards.md").read_text(encoding="utf-8")

    assert "Data operations" in runbook
    assert "schedules deploy **paused by" in runbook
    assert "default** in dev and prod" in runbook
    assert "All MIP refresh and automation schedules deploy paused by default" in deployment
    assert "FRED, lifecycle fallback, and Growth Agent monitor schedules deploy" in onboarding
    assert "scheduler drafts" in onboarding
    assert "ships `PAUSED` in every bundle target" in dashboards
    assert "auto-UNPAUSES" not in dashboards



def _bundle() -> dict:
    return _load_yaml(ROOT / "databricks.yml")


def _run_job_target(task: dict) -> str | None:
    job_id = (task.get("run_job_task") or {}).get("job_id")
    if job_id is None:
        return None
    match = re.fullmatch(r"\$\{resources\.jobs\.([a-z0-9_]+)\.id\}", job_id)
    assert match, job_id
    return match.group(1)


def run_job_closure(jobs: dict, root: str) -> set[str]:
    """Every bundle job reachable from ``root`` through run_job_task, root included."""

    seen: set[str] = set()
    pending = [root]
    while pending:
        job = pending.pop()
        if job in seen:
            continue
        seen.add(job)
        pending.extend(
            target for task in jobs[job].get("tasks", []) if (target := _run_job_target(task)) is not None
        )
    return seen


def test_the_orchestrator_chains_fred_gold_lifecycle_kpi_in_deploy_order() -> None:
    tasks = _bundle()["resources"]["jobs"][ORCHESTRATOR]["tasks"]
    by_key = {task["task_key"]: task for task in tasks}

    assert tuple((task["task_key"], _run_job_target(task)) for task in tasks if "run_job_task" in task) == CHAIN
    previous: str | None = None
    for task_key, _job in CHAIN:
        assert [dep["task_key"] for dep in by_key[task_key].get("depends_on", [])] == (
            [] if previous is None else [previous]
        ), task_key
        previous = task_key
    assert set(by_key) == {key for key, _job in CHAIN} | {"assert_refresh_outcome"}
    assert [dep["task_key"] for dep in by_key["assert_refresh_outcome"]["depends_on"]] == ["refresh_gold_scores"]
    for task in tasks:
        kinds = {key for key in task if key.endswith("_task")}
        assert kinds in ({"run_job_task"}, {"sql_task"}), (task["task_key"], kinds)
        assert "run_if" not in task, "ALL_SUCCESS throughout: a failed step fails the chain"


def test_the_orchestrator_never_reaches_silver_or_the_reviewed_function_publisher() -> None:
    jobs = _bundle()["resources"]["jobs"]
    closure = run_job_closure(jobs, ORCHESTRATOR)

    assert closure == {ORCHESTRATOR, *(job for _key, job in CHAIN)}
    assert "mip_refresh_silver" not in closure, "the Cotality eval share is static; silver stays on demand"
    assert "mip_init_catalog_schemas" not in closure, "the reviewed Growth Agent function publisher"
    reviewed = re.compile(r"uc_functions/fn_(build_cohort|segment_counts|lead_queue_url)\.sql\Z")
    for job in closure:
        for task in jobs[job].get("tasks", []):
            path = (task.get("sql_task") or {}).get("file", {}).get("path", "")
            assert reviewed.search(path) is None, (job, task["task_key"])


def test_the_orchestrator_schedule_is_weekly_and_paused_by_a_variable_no_target_sets() -> None:
    bundle = _bundle()
    job = bundle["resources"]["jobs"][ORCHESTRATOR]

    assert job["schedule"] == {
        "quartz_cron_expression": "0 0 6 ? * FRI *",
        "timezone_id": "America/Chicago",
        "pause_status": "${var.scheduled_refresh_pause_status}",
    }
    assert job["max_concurrent_runs"] == 1 and job["queue"] == {"enabled": True}
    assert job["email_notifications"] == {"on_failure": "${var.scheduled_refresh_failure_recipients}"}
    assert bundle["variables"][posture.PAUSE_VARIABLE]["default"] == "PAUSED"
    recipients = bundle["variables"][posture.RECIPIENTS_VARIABLE]
    assert recipients["type"] == "complex" and recipients["default"] == []
    for name, target in bundle["targets"].items():
        assert not {posture.PAUSE_VARIABLE, posture.RECIPIENTS_VARIABLE} & set(target.get("variables") or {}), name
        assert ORCHESTRATOR not in ((target.get("resources") or {}).get("jobs") or {}), name
        assert "trigger_pause_status" not in (target.get("presets") or {}), name
    text = (ROOT / "databricks.yml").read_text(encoding="utf-8")
    assert text.count(f"{posture.PAUSE_VARIABLE}:") == 1, "declared once, set by no target"
    assert text.count(f"{posture.RECIPIENTS_VARIABLE}:") == 1, "declared once, set by no target"


# ------------------------------------------ the deploy.sh block (pause status)


def _block() -> str:
    text = PREFLIGHT.read_text(encoding="utf-8")
    return text[text.index("# >>> scheduled-refresh") : text.index("# <<< scheduled-refresh")]


def _code_lines() -> list[str]:
    return [line for line in _block().splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_the_block_sits_between_the_install_suffix_export_and_the_first_validate() -> None:
    text = deploy_entrypoint_text()
    block = text.index("# >>> scheduled-refresh")

    assert text.index("export BUNDLE_VAR_bundle_root_leaf BUNDLE_VAR_install_suffix") < block
    assert text.index("# <<< ci-install-isolation") < block
    assert block < text.index("tools.databricks.bundle_env validate")
    assert block < text.index('read -r -p "About to DEPLOY')
    assert block < text.index("\nrun "), "before the first top-level run-wrapped mutation"
    assert not [line for line in _code_lines() if "DRY_RUN" in line], "pure: it runs in DRY_RUN too"


def test_unpaused_is_reachable_only_for_the_dev_live_install() -> None:
    code = _code_lines()
    assignments = [line.strip() for line in code if "BUNDLE_VAR_scheduled_refresh_pause_status=" in line]

    assert assignments == [
        "BUNDLE_VAR_scheduled_refresh_pause_status=UNPAUSED",
        "BUNDLE_VAR_scheduled_refresh_pause_status=PAUSED",
    ]
    unpause = code.index("  BUNDLE_VAR_scheduled_refresh_pause_status=UNPAUSED")
    assert code[unpause - 1] == (
        'if [[ "$TARGET" == "dev" && "$_SCHEDULED_REFRESH" == "on" && -z "$BUNDLE_VAR_install_suffix" ]]; then'
    )
    assert code[unpause + 1] == "else"
    assert '  _SCHEDULED_REFRESH="$(deployment_control_value MIP_SCHEDULED_REFRESH on)"' in code, "dev default on"
    assert '  _SCHEDULED_REFRESH="$(deployment_control_value MIP_SCHEDULED_REFRESH off)"' in code, "prod default off"
    assert "export BUNDLE_VAR_scheduled_refresh_pause_status" in code


def _bash_block(
    target: str, install_suffix: str, env: dict[str, str] | None = None, dotenv: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash
    # The real helper reads the shell, then .env.local, then the default; ``dotenv`` stands in for the file.
    arms = "".join(f"{name}) value='{value}' ;; " for name, value in (dotenv or {}).items())
    prelude = (
        "set -euo pipefail\nRED=''\nRST=''\n"
        f"TARGET='{target}'\nBUNDLE_VAR_install_suffix='{install_suffix}'\n"
        'deployment_control_value() { local name="$1" default_value="${2:-}" value; value="${!name:-}"; '
        f'if [[ -z "$value" ]]; then case "$name" in {arms}*) ;; esac; fi; '
        "printf '%s' \"${value:-$default_value}\"; }\n"
    )
    epilogue = '\nprintf "RESULT=%s\\n" "$(printenv BUNDLE_VAR_scheduled_refresh_pause_status)"\n'
    return subprocess.run(
        [bash, "-c", prelude + _block() + epilogue],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _result(completed: subprocess.CompletedProcess[str]) -> str:
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip().splitlines()[-1]


def test_the_block_unpauses_only_the_dev_live_install() -> None:
    assert _result(_bash_block("dev", "")) == "RESULT=UNPAUSED", "dev live install: on by default"
    assert _result(_bash_block("dev", "_ci")) == "RESULT=PAUSED", "the CI staging install is always paused"
    assert _result(_bash_block("dev", "_ci", env={"MIP_SCHEDULED_REFRESH": "on"})) == "RESULT=PAUSED"
    assert _result(_bash_block("dev", "", env={"MIP_SCHEDULED_REFRESH": "off"})) == "RESULT=PAUSED"
    assert _result(_bash_block("dev", "", dotenv={"MIP_SCHEDULED_REFRESH": "off"})) == "RESULT=PAUSED"
    assert _result(_bash_block("prod", "")) == "RESULT=PAUSED"
    assert _result(_bash_block("prod", "", env={"MIP_SCHEDULED_REFRESH": "off"})) == "RESULT=PAUSED"
    stray = {"BUNDLE_VAR_scheduled_refresh_pause_status": "UNPAUSED"}
    assert _result(_bash_block("dev", "_ci", env=stray)) == "RESULT=PAUSED", "the block owns the export"


@pytest.mark.parametrize(
    ("target", "env"),
    [
        ("dev", {"MIP_SCHEDULED_REFRESH": "garbage"}),
        ("dev", {"MIP_SCHEDULED_REFRESH": "ON"}),
        ("dev", {"MIP_SCHEDULED_REFRESH": "true"}),
        ("prod", {"MIP_SCHEDULED_REFRESH": "on"}),
    ],
)
def test_the_block_refuses_a_bad_value_and_a_prod_unpause(target: str, env: dict[str, str]) -> None:
    refused = _bash_block(target, "", env=env)

    assert refused.returncode == 2, refused.stdout
    assert "MIP_SCHEDULED_REFRESH" in refused.stderr
    assert "RESULT=" not in refused.stdout


# ---------------------------- bundle_env: pause export and failure recipients


@pytest.mark.parametrize(
    ("target", "suffix", "value", "allowed"),
    [
        ("dev", "", "UNPAUSED", True),
        ("dev", "", "PAUSED", True),
        ("dev", "_ci", "PAUSED", True),
        ("prod", "", "PAUSED", True),
        ("dev", "_ci", "UNPAUSED", False),
        ("prod", "", "UNPAUSED", False),
        ("ci", "", "UNPAUSED", False),
        ("dev", "", "", False),
        ("dev", "", "unpaused", False),
    ],
)
def test_the_pause_export_is_checked_against_the_install(target: str, suffix: str, value: str, allowed: bool) -> None:
    env = {posture.PAUSE_ENV: value}
    if allowed:
        posture.check_pause_status(env, target=target, install_suffix=suffix)
    else:
        with pytest.raises(ValueError, match=posture.PAUSE_ENV):
            posture.check_pause_status(env, target=target, install_suffix=suffix)
    posture.check_pause_status({}, target=target, install_suffix=suffix)  # absent: the bundle default


def test_only_the_dev_live_install_mails_its_deployer() -> None:
    assert posture.failure_recipients(target="dev", install_suffix="") == ["${workspace.current_user.userName}"]
    for target, suffix in (("dev", "_ci"), ("prod", ""), ("ci", "")):
        assert posture.failure_recipients(target=target, install_suffix=suffix) == [], (target, suffix)


def test_the_overrides_writer_keeps_other_keys_and_refuses_a_bad_file(tmp_path: Path) -> None:
    path = posture.overrides_path(tmp_path, "dev")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"operator_key": ["kept"]}), encoding="utf-8")

    posture.write_failure_recipients(tmp_path, target="dev", recipients=["${workspace.current_user.userName}"])
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "operator_key": ["kept"],
        posture.RECIPIENTS_VARIABLE: ["${workspace.current_user.userName}"],
    }
    posture.write_failure_recipients(tmp_path, target="dev", recipients=[])
    assert json.loads(path.read_text(encoding="utf-8"))[posture.RECIPIENTS_VARIABLE] == []

    for bad in ("{not json", "[]"):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError, match="fix or remove it"):
            posture.write_failure_recipients(tmp_path, target="dev", recipients=[])
        assert path.read_text(encoding="utf-8") == bad, "never overwritten"
    with pytest.raises(ValueError, match="target name"):
        posture.overrides_path(tmp_path, "../dev")


def _bundle_env_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], target: str = "dev"
) -> tuple[int, list[list[str]]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", tmp_path / ".env.local.absent")
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path)
    for name in ("DATABRICKS_BUNDLE_ROOT", "MIP_BUNDLE_ROOT_LEAF", posture.PAUSE_ENV, "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))

    def fake_run(command: list[str], *, env: dict[str, str], check: bool) -> object:
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_run)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", target])
    return bundle_env.main(), calls


def _written_recipients(tmp_path: Path, target: str = "dev") -> object:
    overrides = json.loads(posture.overrides_path(tmp_path, target).read_text(encoding="utf-8"))
    return overrides[posture.RECIPIENTS_VARIABLE]


def test_bundle_env_mails_the_live_install_and_never_the_ci_leaf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    code, calls = _bundle_env_main(monkeypatch, tmp_path, {posture.PAUSE_ENV: "UNPAUSED"})
    assert code == 0 and len(calls) == 1
    assert _written_recipients(tmp_path) == ["${workspace.current_user.userName}"]

    code, calls = _bundle_env_main(monkeypatch, tmp_path, {"MIP_BUNDLE_ROOT_LEAF": "dev-ci"})
    assert code == 0 and len(calls) == 1
    assert _written_recipients(tmp_path) == [], "the dev-ci leaf rewrites the same file to no recipients"

    code, calls = _bundle_env_main(monkeypatch, tmp_path, {}, target="prod")
    assert code == 0 and _written_recipients(tmp_path, "prod") == []


@pytest.mark.parametrize(
    ("env", "target"),
    [
        ({"MIP_BUNDLE_ROOT_LEAF": "dev-ci", posture.PAUSE_ENV: "UNPAUSED"}, "dev"),
        ({posture.PAUSE_ENV: "UNPAUSED"}, "prod"),
        ({posture.PAUSE_ENV: "sometimes"}, "dev"),
    ],
)
def test_bundle_env_refuses_an_unpause_outside_the_live_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], target: str
) -> None:
    code, calls = _bundle_env_main(monkeypatch, tmp_path, env, target=target)

    assert code == 2 and calls == [], "refused before the CLI runs"
