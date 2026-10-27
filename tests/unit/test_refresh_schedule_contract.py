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
from tools import verify_live_source_readiness as readiness
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

    # The one exception, and how it lands (data-freshness design + adversarial corrections).
    flat_deployment = " ".join(deployment.split())
    flat_runbook = " ".join(runbook.split())
    assert (
        "The one exception: the dev target's live install runs the weekly `mip_scheduled_refresh` "
        "(FRED → gold → lifecycle → KPI, Fridays 06:00 America/Chicago). `MIP_SCHEDULED_REFRESH=off` pauses it."
    ) in flat_deployment
    assert "only through the bundle apply of a GO governed `./scripts/deploy.sh -t dev` run" in flat_deployment
    assert "### Weekly scheduled refresh (dev live install)" in runbook
    for phrase in (
        "Run **Refresh market rates** only together with **Rebuild scoring snapshot**.",
        "The schedule reaches a workspace only through the bundle apply inside a full governed "
        "`./scripts/deploy.sh -t dev` run.",
        "Step 0a stops and quarantines the live App before the bundle apply",
        "**Static-share decay, not freshness.**",
        "The new FRED rate is real freshness; this drain is decay.",
        "11 weekly prints (13 August to 22 October 2026)",
        "**Treatment-table DDL race.**",
        "**Silent re-pause.**",
        "on the live install only",
        # Design §7.1 risks and the full GO list (adversarial follow-ups).
        "**Piecewise-render trap.**",
        "**Shared catalog.**",
        "**Postflight failure (step 9c).**",
        "the `SALESFORCE_*` values supplied or the connector accepted as disabled",
        "a venv built outside the checkout and selected with `MIP_DEPLOY_PYTHON`",
        "`MIP_REBASE_UNVERIFIED_APP=1` once",
        "no Genie space that the App service principal reaches through a group grant",
    ):
        assert phrase in flat_runbook, phrase
    for doc in (flat_deployment, flat_runbook):
        assert "no other operator action" not in doc.lower()


# --------------------------------------------- mip_scheduled_refresh (bundle)


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


def test_every_chain_child_queues_so_a_deploy_delays_the_chain_instead_of_skipping_it() -> None:
    """The runbook's collision risk rests on this: no child relies on the CLI's default queueing.

    A child run that meets a deploy's run of the same job waits in the queue
    (max_concurrent_runs 1); only a hold past the orchestrator's timeout fails
    the chain.
    """

    jobs = _bundle()["resources"]["jobs"]
    for _task_key, job in CHAIN:
        assert jobs[job]["max_concurrent_runs"] == 1, job
        assert jobs[job].get("queue") == {"enabled": True}, job
    assert jobs[ORCHESTRATOR]["timeout_seconds"] == 5400
    flat_runbook = " ".join((ROOT / "docs" / "runbook.md").read_text(encoding="utf-8").split())
    assert "so a deploy that holds one at Friday 06:00 delays the chain instead of skipping it" in flat_runbook
    assert "orchestrator's 5400 s (90 min) timeout" in flat_runbook


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


def test_the_cadence_constant_follows_the_weekly_cron() -> None:
    cron = _bundle()["resources"]["jobs"][ORCHESTRATOR]["schedule"]["quartz_cron_expression"]
    seconds, minutes, hours, day_of_month, month, day_of_week, *_year = cron.split()

    assert all(field.isdigit() for field in (seconds, minutes, hours)), "one fixed time of day"
    assert (day_of_month, month) == ("?", "*")
    assert re.fullmatch(r"MON|TUE|WED|THU|FRI|SAT|SUN", day_of_week), "a single weekday is a 7-day cadence"
    assert readiness.SCHEDULED_REFRESH_CADENCE_DAYS == 7
    assert readiness.SCHEDULED_REFRESH_MAX_SOURCE_AGE_DAYS == readiness.SCHEDULED_REFRESH_CADENCE_DAYS + 1 == 8
    # verify_live proves the schedule live by this cron and time zone; the
    # default stays fail-closed (tests/unit/test_verify_live_source_age.py).
    schedule = _bundle()["resources"]["jobs"][ORCHESTRATOR]["schedule"]
    assert (cron, schedule["timezone_id"]) == (readiness.SCHEDULED_REFRESH_CRON, readiness.SCHEDULED_REFRESH_TIMEZONE)
    assert readiness.DEFAULT_MAX_SOURCE_AGE_DAYS == readiness.FAIL_CLOSED_MAX_SOURCE_AGE_DAYS == 3


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


