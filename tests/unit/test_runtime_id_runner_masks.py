"""An id the release resolves at run time is a runner mask before any command is handed it.

``scripts/deploy.sh``'s ``run`` helpers echo every command line, and the
governed release's job log is public. The client profile cannot hold the App's
resource id, its service principal's application and SCIM ids or a deployment
id (the platform mints them), so the profile's mask step never registered
them, and every ``--app-application-id``, ``--principal``, ``--app-scim-id``,
``--expected-app-id`` and ``--rollback-deployment-id`` echo printed them.

``mask_runtime_ids`` registers them through the real
``tools.clients.redact.emit_mask`` at each place the shell first holds one:

* the step-4 resolve after the bundle apply (a first install's first sight of
  its App), whose three parses discard stderr, where a mask is written;
* the App inventory before step 4 (a routine release's first sight);
* the signed first-install journal (an interrupted install's App);
* the EXIT trap's two fallback reads of the App's service principal;
* the window plan's probe file (``plan_quiesce_window``), which hands the
  shell the App's service principal before the lease, in a plan and in a
  release, ahead of every place above;
* every sourced rollback binding and quiesce window env file, for the
  deployment ids it carries (``mask_deployment_ids``).

Each harness runs the real shell with the real ``redact`` module, stdout and
stderr merged, so the order of a mask and the first use is the log's own.
tests/unit/test_deployment_id_runner_masks.py drives the deployment-id places,
and tests/unit/test_bootstrap_warehouse_release_sandbox.py proves the same on
a whole fresh-client release.
"""

from __future__ import annotations

import io
import json
import shlex
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import function
from tests.fixtures.deploy_script import deploy_contract_text
from tests.fixtures.runner_mask_harness import (
    WRAPPERS,
    carrying,
    env_writer,
    merged_log,
    shell_function,
    shell_slice,
)
from tools.clients.redact import emit_mask

APP_ID = "app-object-EXAMPLE"
CLIENT = "00000000-0000-4000-8000-0000000000aa"
SCIM = "7001"
DEPLOYMENT = "deployment-EXAMPLE-01"


# --------------------------------------------------------------------------
# The helper
# --------------------------------------------------------------------------


def test_each_value_is_registered_through_the_real_mask_rule_before_its_use(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path,
        'mask_runtime_ids "$A" "" "$B"\necho "\\$ tool --app-application-id $A --app-scim-id $B"',
        env={"A": CLIENT, "B": SCIM},
    )

    assert code == 0, lines
    assert lines == [
        f"::add-mask::{CLIENT}",
        f"::add-mask::{SCIM}",
        f"$ tool --app-application-id {CLIENT} --app-scim-id {SCIM}",
        "reached-after-block",
    ]
    rule = io.StringIO()
    for value in (CLIENT, "", SCIM):
        emit_mask(value, stream=rule, environ={"GITHUB_ACTIONS": "true"})
    assert rule.getvalue().splitlines() == lines[:2], "the shell helper writes exactly what emit_mask does"


def test_outside_actions_the_helper_is_silent_and_starts_no_interpreter(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path,
        'PYTHON=/nonexistent/python\nmask_runtime_ids "$A"\nmask_deployment_ids\necho used',
        actions=False,
        redact=False,
        env={"A": CLIENT, "MIP_APP_ROLLBACK_DEPLOYMENT_ID": DEPLOYMENT},
    )

    assert code == 0 and lines == ["used", "reached-after-block"], lines


@pytest.mark.parametrize("failure", ["no-interpreter", "no-mask-module"])
def test_a_mask_that_cannot_be_registered_stops_before_the_value_is_used(tmp_path: Path, failure: str) -> None:
    broken = "PYTHON=/nonexistent/python\n" if failure == "no-interpreter" else ""
    code, lines = merged_log(
        tmp_path,
        broken + 'mask_runtime_ids "$A"\necho "\\$ tool --app-application-id $A"',
        redact=failure != "no-mask-module",
        env={"A": CLIENT},
    )

    assert code == 99, lines
    assert not carrying(lines, CLIENT), "neither the failure nor a later command printed the id"
    assert "reached-after-block" not in lines


