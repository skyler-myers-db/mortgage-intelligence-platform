"""What the apps CLI prints when a mutation returns never reaches the release's job log.

``databricks apps deploy`` renders the created deployment as JSON on its
stdout when it returns (its id, its source path, its creator), and ``apps
start`` and ``apps update`` render the whole App: its service principal's
name and OAuth client ids, its active and pending deployments, its URL
(the CLI's ``cmdio.Render``; text mode with no template falls through to
JSON). ``scripts/deploy.sh`` used to leave that stdout on the job log, which
is public for a governed release. The shell holds none of those values at
that moment, so no runner mask could come first: the created deployment's id
was printed before the capture handed it to the shell and
``mask_deployment_ids`` registered it.

``keep_cli_result_private`` runs the command with its stdout in a private
file, never the log, registers the deployment ids the result names as runner
masks (through the real ``tools.clients.redact.emit_mask``) and removes the
file. The command's stderr and exit status stay its own.

Each harness runs the reviewed shell with the real ``redact`` module on one
merged stream (tests/fixtures/runner_mask_harness.py), with a ``databricks``
that prints what the real CLI prints.
tests/unit/test_bootstrap_warehouse_release_sandbox.py proves the same on a
whole fresh-client release, whose CLI stand-in prints both results.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import function
from tests.fixtures.deploy_script import deploy_contract_text
from tests.fixtures.runner_mask_harness import (
    carrying,
    env_writer,
    merged_log,
    shell_function,
    shell_slice,
)

DEPLOYMENT = "deployment-EXAMPLE-created"
ACTIVE, PENDING = "deployment-EXAMPLE-active", "deployment-EXAMPLE-pending"
CREATOR = "creator-EXAMPLE"
ARTIFACTS = "/Workspace/EXAMPLE/deployment-artifacts"
SP_NAME, OAUTH_CLIENT, OAUTH_INTEGRATION = "app-sp-name-EXAMPLE", "oauth2-client-EXAMPLE", "oauth2-integration-EXAMPLE"
APP_URL = "https://mip-app-EXAMPLE.example.invalid"
# What the real CLI renders when ``apps deploy`` returns, and when ``apps start`` or ``apps update`` does.
CREATED = json.dumps(
    {
        "deployment_id": DEPLOYMENT,
        "mode": "SNAPSHOT",
        "creator": CREATOR,
        "deployment_artifacts": {"source_code_path": ARTIFACTS},
        "status": {"state": "SUCCEEDED"},
    }
)
APP = json.dumps(
    {
        "name": "mip-app",
        "service_principal_name": SP_NAME,
        "oauth2_app_client_id": OAUTH_CLIENT,
        "oauth2_app_integration_id": OAUTH_INTEGRATION,
        "url": APP_URL,
        "active_deployment": {"deployment_id": ACTIVE, "creator": CREATOR},
        "pending_deployment": {"deployment_id": PENDING},
    }
)
RESULT_ONLY = (CREATOR, ARTIFACTS, SP_NAME, OAUTH_CLIENT, OAUTH_INTEGRATION, APP_URL)
HELPER = function("keep_cli_result_private")
# A CLI that prints ``$RESULT`` on stdout and a progress line on stderr, as the real one does.
CLI = 'databricks() { echo "cli progress on stderr" >&2; printf \'%s\' "$RESULT"; return "${CLI_RC:-0}"; }\n'
DEPLOY = "run keep_cli_result_private databricks apps deploy mip-app --json @payload.json --timeout 20m\n"
DEPLOY_ECHO = "$ keep_cli_result_private databricks apps deploy mip-app --json @payload.json --timeout 20m"
LATER_COMMAND = 'echo "\\$ tool --rollback-deployment-id $D"\n'


def _nothing_of_the_result(lines: list[str]) -> None:
    for value in RESULT_ONLY:
        assert not carrying(lines, value), (value, lines)
    assert not [line for line in lines if line.lstrip().startswith("{")], "no rendered JSON on the log"


# --------------------------------------------------------------------------
# The helper
# --------------------------------------------------------------------------


def test_the_created_deployment_is_never_printed_and_its_id_is_masked_before_any_later_use(tmp_path: Path) -> None:
    code, lines = merged_log(tmp_path, HELPER + CLI + DEPLOY + LATER_COMMAND, env={"RESULT": CREATED, "D": DEPLOYMENT})

    assert code == 0, lines
    assert lines == [
        DEPLOY_ECHO,
        "cli progress on stderr",
        f"::add-mask::{DEPLOYMENT}",
        f"$ tool --rollback-deployment-id {DEPLOYMENT}",
        "reached-after-block",
    ]


def test_an_app_result_is_never_printed_and_the_deployments_it_names_are_masked(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path, HELPER + CLI + "run keep_cli_result_private databricks apps update mip-app --json @bindings.json\n",
        env={"RESULT": APP},
    )

    assert code == 0, lines
    assert lines == [
        "$ keep_cli_result_private databricks apps update mip-app --json @bindings.json",
        "cli progress on stderr",
        f"::add-mask::{ACTIVE}",
        f"::add-mask::{PENDING}",
        "reached-after-block",
    ]


@pytest.mark.parametrize("result", ["", "Deployment succeeded\n", "[]", '{"status": {"state": "SUCCEEDED"}}'])
def test_a_result_that_names_no_deployment_registers_nothing_and_is_not_printed(tmp_path: Path, result: str) -> None:
    code, lines = merged_log(tmp_path, HELPER + CLI + DEPLOY, env={"RESULT": result})

    assert code == 0, lines
    assert lines == [DEPLOY_ECHO, "cli progress on stderr", "reached-after-block"]


def test_a_failed_command_keeps_its_exit_status_and_its_stderr_and_still_prints_no_result(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path,
        HELPER + CLI + 'keep_cli_result_private databricks apps deploy mip-app || echo "exit status: $?"\n',
        env={"RESULT": CREATED, "CLI_RC": "7"},
    )

    assert code == 0, lines
    assert lines == ["cli progress on stderr", "exit status: 7", "reached-after-block"], "nothing masked, nothing printed"


def test_outside_actions_the_result_is_still_private_and_no_interpreter_is_started(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path, "PYTHON=/nonexistent/python\n" + HELPER + CLI + DEPLOY, actions=False, redact=False, env={"RESULT": CREATED}
    )

    assert code == 0, lines
    assert lines == [DEPLOY_ECHO, "cli progress on stderr", "reached-after-block"]


@pytest.mark.parametrize("failure", ["no-interpreter", "no-mask-module"])
def test_a_result_whose_deployment_cannot_be_masked_stops_the_step(tmp_path: Path, failure: str) -> None:
    broken = "PYTHON=/nonexistent/python\n" if failure == "no-interpreter" else ""
    code, lines = merged_log(
        tmp_path,
        broken + HELPER + CLI + DEPLOY + LATER_COMMAND,
        redact=failure != "no-mask-module",
        env={"RESULT": CREATED, "D": DEPLOYMENT},
    )

    assert code == 99, lines
    assert not carrying(lines, DEPLOYMENT), "neither the failure nor a later command printed the id"
    assert "reached-after-block" not in lines


def test_the_result_file_is_owner_only_and_removed(tmp_path: Path) -> None:
    held = tmp_path / "held"
    held.mkdir()
    # The command reports, on stderr, the mode of the one file that holds its stdout.
    (tmp_path / "probe.py").write_text(
        "import glob, os, stat, sys\n"
        "(path,) = glob.glob(os.path.join('held', 'mip-cli-result.*'))\n"
        "print('mode', oct(stat.S_IMODE(os.stat(path).st_mode)), file=sys.stderr)\n",
        encoding="utf-8",
    )
    # macOS's ``mktemp -t`` ignores TMPDIR: the helper's temporary file is created under ``held`` instead.
    probe = (
        'mktemp() { command mktemp "$PWD/held/$2"; }\n'
        'databricks() { "$PYTHON" probe.py; printf \'%s\' "$RESULT"; }\n'
    )
    code, lines = merged_log(tmp_path, HELPER + probe + DEPLOY, env={"RESULT": CREATED})

    assert code == 0, lines
    assert lines == [DEPLOY_ECHO, "mode 0o600", f"::add-mask::{DEPLOYMENT}", "reached-after-block"]
    assert list(held.iterdir()) == [], "the result is removed once its ids are masks"


# --------------------------------------------------------------------------
# The three call sites, each the reviewed shell
# --------------------------------------------------------------------------

ACTIVATION_STATE = (
    "prove_blue_restorable_before_activation() { :; }\nassert_expected_app_identity() { :; }\n"
    'emit_app_deploy_payload() { echo "{}" > "$1"; }\n'
    "APP_NAME=mip-app\nAPP_SOURCE_PATH=/Workspace/EXAMPLE/app-src/release-1\nAPP_GIT_SHA=0123abcd\n"
    "APP_RELEASE_ID=release-1\nAPP_RELEASE_MANIFEST=manifest.json\nAPP_RELEASE_PRINCIPAL=deployer@example.invalid\n"
    "TARGET=client\nAPP_RELEASE_ROOT_LEAF=leaf\nMIP_APP_URL=https://mip-app.example.invalid\n"
    "APP_LAST_DEPLOY_PAYLOAD=\nAPP_DEPLOY_PAYLOAD=\n"
)


def test_the_activation_keeps_the_created_deployment_off_the_log_and_masks_its_id(tmp_path: Path) -> None:
    env_writer(tmp_path, "converge_static_app_source", {})
    code, lines = merged_log(
        tmp_path,
        HELPER + CLI + ACTIVATION_STATE + shell_function("deploy_app_snapshot") + 'deploy_app_snapshot "activate"\n' + LATER_COMMAND,
        env={"RESULT": CREATED, "D": DEPLOYMENT},
    )

    assert code == 0, lines
    (deploy,) = [index for index, line in enumerate(lines) if line.startswith("$ keep_cli_result_private databricks apps deploy mip-app --json @")]
    assert lines[deploy + 1 :] == [
        "cli progress on stderr",
        f"::add-mask::{DEPLOYMENT}",
        f"$ tool --rollback-deployment-id {DEPLOYMENT}",
        "reached-after-block",
    ]
    _nothing_of_the_result(lines)


def test_the_start_before_an_activation_keeps_the_app_off_the_log(tmp_path: Path) -> None:
    code, lines = merged_log(
        tmp_path,
        HELPER + CLI + shell_function("start_stopped_app_before_activation") + "APP_NAME=mip-app\nrun start_stopped_app_before_activation\n",
        env={"RESULT": APP},
    )

    assert code == 0, lines
    assert lines == [
        "$ start_stopped_app_before_activation",
        "cli progress on stderr",
        f"::add-mask::{ACTIVE}",
        f"::add-mask::{PENDING}",
        "reached-after-block",
    ]


def test_the_binding_update_keeps_the_app_off_the_log(tmp_path: Path) -> None:
    update = shell_slice(
        '    run keep_cli_result_private databricks apps update "$_GRANTS_APP_NAME" \\\n',
        '      --json "@$APP_RESOURCE_BINDING_PAYLOAD"\n',
    )
    code, lines = merged_log(
        tmp_path,
        HELPER + CLI + "_GRANTS_APP_NAME=mip-app\nAPP_RESOURCE_BINDING_PAYLOAD=bindings.json\n" + update,
        env={"RESULT": APP},
    )

    assert code == 0, lines
    assert lines == [
        "$ keep_cli_result_private databricks apps update mip-app --json @bindings.json",
        "cli progress on stderr",
        f"::add-mask::{ACTIVE}",
        f"::add-mask::{PENDING}",
        "reached-after-block",
    ]


# --------------------------------------------------------------------------
# Every apps mutation the release shell runs
# --------------------------------------------------------------------------

MUTATION = re.compile(r"\bdatabricks\s+apps\s+(create|deploy|start|stop|update|delete)\b")
# How a mutation's stdout stays off the log, and how many take each way.
PRIVATE = {
    # deploy, the start before an activation, the binding update.
    "keep_cli_result_private databricks apps ": 3,
    # The first install's create: its result is a private file the release reads.
    'run_json_to_file "$APP_CREATE_RESULT" databricks apps create': 1,
    # WR5's start (owner-run, never in a release).
    'databricks apps start "$WR5_APP_NAME" >/dev/null': 1,
}


def test_every_apps_mutation_the_shell_runs_keeps_its_result_off_the_log() -> None:
    """A new ``databricks apps <mutation>`` must not leave what the CLI renders on the job log."""

    calls = [
        line.strip()
        for line in deploy_contract_text().splitlines()
        if MUTATION.search(line) and not line.lstrip().startswith(("#", "echo ", "step "))
    ]
    ways = {way: sum(way in call for call in calls) for way in PRIVATE}
    assert ways == PRIVATE, calls
    assert len(calls) == sum(PRIVATE.values()), calls
