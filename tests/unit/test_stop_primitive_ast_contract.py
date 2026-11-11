"""Every SDK App stop, start, deploy and update in tools/ is reviewed and reached only by the window (O16).

``tools/databricks/stop_primitive_map.py`` is the reviewed map. Its AST scan
must find exactly ``PRIMITIVE_SITES``; the static call graph from each site
back to a CLI ``main`` must find exactly the reviewed ``ENTRY_POINTS``; each
mutating mode must be one the module's parser accepts; and a shell file
outside the two stop libraries that invokes such a mode, or carries an inline
``deploy_and_wait``, fails. The mutation tests add each kind of stray call to
a copy and require the contract to notice.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from tools.databricks import stop_primitive_map as stops

REPO = Path(__file__).resolve().parents[2]
STEP_FILE = REPO / "scripts" / "lib" / "deploy_step_bundle_apply.sh"


def test_the_ast_finds_exactly_the_reviewed_primitive_sites() -> None:
    assert stops.python_primitive_sites() == set(stops.PRIMITIVE_SITES)


DEADMAN_SITE = ("jobs/window_deadman.py", "start_blue", "start")


def test_the_dead_mans_restart_is_a_reviewed_site_no_shell_file_reaches() -> None:
    # M0-PR9: the job sources are scanned too. The dead-man's apps start is
    # reached only by its own main, the one-time run the window submits.
    assert DEADMAN_SITE in stops.PRIMITIVE_SITES
    assert stops.reaching_entry_points()[DEADMAN_SITE] == {"jobs.window_deadman"}
    entry = stops.ENTRY_POINTS["jobs.window_deadman"]
    assert entry.callers == frozenset() and entry.modes == {"(default)": True}
    jobs_sites = {site for site in stops.python_primitive_sites() if site[0].startswith("jobs/")}
    assert jobs_sites == {DEADMAN_SITE}, "no other job source stops, starts, deploys or updates an App"


def test_the_ast_finds_exactly_the_reviewed_job_run_sites() -> None:
    assert stops.python_job_run_sites() == set(stops.JOB_RUN_SITES)


RUNNER_SITE = ("tools/databricks/job_phase_runner.py", "start_run", "run_now")


def test_the_dead_mans_submit_and_cancel_are_reached_only_through_the_window_tool() -> None:
    # M0-PR9: the submit and the cancel sit in quiesce_window_deadman, reached
    # only by quiesce_window's open and cancel-deadman, which only the window
    # library invokes.
    reached = stops.reaching_entry_points(sites=set(stops.JOB_RUN_SITES))
    assert set(reached) == set(stops.JOB_RUN_SITES)
    deadman = {site: entries for site, entries in reached.items() if site != RUNNER_SITE}
    assert deadman and all(entries == {"tools.databricks.quiesce_window"} for entries in deadman.values()), reached
    entry = stops.ENTRY_POINTS["tools.databricks.quiesce_window"]
    assert entry.modes["open"] and entry.modes["cancel-deadman"]
    assert entry.callers == frozenset({stops.WINDOW_LIBRARY})


def test_the_data_phases_run_now_is_reached_only_through_the_runners_run_mode() -> None:
    # M1-PR6: the one run-now sits in job_phase_runner.start_run, reached only
    # by the runner's own main, whose mutating modes only the data-phase
    # library invokes; plan is read-only. It never reaches an App primitive.
    assert RUNNER_SITE in stops.JOB_RUN_SITES
    assert stops.reaching_entry_points(sites={RUNNER_SITE})[RUNNER_SITE] == {"tools.databricks.job_phase_runner"}
    entry = stops.ENTRY_POINTS["tools.databricks.job_phase_runner"]
    assert entry.callers == frozenset({stops.DATA_PHASE_LIBRARY})
    assert entry.modes == {"plan": False, "quiet": False, "begin": True, "run": True, "close": True}
    assert not any("job_phase" in site[0] for site in stops.python_primitive_sites())
    run_now = {site for site in stops.python_job_run_sites() if site[2].startswith("run_now")}
    assert run_now == {RUNNER_SITE}, "no other tool or job source starts a job run"


def test_every_site_is_reached_only_by_reviewed_entry_points() -> None:
    reached = stops.reaching_entry_points()
    assert set(reached) == set(stops.PRIMITIVE_SITES)
    for site, entries in reached.items():
        assert entries, f"{site} is reached by no CLI entry point"
        assert entries <= set(stops.ENTRY_POINTS), f"{site}: unreviewed {entries - set(stops.ENTRY_POINTS)}"
    job_runs = stops.reaching_entry_points(sites=set(stops.JOB_RUN_SITES))
    assert set().union(*reached.values(), *job_runs.values()) == set(stops.ENTRY_POINTS)


def test_every_reviewed_mode_is_one_the_entry_point_parses() -> None:
    for module, entry in stops.ENTRY_POINTS.items():
        source = (REPO / Path(*module.split(".")).with_suffix(".py")).read_text(encoding="utf-8")
        if module.endswith("app_deployment_rollback"):
            source += (REPO / "tools/databricks/app_deployment_rollback_cli.py").read_text(encoding="utf-8")
        for mode in entry.modes:
            if mode != "(default)":
                assert f'"{mode}"' in source, f"{module} does not parse {mode}"
        if entry.positional:
            assert any(entry.modes.values()) and not all(entry.modes.values())


# Reviewed exceptions, each one module and one caller: the data phase's runner
# (M1-PR6) and the release's online description fix-up (spike S2: a
# create-update masked to description, which stops, starts and deploys nothing).
NON_STOP_CALLERS = {
    "tools.databricks.job_phase_runner": stops.JOB_RUN_LIBRARIES,
    "tools.databricks.app_description_fixup": frozenset({stops.BUNDLE_APPLY_STEP}),
}


def test_the_stop_libraries_are_the_only_callers_of_a_mutating_mode() -> None:
    for module, entry in stops.ENTRY_POINTS.items():
        allowed = NON_STOP_CALLERS.get(module, stops.STOP_LIBRARIES)
        assert entry.callers <= allowed, module
    fixup = stops.ENTRY_POINTS["tools.databricks.app_description_fixup"]
    assert fixup.callers == frozenset({stops.BUNDLE_APPLY_STEP}) and not fixup.positional
    assert ("tools/databricks/app_description_fixup.py", "apply_bundle_description", "create_update") in (
        stops.PRIMITIVE_SITES
    ), "its one SDK call is the description-masked create-update"
    assert stops.repo_shell_violations() == []


@pytest.mark.parametrize(
    "stray",
    (
        '  "$PYTHON" -m tools.databricks.app_deployment_rollback restore --app-name "$APP_NAME"\n',
        '  "$PYTHON" -m tools.databricks.app_deployment_rollback \\\n    restore \\\n    --app-name "$A"\n',
        '"$PYTHON" - <<\'PYEOF\'\nworkspace.apps.deploy_and_wait(app_name, deployment)\nPYEOF\n',
        '"$PYTHON" - <<\'PYEOF\'\nclient.apps.start(app_name)\nPYEOF\n',
        '  "$PYTHON" -m tools.databricks.quiesce_window await --app-name "$A"\n',
    ),
)
def test_a_step_file_that_calls_a_primitive_fails_the_contract(tmp_path: Path, stray: str) -> None:
    copy = tmp_path / "scripts" / "lib" / STEP_FILE.name
    copy.parent.mkdir(parents=True)
    (tmp_path / "scripts" / "deploy.sh").write_text("", encoding="utf-8")
    copy.write_text(STEP_FILE.read_text(encoding="utf-8") + "\n" + stray, encoding="utf-8")
    violations = stops.repo_shell_violations(root=tmp_path)
    assert violations and all(STEP_FILE.name in finding for finding in violations)


def _tools_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name in stops.SCAN_ROOTS:
        shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    return root


@pytest.mark.parametrize(
    "stray",
    (
        "    window.workspace.apps.stop(window.args.app)\n",
        "    window.workspace.apps.deploy_and_wait(window.args.app, None)\n",
        "    getattr(window.workspace, 'apps').update(window.args.app, None)\n",
    ),
)
def test_a_stop_deploy_or_update_in_the_dead_man_is_an_unmapped_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stray: str
) -> None:
    root = _tools_copy(tmp_path)
    deadman = root / "jobs" / "window_deadman.py"
    text = deadman.read_text(encoding="utf-8")
    anchor = "def blue_refusal(window: Window) -> str:\n"
    assert anchor in text
    deadman.write_text(text.replace(anchor, anchor + stray, 1), encoding="utf-8")
    monkeypatch.setattr(stops, "REPO", root)
    found = stops.python_primitive_sites()
    assert {site for site in found - set(stops.PRIMITIVE_SITES) if site[0] == "jobs/window_deadman.py"}


def test_a_stray_job_submit_or_cancel_in_a_tool_is_an_unmapped_job_run_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_run.py").write_text(
        "def fire(workspace):\n    jobs = workspace.jobs\n    jobs.submit(run_name='x')\n\n\n"
        "def halt(workspace, run_id):\n    getattr(workspace, 'jobs').cancel_run(run_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    assert stops.python_job_run_sites() - set(stops.JOB_RUN_SITES) == {
        ("tools/databricks/stray_run.py", "fire", "submit"),
        ("tools/databricks/stray_run.py", "halt", "cancel_run"),
    }


@pytest.mark.parametrize(
    ("source", "site"),
    (
        ("def fire(w):\n    w.jobs.run_now(job_id=1)\n", ("fire", "run_now")),
        ("def fire(w):\n    j = w.jobs\n    j.run_now_and_wait(job_id=1)\n", ("fire", "run_now_and_wait")),
        ("def fire(w):\n    w.jobs.repair_run(run_id=1, rerun_all_failed_tasks=True)\n", ("fire", "repair_run")),
        ("def fire(w):\n    w.api_client.do('POST', '/api/2.2/jobs/run-now', body={})\n", ("fire", "rest:run-now")),
    ),
    ids=("run_now", "alias_and_wait", "repair", "rest"),
)
def test_a_stray_run_now_or_repair_in_a_tool_is_an_unmapped_job_run_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, site: tuple[str, str]
) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_run_now.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(stops, "REPO", root)
    assert stops.python_job_run_sites() - set(stops.JOB_RUN_SITES) == {("tools/databricks/stray_run_now.py", *site)}


def test_no_tool_or_job_source_starts_or_stops_a_pipeline_update() -> None:
    # M1-PR6: silver refreshes only as the runner's run-now of mip_refresh_silver.
    assert not stops.PIPELINE_RUN_SITES
    assert stops.python_pipeline_run_sites() == set(stops.PIPELINE_RUN_SITES)


@pytest.mark.parametrize(
    ("source", "site"),
    (
        ("def fire(w):\n    w.pipelines.start_update(pipeline_id='p', full_refresh=True)\n", ("fire", "start_update")),
        ("def halt(w):\n    p = w.pipelines\n    p.stop_and_wait(pipeline_id='p')\n", ("halt", "stop_and_wait")),
        ("def fire(w):\n    w.api_client.do('POST', '/api/2.0/pipelines/p-1/updates', body={})\n", ("fire", "rest:updates")),
    ),
    ids=("start_update", "alias_stop", "rest"),
)
def test_a_stray_pipeline_update_in_a_tool_is_an_unmapped_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, site: tuple[str, str]
) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_pipeline.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(stops, "REPO", root)
    assert stops.python_pipeline_run_sites() == {("tools/databricks/stray_pipeline.py", *site)}


def test_a_new_sdk_primitive_in_a_tool_is_an_unmapped_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_restart.py").write_text(
        "def restart(workspace, name):\n    workspace.apps.stop_and_wait(name)\n"
        "    workspace.apps.start(name)\n\n\ndef main():\n    restart(None, 'x')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    found = stops.python_primitive_sites(root / "tools")
    assert found - set(stops.PRIMITIVE_SITES) == {
        ("tools/databricks/stray_restart.py", "restart", "stop_and_wait"),
        ("tools/databricks/stray_restart.py", "restart", "start"),
    }


def test_an_aliased_apps_client_is_still_a_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_alias.py").write_text(
        "def bounce(workspace, name):\n    apps = workspace.apps\n    apps.get(name)\n"
        "    apps.stop(name)\n\n\ndef revive(workspace, name):\n"
        "    getattr(workspace, 'apps').start(name)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    found = stops.python_primitive_sites(root / "tools")
    assert found - set(stops.PRIMITIVE_SITES) == {
        ("tools/databricks/stray_alias.py", "bounce", "stop"),
        ("tools/databricks/stray_alias.py", "revive", "start"),
    }, "a read through the alias is not a site; a stop or start is"


@pytest.mark.parametrize(
    ("source", "apps_sites", "job_sites"),
    (
        # The review's mutation: a tuple alias of both clients.
        (
            "def cancel(workspace, name):\n    apps, jobs = workspace.apps, workspace.jobs\n"
            "    apps.stop(name)\n    jobs.submit(run_name='x')\n",
            {("cancel", "stop")},
            {("cancel", "submit")},
        ),
        ("def f(w, n):\n    apps, *rest = w.apps, w.jobs\n    apps.stop(n)\n", {("f", "stop")}, set()),
        ("def f(w, n):\n    for a in (w.apps,):\n        a.start(n)\n", {("f", "start")}, set()),
        ("def f(w, n):\n    with ctx(w.apps) as a:\n        a.deploy(n, None)\n", {("f", "deploy")}, set()),
        ("def f(n, a=W.apps):\n    a.update(n, None)\n", {("f", "update")}, set()),
        ("def f(w, n):\n    a = w.apps\n    b = a\n    b.stop(n)\n", {("f", "stop")}, set()),
        ("def f(w, n):\n    halt = w.apps.stop\n    halt(n)\n", {("f", "stop")}, set()),
        ("def f(w, n, verb):\n    getattr(w.apps, verb)(n)\n", {("f", "<dynamic>")}, set()),
        ("def f(apps, n):\n    apps.stop(n)\n", {("f", "stop")}, set()),
        ("def f(w, n):\n    (j := w.jobs).cancel_run(n)\n", set(), {("f", "cancel_run")}),
        (
            "def f(w, n):\n    w.api_client.do('POST', f'/api/2.0/apps/{n}/stop')\n"
            "    w.api_client.do('POST', '/api/2.2/jobs/runs/submit', body={})\n",
            {("f", "rest:stop")},
            {("f", "rest:submit")},
        ),
    ),
    ids=("tuple", "starred", "for", "with", "default", "chain", "reference", "dynamic", "param", "walrus", "rest"),
)
def test_every_way_to_reach_a_client_is_still_a_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, apps_sites: set[Any], job_sites: set[Any]
) -> None:
    # M0-PR9 review: a tuple alias or a raw REST call left every contract green.
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_bypass.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(stops, "REPO", root)
    module = "tools/databricks/stray_bypass.py"
    assert stops.python_primitive_sites() - set(stops.PRIMITIVE_SITES) == {(module, *site) for site in apps_sites}
    assert stops.python_job_run_sites() - set(stops.JOB_RUN_SITES) == {(module, *site) for site in job_sites}


def test_reads_through_a_tainted_alias_are_not_sites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Control: the over-approximation adds no site for a read or a value.
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_reads.py").write_text(
        "def f(w, n):\n    app = w.apps.get(n)\n    apps, jobs = w.apps, w.jobs\n"
        "    apps.list_deployments(n)\n    jobs.get_run(1)\n    getattr(app, 'url')\n    return app.update_time\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    assert stops.python_primitive_sites() == set(stops.PRIMITIVE_SITES)
    assert stops.python_job_run_sites() == set(stops.JOB_RUN_SITES)


def test_a_new_cli_that_reaches_a_reviewed_primitive_is_an_unreviewed_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_cli.py").write_text(
        "from tools.databricks.stop_app_fail_closed import stop_app_fail_closed\n\n\n"
        "def main():\n    stop_app_fail_closed(app_name='x')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    reached = stops.reaching_entry_points(root / "tools")
    stop_site = ("tools/databricks/stop_app_fail_closed.py", "stop_app_fail_closed", "stop")
    assert "tools.databricks.stray_cli" in reached[stop_site]
    assert "tools.databricks.stray_cli" not in stops.ENTRY_POINTS