def test_the_values_go_in_on_stdin_never_on_a_command_line(tmp_path: Path) -> None:
    recorder = tmp_path / "python-recorder.sh"
    recorder.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > argv.txt\ncat > stdin.txt\n", encoding="utf-8"
    )
    recorder.chmod(0o755)
    code, lines = merged_log(
        tmp_path,
        f'PYTHON={shlex.quote(str(recorder))}\nmask_runtime_ids "$A" "$B"',
        redact=False,
        env={"A": CLIENT, "B": SCIM},
    )

    assert code == 0, lines
    argv = (tmp_path / "argv.txt").read_text(encoding="utf-8")
    assert "emit_mask(sys.stdin.read())" in argv and CLIENT not in argv and SCIM not in argv
    assert (tmp_path / "stdin.txt").read_text(encoding="utf-8") == f"{CLIENT}\n{SCIM}\n"


def test_nothing_to_register_starts_no_interpreter(tmp_path: Path) -> None:
    # Unset under nounset, and empty: neither is a failure, and neither runs anything.
    code, lines = merged_log(
        tmp_path,
        'PYTHON=/nonexistent/python\nmask_deployment_ids\nmask_runtime_ids "" "$EMPTY"\nmask_runtime_ids\necho used',
        redact=False,
        env={"EMPTY": ""},
    )

    assert code == 0 and lines == ["used", "reached-after-block"], lines


# --------------------------------------------------------------------------
# Step 4: the App's identity after the bundle apply
# --------------------------------------------------------------------------

STEP_4 = shell_slice('  APP_RESOURCE_JSON="$(databricks apps get', "# <<< app identity: mask after resolve <<<")
NEXT_COMMAND = (
    'echo "\\$ tool --expected-app-id ${APP_OBJECT_ID} --app-application-id ${APP_SP_CLIENT_ID} '
    '--app-scim-id ${APP_SP_SCIM_ID}"\n'
)
APPS_GET = 'databricks() { [[ -n "$APP_JSON" ]] || return 1; printf \'%s\' "$APP_JSON"; }\n_GRANTS_APP_NAME=mip-app\n'


def _app(**changes: object) -> str:
    app = {"name": "mip-app", "id": APP_ID, "service_principal_client_id": CLIENT, "service_principal_id": int(SCIM)}
    return json.dumps({**app, **changes})


def test_step_4_masks_the_apps_three_ids_before_the_first_command_is_handed_one(tmp_path: Path) -> None:
    code, lines = merged_log(tmp_path, APPS_GET + STEP_4 + "\n" + NEXT_COMMAND, env={"APP_JSON": _app()})

    assert code == 0, lines
    command = f"$ tool --expected-app-id {APP_ID} --app-application-id {CLIENT} --app-scim-id {SCIM}"
    assert lines == [f"::add-mask::{APP_ID}", f"::add-mask::{CLIENT}", f"::add-mask::{SCIM}", command, "reached-after-block"]


def test_step_4_masks_a_partial_identity_before_its_refusal(tmp_path: Path) -> None:
    # The refusal that follows the block names no id, but the mask comes first all the same.
    code, lines = merged_log(
        tmp_path,
        APPS_GET + STEP_4 + '\necho "resolved: ${APP_OBJECT_ID:+id} ${APP_SP_CLIENT_ID:+client} ${APP_SP_SCIM_ID:+scim}"',
        env={"APP_JSON": _app(id="", service_principal_id=None)},
    )

    assert code == 0, lines
    assert lines == [f"::add-mask::{CLIENT}", "resolved:  client ", "reached-after-block"]