def test_bundle_env_writes_the_overrides_under_databricks_bundle_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI reads variable-overrides.json under DATABRICKS_BUNDLE_ROOT when that names another root."""
    other_root = tmp_path / "other-bundle-root"
    other_root.mkdir()

    code, calls = _bundle_env_main(
        monkeypatch, tmp_path, {"DATABRICKS_BUNDLE_ROOT": str(other_root), posture.PAUSE_ENV: "UNPAUSED"}
    )

    assert code == 0 and len(calls) == 1
    assert _written_recipients(other_root) == ["${workspace.current_user.userName}"]
    assert not posture.overrides_path(tmp_path, "dev").exists(), "never the default root's file"


# ------------------------------------- docs/runbook.md: the interim refresh


def test_the_runbook_pins_the_interim_refresh_and_its_never_run_now_warning() -> None:
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    start = runbook.index("### Interim refresh (governed deploy NO-GO)")
    section = " ".join(runbook[start : runbook.index("\n## ", start)].split())
    flat_runbook = " ".join(runbook.split())

    # The warning that keeps a stale deployed job from dropping the reviewed grants.
    for phrase in (
        "**Never `run-now`, unpause or Admin-trigger the deployed gold job (`mip_refresh_scores`) or the deployed "
        "lifecycle job (`mip_sync_lifecycle_state`) on an install whose job definitions predate the current bundle.**",
        "which drops the App, agent-runtime and agent-proxy `EXECUTE` grants",
        "**Rebuild scoring snapshot** runs the deployed gold job",
        "Never run-now the deployed lifecycle job instead.",
        # The Admin lifecycle button is in-process; the real job fallback is an approval's failed MERGE.
        "**Sync workflow state** runs the warehouse lifecycle MERGE inside the App (the same work as step 6), "
        "not the deployed job.",
        "But an approve, reject or revoke whose warehouse MERGE fails falls back to run-now of the deployed "
        "`mip_sync_lifecycle_state` (`backend/services/job_trigger.py`, `trigger_lifecycle_sync`)",
        "check the App log for `lifecycle_sync_error` followed by `job_trigger_fired`.",
        # What the procedure writes, and the verifier it could not run on 2026-10-27.
        "It changes data and, only when step 1 finds a FRED source file that differs from main's render, that "
        "file under the bundle root; never a job definition, grant, ACL,",
        "Step 4's verifier was substituted on 2026-10-27",
        "That file must equal the scratch render byte for byte; when it differs, stop, because this procedure "
        "uploads only step 1's FRED files",
        "the `init_fn_*` statements step 2 did not skip (eleven at most)",
        "with the plan computed after step 2's skips",
        "the verifier cannot run or fails by design. Record its output, never hand-grant, and treat the strict "
        "before/after diff above plus the App SP's effective `EXECUTE` on the three reviewed functions as the "
        "gate. Only the governed deploy restores the direct grants.",
    ):
        assert phrase in section, phrase
    for retired in (
        "**Sync workflow state** can queue the deployed lifecycle job",
        "It changes data only:",
        "the eleven `init_fn_*`",
    ):
        assert retired not in section, retired
    # The lifecycle claims above stay true of the code they name.
    admin = (ROOT / "backend" / "api" / "admin.py").read_text(encoding="utf-8")
    lifecycle_branch = admin[admin.index('if payload.job_key == "lifecycle_sync":') :]
    lifecycle_branch = lifecycle_branch[: lifecycle_branch.index("else:")]
    assert "sync_lifecycle_state_via_warehouse()" in lifecycle_branch
    assert "run_now" not in lifecycle_branch
    trigger = (ROOT / "backend" / "services" / "job_trigger.py").read_text(encoding="utf-8")
    sync = trigger[trigger.index("def trigger_lifecycle_sync(") : trigger.index("def _trigger_lifecycle_sync_job(")]
    assert sync.index('"lifecycle_sync_error"') < sync.index("_trigger_lifecycle_sync_job(reason=f")
    assert 'os.environ.get("MIP_LIFECYCLE_SYNC_MODE", "warehouse")' in sync
    assert '"job_trigger_fired"' in trigger
    # The recipe's load-bearing steps, in order.
    order = [
        "1. **FRED.** First prove that the deployed `mip_fred_rates_ingest` equals main's",
        "2. **Grant snapshot, before.**",
        "3. **Gold.**",
        "Never include a `publish_fn_*` task",
        "opens with a `TEMP VIEW`",
        "databricks jobs submit --no-wait",
        '"idempotency_token": "<unique per refresh>"',
        "4. **Grant snapshot, after, and a strict diff.**",
        "So does an object whose effective `EXECUTE` cannot be read on either side",
        "5. **Rate alignment.**",
        "6. **Lifecycle.**",
        "7. **KPI.**",
        "Probe each App endpoint twice and read the second answer.",
        "only a real Genie turn or an App restart closes it",
    ]
    positions = [section.index(phrase) for phrase in order]
    assert positions == sorted(positions), "the steps keep their order"
    assert "MIP_ENABLE_DEMO_FIRST_PARTY_FEEDS=1 python tools/render_sql.py" in section
    # The interleaving note next to the same-job collision.
    assert "Do not start a governed deploy (or the interim refresh below) within about an hour of Friday 06:00 CT" in (
        flat_runbook
    )


# The governed deploy's own step 10 (runbook §4, "That single invocation
# executes:"): it runs right after the deploy's bundle apply, so the job it
# runs is never stale.
_STALE_JOB_CAVEAT_EXEMPT = {
    ("runbook.md", "10. `databricks bundle run mip_refresh_scores -t dev` — CTAS chain:"),
}
_RUNS_GOLD_OR_LIFECYCLE_JOB = re.compile(r"bundle run\s+(?:mip_refresh_scores|mip_sync_lifecycle_state)\b")


def test_every_operator_doc_that_runs_the_gold_or_lifecycle_job_carries_the_stale_job_caveat() -> None:
    """A stale deployed gold job drops the reviewed-function grants; a stale lifecycle job reseeds defaults.

    Every operator doc (docs/*.md; docs/audits and docs/validation are dated
    records) that tells an operator to run either job carries, within 12 lines,
    the NO-GO caveat and a pointer to the interim refresh.
    """
    checked: list[str] = []
    for doc in sorted((ROOT / "docs").glob("*.md")):
        lines = doc.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not _RUNS_GOLD_OR_LIFECYCLE_JOB.search(line) or (doc.name, line.strip()) in _STALE_JOB_CAVEAT_EXEMPT:
                continue
            window = " ".join(" ".join(lines[max(0, index - 12) : index + 13]).split())
            site = f"{doc.name}:{index + 1}"
            assert "governed deploy is NO-GO, never run" in window, site
            assert "Interim refresh (governed deploy NO-GO)" in window, site
            checked.append(site)
    # Non-vacuity: the nine sites this caveat was written for (2026-10-27).
    assert len(checked) >= 9, checked
    runbook = " ".join((ROOT / "docs" / "runbook.md").read_text(encoding="utf-8").split())
    assert (
        "Buttons 1 to 3 run the deployed jobs. Press them only where the deployed jobs match the current bundle: on an "
        "install whose governed deploy is NO-GO, never run them"
    ) in runbook
    from backend.services import job_trigger

    assert "only when the deployed job matches the current bundle" in job_trigger._LIFECYCLE_REPAIR_COMMAND
    assert "Interim refresh (governed deploy NO-GO)" in job_trigger._LIFECYCLE_REPAIR_COMMAND
