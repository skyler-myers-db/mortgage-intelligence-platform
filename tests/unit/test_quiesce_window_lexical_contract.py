"""Shell stop primitives live only in the window and EXIT-trap libraries (M0-PR8, attack O16).

The deploy's shell files may stop or start the App (``databricks apps
stop|start``, ``stop_app_fail_closed`` without ``--assert-identity-only``,
``--stop-app-for-mutation``, a mutating ``quiesce_window`` or
``app_deployment_rollback`` mode, an inline SDK ``apps.<stop|start|deploy|
update>`` or ``*_and_wait`` call) only in
``scripts/lib/deploy_quiesce_window_lifecycle.sh`` and the EXIT-trap
compensation in ``scripts/lib/deploy_app_release_lifecycle.sh``. The scanner
is ``tools/databricks/stop_primitive_map.shell_violations``; the mutation
self-test proves it sees each primitive in every other file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.databricks import stop_primitive_map as stops

REPO = Path(__file__).resolve().parents[2]
PRIMITIVES = (
    'databricks apps stop "$APP_NAME"',
    'run databricks apps start "$APP_NAME"',
    '"$PYTHON" -m tools.databricks.stop_app_fail_closed --app-name "$APP_NAME"',
    '"$PYTHON" -m tools.databricks.converge_lakebase_oauth_role --stop-app-for-mutation',
    '"$PYTHON" -m tools.databricks.quiesce_window open --plan "$P"',
    '"$PYTHON" -m tools.databricks.quiesce_window start --app-name "$APP_NAME"',
    '"$PYTHON" -m tools.databricks.app_deployment_rollback ensure --app-name "$APP_NAME"',
    '"$PYTHON" -m tools.databricks.app_deployment_rollback restore --app-name "$APP_NAME"',
    '"$PYTHON" -m tools.databricks.app_deployment_rollback repin --to "$ID"',
    "workspace.apps.stop(app_name)",
    "workspace.apps.start_and_wait(app_name)",
    "client.apps.deploy(app_name, deployment)",
    "client.apps.update(app_name, app)",
)
NOT_PRIMITIVES = (
    '"$PYTHON" -m tools.databricks.stop_app_fail_closed --app-name "$APP_NAME" --assert-identity-only',
    '"$PYTHON" -m tools.databricks.quiesce_window plan --position pre-lease',
    '"$PYTHON" -m tools.databricks.quiesce_window status --out-env "$F"',
    '"$PYTHON" -m tools.databricks.app_deployment_rollback capture --payload "$P"',
    '"$PYTHON" -m tools.databricks.converge_lakebase_oauth_role --check',
    "# databricks apps stop is the window's alone",
    "databricks apps get \"$APP_NAME\" -o json",
)


def _step_files() -> list[Path]:
    return [path for path in stops.shell_files() if str(path.relative_to(REPO)) not in stops.STOP_LIBRARIES]


def test_the_repository_shell_holds_stop_primitives_only_in_the_two_libraries() -> None:
    assert stops.repo_shell_violations() == []


def test_the_libraries_really_hold_the_primitives_the_contract_allows() -> None:
    """Positive control: unallowlisted, the two libraries would be reported."""

    window = (REPO / stops.WINDOW_LIBRARY).read_text(encoding="utf-8")
    trap = (REPO / stops.EXIT_TRAP_LIBRARY).read_text(encoding="utf-8")
    assert stops.shell_violations(stops.WINDOW_LIBRARY, window) == []
    assert len(stops.shell_violations("scripts/lib/elsewhere.sh", window)) >= 10
    assert any(
        "app_deployment_rollback restore" in finding
        for finding in stops.shell_violations("scripts/lib/elsewhere.sh", trap)
    )


@pytest.mark.parametrize("primitive", PRIMITIVES)
@pytest.mark.parametrize("path", _step_files(), ids=lambda path: path.name)
def test_inserting_a_primitive_into_any_other_shell_file_is_reported(path: Path, primitive: str) -> None:
    rel = str(path.relative_to(REPO))
    text = path.read_text(encoding="utf-8") + f"\nrun {primitive}\n"
    assert stops.shell_violations(rel, text), f"{primitive} in {rel} must be refused"


@pytest.mark.parametrize("primitive", PRIMITIVES)
def test_the_same_primitive_in_the_window_library_is_allowed(primitive: str) -> None:
    window = (REPO / stops.WINDOW_LIBRARY).read_text(encoding="utf-8") + f"\n{primitive}\n"
    assert stops.shell_violations(stops.WINDOW_LIBRARY, window) == []


@pytest.mark.parametrize("line", NOT_PRIMITIVES)
def test_read_only_modes_and_comments_are_not_primitives(line: str) -> None:
    assert stops.shell_violations("scripts/lib/deploy_step_bundle_apply.sh", line + "\n") == []


def test_continuation_lines_are_read_as_one_command() -> None:
    stop = '"$PYTHON" -m tools.databricks.stop_app_fail_closed \\\n  --app-name "$A" \\\n  "${ARGS[@]}"\n'
    check = stop.replace('"${ARGS[@]}"', "--assert-identity-only")
    assert stops.shell_violations("scripts/lib/x.sh", stop)
    assert stops.shell_violations("scripts/lib/x.sh", check) == []


def test_the_mode_after_the_module_decides_a_positional_entry_point() -> None:
    restore = '"$PYTHON" -m tools.databricks.app_deployment_rollback \\\n  restore --app-name "$A"\n'
    inspect = restore.replace("restore", "inspect")
    assert stops.shell_violations("scripts/lib/x.sh", restore)
    assert stops.shell_violations("scripts/lib/x.sh", inspect) == []


def test_every_step_file_that_needs_a_stop_calls_a_window_function() -> None:
    contract = {
        "deploy_step_signed_blue_proof.sh": ("run stop_unsigned_rebase_app", "prove_or_restore_signed_blue"),
        "deploy_step_bundle_apply.sh": (
            "ensure_quiesce_window_open_for WR4",
            "ensure_quiesce_window_open_for WR1",
            "ensure_quiesce_window_open_for WR2",
            "run stop_ungoverned_existing_app_before_binding_update",
        ),
        "deploy_step_lakebase_and_uc_grants.sh": (
            "ensure_quiesce_window_open_for WR3",
            "close_quiesce_window",
        ),
        "deploy_step_agentic_provisioning.sh": ("run stop_unsigned_rebase_app",),
        "deploy_step_agent_eval_and_smoke.sh": ("run stop_unproven_candidate_before_rollback",),
        "deploy_step_app_promotion_and_refresh.sh": ("run start_stopped_app_before_activation",),
        "deploy_first_install_lifecycle.sh": ("run stop_journal_owned_first_install_app",),
    }
    window = (REPO / stops.WINDOW_LIBRARY).read_text(encoding="utf-8")
    for name, calls in contract.items():
        text = (REPO / "scripts" / "lib" / name).read_text(encoding="utf-8")
        for call in calls:
            assert call in text, f"{name}: {call}"
            function = call.removeprefix("run ").split()[0]
            assert f"\n{function}() {{\n" in window, f"{function} lives in the window library"