def test_step_4_registers_nothing_for_an_app_it_cannot_read(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path,
        APPS_GET + STEP_4 + '\necho "resolved: [${APP_OBJECT_ID}${APP_SP_CLIENT_ID}${APP_SP_SCIM_ID}]"',
        env={"APP_JSON": ""},
    )

    assert code == 0 and lines == ["resolved: []", "reached-after-block"], lines


def test_step_4_is_silent_outside_actions(tmp_path: Path) -> None:
    code, lines = merged_log(tmp_path, APPS_GET + STEP_4 + "\n" + NEXT_COMMAND, actions=False, env={"APP_JSON": _app()})

    assert code == 0, lines
    assert not [line for line in lines if line.startswith("::")], lines
    assert lines[0].startswith("$ tool --expected-app-id "), "the ids still reach the command"


def test_step_4_stops_before_any_command_when_its_ids_cannot_be_masked(tmp_path: Path) -> None:
    # The call is bare: the entrypoint's errexit and ERR trap stop the release there (the
    # harness's trap exits 99). A call that swallowed the failure would run the next command.
    code, lines = merged_log(tmp_path, APPS_GET + STEP_4 + "\n" + NEXT_COMMAND, redact=False, env={"APP_JSON": _app()})

    assert code == 99, lines
    for value in (APP_ID, CLIENT, SCIM):
        assert not carrying(lines, value), (value, lines)
    assert "reached-after-block" not in lines


# --------------------------------------------------------------------------
# Before step 4: a routine release's App inventory
# --------------------------------------------------------------------------

# From the listing to the first check of what it read; the slice ends inside the
# inventory's ``if``, before that check, and the harness closes it.
INVENTORY_CHECK = '    if [[ -z "$_EXISTING_APP_ID" || -z "$_EXISTING_APP_SP_CLIENT_ID" || \\\n'
INVENTORY = (
    shell_slice('  _EXISTING_APPS_JSON="$(databricks apps list -o json)"', INVENTORY_CHECK).removesuffix(INVENTORY_CHECK)
    + "  fi\n"
)
APPS_LIST = 'databricks() { printf \'%s\' "$APPS_JSON"; }\n'
RECOVERY_COMMAND = (
    'echo "\\$ tool --application-id ${_EXISTING_APP_SP_CLIENT_ID} --expected-app-id ${_EXISTING_APP_ID} '
    '--expected-scim-id ${_EXISTING_APP_SP_SCIM_ID}"\n'
)


def test_a_routine_release_masks_the_existing_apps_ids_at_the_inventory(tmp_path: Path) -> None:
    apps = json.dumps([{"name": "another-app", "id": "x", "service_principal_client_id": "y"}, json.loads(_app())])
    code, lines = merged_log(tmp_path, APPS_LIST + INVENTORY + RECOVERY_COMMAND, env={"APPS_JSON": apps})

    assert code == 0, lines
    command = f"$ tool --application-id {CLIENT} --expected-app-id {APP_ID} --expected-scim-id {SCIM}"
    assert lines == [f"::add-mask::{APP_ID}", f"::add-mask::{CLIENT}", f"::add-mask::{SCIM}", command, "reached-after-block"]


def test_a_first_install_inventory_registers_nothing(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path,
        APPS_LIST + INVENTORY + 'echo "existing: [${_EXISTING_APP_ID}${_EXISTING_APP_SP_CLIENT_ID}]"',
        env={"APPS_JSON": "[]"},
    )

    assert code == 0 and lines == ["existing: []", "reached-after-block"], lines


def test_the_inventory_stops_before_any_command_when_its_ids_cannot_be_masked(tmp_path: Path) -> None:
    apps = json.dumps([json.loads(_app())])
    code, lines = merged_log(tmp_path, APPS_LIST + INVENTORY + RECOVERY_COMMAND, redact=False, env={"APPS_JSON": apps})

    assert code == 99, lines
    for value in (APP_ID, CLIENT, SCIM):
        assert not carrying(lines, value), (value, lines)
    assert "reached-after-block" not in lines


# --------------------------------------------------------------------------
# The signed first-install journal
# --------------------------------------------------------------------------

JOURNAL = function("refresh_first_install_journal_status")
JOURNAL_STATE = (
    "APP_FAIL_CLOSED_NAME=mip-app\nMIP_APP_DEPLOYMENT_LEASE_ID=lease-1\nSOURCE_GIT_SHA=0123abcd\n"
    "APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback\nMIP_LAKEBASE_INSTANCE=mip-app-state\nAPP_SP_CLIENT_ID=\n"
)
JOURNAL_COMMAND = (
    'echo "\\$ tool --application-id ${FIRST_INSTALL_APP_CLIENT_ID} --expected-app-id ${FIRST_INSTALL_APP_ID} '
    '--expected-scim-id ${FIRST_INSTALL_APP_SCIM_ID}"\n'
)


def _journal(status: str, ids: tuple[str, str, str]) -> dict[str, str]:
    names = ("MIP_FIRST_INSTALL_APP_ID", "MIP_FIRST_INSTALL_APP_CLIENT_ID", "MIP_FIRST_INSTALL_APP_SCIM_ID")
    return {"MIP_FIRST_INSTALL_JOURNAL_STATUS": status, **dict(zip(names, ids, strict=True))}


def test_a_journaled_apps_ids_are_masked_before_any_recovery_command(tmp_path: Path) -> None:
    env_writer(tmp_path, "app_first_install_journal", _journal("recover", (APP_ID, CLIENT, SCIM)))
    code, lines = merged_log(
        tmp_path, WRAPPERS + JOURNAL + JOURNAL_STATE + "refresh_first_install_journal_status\n" + JOURNAL_COMMAND
    )

    assert code == 0, lines
    assert lines[0].startswith("$ ") and "app_first_install_journal status" in lines[0]
    command = f"$ tool --application-id {CLIENT} --expected-app-id {APP_ID} --expected-scim-id {SCIM}"
    assert lines[1:] == [f"::add-mask::{APP_ID}", f"::add-mask::{CLIENT}", f"::add-mask::{SCIM}", command, "reached-after-block"]


def test_an_absent_journal_registers_nothing(tmp_path: Path) -> None:
    env_writer(tmp_path, "app_first_install_journal", _journal("absent", ("", "", "")))
    code, lines = merged_log(tmp_path, WRAPPERS + JOURNAL + JOURNAL_STATE + "refresh_first_install_journal_status\necho read")

    assert code == 0 and lines[1:] == ["read", "reached-after-block"], lines


def test_a_journal_whose_ids_cannot_be_masked_is_not_read(tmp_path: Path) -> None:
    env_writer(tmp_path, "app_first_install_journal", _journal("recover", (APP_ID, CLIENT, SCIM)))
    code, lines = merged_log(
        tmp_path,
        WRAPPERS + JOURNAL + JOURNAL_STATE + 'refresh_first_install_journal_status || echo "refused: $?"\n'
        'echo "recover with: [${APP_SP_CLIENT_ID}]"',
        redact=False,
    )

    assert code == 0, lines
    assert lines[-3:] == ["refused: 1", "recover with: []", "reached-after-block"], lines
    assert not carrying(lines, CLIENT) and not carrying(lines, APP_ID)


# --------------------------------------------------------------------------
# The EXIT trap's fallback read of the App's service principal
# --------------------------------------------------------------------------

FAIL_CLOSED = function("stop_and_quiesce_unproven_app")
FAIL_CLOSED_STATE = (
    "APP_FAIL_CLOSED_NAME=mip-app\nAPP_EXPECTED_IDENTITY_ARGS=()\nAPP_SP_CLIENT_ID=\nTREATMENT_RUNTIME_QUIESCED=0\n"
    # Stands for the real quiesce, whose bounded wrappers echo its --principal.
    'converge_app_treatment_access() { [[ -n "$APP_SP_CLIENT_ID" ]] || { echo "no App identity"; return 1; }; '
    'echo "\\$ tool --principal ${APP_SP_CLIENT_ID} --mode $1"; }\n'
)


def test_the_exit_traps_fallback_principal_is_masked_before_the_quiesce(tmp_path: Path) -> None:
    env_writer(tmp_path, "stop_app_fail_closed", {"MIP_APP_STOP_OUTCOME": "stopped"})
    code, lines = merged_log(
        tmp_path, APPS_GET + FAIL_CLOSED + FAIL_CLOSED_STATE + "stop_and_quiesce_unproven_app\n", env={"APP_JSON": _app()}
    )

    assert code == 0, lines
    assert lines == [f"::add-mask::{CLIENT}", f"$ tool --principal {CLIENT} --mode quiesce", "reached-after-block"]


def test_a_fallback_principal_that_cannot_be_masked_is_not_used(tmp_path: Path) -> None:
    env_writer(tmp_path, "stop_app_fail_closed", {"MIP_APP_STOP_OUTCOME": "stopped"})
    code, lines = merged_log(
        tmp_path,
        APPS_GET + FAIL_CLOSED + FAIL_CLOSED_STATE + 'stop_and_quiesce_unproven_app || echo "failed closed: $?"\n',
        redact=False,
        env={"APP_JSON": _app()},
    )

    assert code == 0, lines
    assert lines[-3:] == ["no App identity", "failed closed: 1", "reached-after-block"], lines
    assert not carrying(lines, CLIENT)


# The trap's own order (restore_rendered_sql_fail_closed): the stop, and when that fails the
# second quiesce, with the real functions and the real bounded-authority wrappers, each of
# which echoes --principal. No App identity is resolved yet on this path.
TRAP = (
    shell_function("run_with_account_identity")
    + shell_function("run_with_proof_signing_authority")
    + shell_function("converge_app_treatment_access")
    + FAIL_CLOSED
    + shell_function("quiesce_app_treatment_after_failed_stop")
    + "APP_FAIL_CLOSED_NAME=mip-app\nAPP_EXPECTED_IDENTITY_ARGS=()\nAPP_SP_CLIENT_ID=\n_EXISTING_APP_SP_CLIENT_ID=\n"
    "TREATMENT_RUNTIME_QUIESCED=0\nFIRST_INSTALL_APP_CREATED=0\n_GRANTS_WAREHOUSE_ID=warehouse-1\n_GRANTS_CATALOG=mip\n"
    "DATABRICKS_ACCOUNT_CLIENT_ID=account-EXAMPLE\nDATABRICKS_ACCOUNT_CLIENT_SECRET=placeholder\n"
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY=placeholder\n"
    'if stop_and_quiesce_unproven_app; then echo "trap: stopped and quiesced"; else\n'
    '  quiesce_app_treatment_after_failed_stop || echo "trap: the second quiesce failed too"\n'
    "fi\n"
)
QUIESCE = f"--principal {CLIENT} --mode quiesce"


def _trap_tools(repo: Path, *, stop_fails: bool) -> None:
    env_writer(repo, "stop_app_fail_closed", {"MIP_APP_STOP_OUTCOME": "stopped"})
    if stop_fails:
        # The identity-pinned stop cannot prove a stop (an API error, a denied stop).
        (repo / "tools" / "databricks" / "stop_app_fail_closed.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    (repo / "tools" / "databricks" / "converge_campaign_treatment_access.py").write_text("", encoding="utf-8")


def test_the_traps_second_read_masks_the_principal_when_the_stop_itself_failed(tmp_path: Path) -> None:
    # The stop returns before its own (masked) fallback read, so the second quiesce's read is the first sight.
    _trap_tools(tmp_path, stop_fails=True)
    code, lines = merged_log(tmp_path, APPS_GET + TRAP, env={"APP_JSON": _app()})

    assert code == 0, lines
    printed = carrying(lines, CLIENT)
    assert printed[0] == f"::add-mask::{CLIENT}", lines
    assert len(printed) == 3 and all(line.startswith("$ ") and QUIESCE in line for line in printed[1:]), printed
    assert lines[-1] == "reached-after-block" and "trap: the second quiesce failed too" not in lines


def test_the_trap_prints_no_principal_when_neither_read_can_mask_it(tmp_path: Path) -> None:
    # The first read blanks a principal it cannot mask; the second read must not hand the same id on.
    _trap_tools(tmp_path, stop_fails=False)
    code, lines = merged_log(tmp_path, APPS_GET + TRAP, redact=False, env={"APP_JSON": _app()})

    assert code == 0, lines
    assert not carrying(lines, CLIENT), lines
    assert not [line for line in lines if "converge_campaign_treatment_access" in line], "no quiesce was handed an id"
    assert "[deploy] treatment convergence lacks an App identity or warehouse." in lines, "the first read refused"
    assert lines[-3:] == [
        "[deploy] secondary treatment quiescence lacks a resolved App identity or warehouse.",
        "trap: the second quiesce failed too",
        "reached-after-block",
    ], lines


def test_the_traps_first_read_still_masks_and_quiesces_with_the_real_wrappers(tmp_path: Path) -> None:
    _trap_tools(tmp_path, stop_fails=False)
    code, lines = merged_log(tmp_path, APPS_GET + TRAP, env={"APP_JSON": _app()})

    assert code == 0, lines
    printed = carrying(lines, CLIENT)
    assert printed[0] == f"::add-mask::{CLIENT}" and len(printed) == 3, lines
    assert all(line.startswith("$ ") and QUIESCE in line for line in printed[1:]), printed
    assert lines[-2:] == ["trap: stopped and quiesced", "reached-after-block"]


# --------------------------------------------------------------------------
# The window plan's probe file: before the lease, in a plan and in a release
# --------------------------------------------------------------------------

# The real function and the real bounded-authority wrappers, each of which echoes
# the whole command line it is handed. The credentials are placeholders.
WINDOW_PLAN = (
    shell_function("run_with_account_identity")
    + shell_function("run_with_proof_signing_authority")
    + shell_function("run_with_lakebase_bootstrap_authority")
    + shell_function("plan_quiesce_window")
    + "tier_is_agentic() { return 1; }\ncollect_quiesce_window_binding_payload() { return 1; }\n"
    "nonblank_control_value() { echo seed@example.invalid; }\n"
    "_GRANTS_APP_NAME=mip-app\nAPP_ROLLBACK_SECRET_SCOPE=mip-app-rollback\nMIP_LAKEBASE_INSTANCE=mip-app-state\n"
    "LAKEBASE_DATABASE=mip_app_state\nTARGET=client\nQUIESCE_WINDOW_STATE=\nCLIENT_JOBS_PRINCIPAL=jobs-EXAMPLE\n"
    "DATABRICKS_ACCOUNT_CLIENT_ID=account-EXAMPLE\nDATABRICKS_ACCOUNT_CLIENT_SECRET=placeholder\n"
    "DATABRICKS_RELEASE_PROBE_CLIENT_ID=probe-EXAMPLE\nDATABRICKS_RELEASE_PROBE_CLIENT_SECRET=placeholder\n"
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY=placeholder\nMIP_LENDER_NAME=Summit\nMIP_LENDER_NMLS_ID=1\nMIP_TENANT_ID=t\n"
)
ROLE_CHECK = f"--application-id {CLIENT} --role-contract app"


def _window_tools(repo: Path, probe: str) -> None:
    """Stand-ins for the tools the plan runs; ``applicable`` writes ``probe`` as its env file."""

    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    (package / "quiesce_window.py").write_text(
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'applicable':\n"
        f"    open(args[args.index('--out-env') + 1], 'w', encoding='utf-8').write({probe!r})\n"
        "    print('[mip-quiesce-window] applicable: read')\n"
        "elif args[0] == 'plan':\n"
        "    json.dump({'code': 'none', 'reasons': []}, open(args[args.index('--out') + 1], 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    (package / "converge_lakebase_oauth_role.py").write_text("", encoding="utf-8")
    (repo / "jobs").mkdir(exist_ok=True)
    (repo / "jobs" / "lakebase_migrate.py").write_text("print('{}')\n", encoding="utf-8")


def _installed(client: str = CLIENT) -> str:
    return f"MIP_QUIESCE_WINDOW_APP_PRESENT=1\nMIP_QUIESCE_WINDOW_APP_CLIENT_ID={client}\nMIP_QUIESCE_WINDOW_REBASE=0\n"


def _plan_the_window(phase: str) -> str:
    return WINDOW_PLAN + f'DEPLOY_PHASE={phase}\nplan_quiesce_window pre-lease "$PWD/gate-report.json"\n'


@pytest.mark.parametrize("phase", ["plan", "release"])
def test_the_window_plan_masks_the_installed_apps_principal_before_its_role_check_echoes_it(
    tmp_path: Path, phase: str
) -> None:
    # Before the lease: a plan ends right after this gate and never reaches the inventory's mask, and a
    # release reaches it only after the lease. The probe file is the shell's first sight of the id in both.
    _window_tools(tmp_path, _installed())
    code, lines = merged_log(tmp_path, _plan_the_window(phase))

    assert code == 0, lines
    printed = carrying(lines, CLIENT)
    assert printed[0] == f"::add-mask::{CLIENT}", lines
    # Each of the two wrappers echoes the role check, and nothing else carries the id.
    assert len(printed) == 3 and all(line.startswith("$ ") and ROLE_CHECK in line for line in printed[1:]), printed
    assert lines.index(printed[0]) == lines.index("[mip-quiesce-window] applicable: read") + 1, "right after the probe"


@pytest.mark.parametrize("phase", ["plan", "release"])
def test_a_window_plan_whose_probe_cannot_be_masked_refuses_before_any_role_check(tmp_path: Path, phase: str) -> None:
    _window_tools(tmp_path, _installed())
    code, lines = merged_log(tmp_path, _plan_the_window(phase), redact=False)

    assert code == 2, lines
    assert not carrying(lines, CLIENT), "neither the failure nor a role check printed the id"
    assert not [line for line in lines if "converge_lakebase_oauth_role" in line], "no role check ran"
    assert "reached-after-block" not in lines


@pytest.mark.parametrize("probe", ["", "MIP_QUIESCE_WINDOW_APP_PRESENT=0\nMIP_QUIESCE_WINDOW_APP_CLIENT_ID=\n"])
def test_a_window_plan_with_no_app_registers_nothing(tmp_path: Path, probe: str) -> None:
    # No probe file, or one that names no App (a first install): nothing to mask, and no interpreter is needed.
    _window_tools(tmp_path, probe)
    code, lines = merged_log(tmp_path, _plan_the_window("release"), redact=False)

    assert code == 0, lines
    assert not [line for line in lines if line.startswith("::")], lines
    assert not [line for line in lines if "--role-contract app" in line], "no App role to check"
    assert lines[-1] == "reached-after-block"


def test_the_helper_never_prints_through_run() -> None:
    for name in ("mask_runtime_ids", "mask_deployment_ids"):
        body = function(name)
        assert "echo" not in body and "run " not in body, name
    calls = [line for line in deploy_contract_text().splitlines() if "mask_runtime_ids " in line or "mask_deployment_ids" in line]
    assert not [line for line in calls if line.lstrip().startswith(("run ", "run_"))], "a mask is never an echoed command"

