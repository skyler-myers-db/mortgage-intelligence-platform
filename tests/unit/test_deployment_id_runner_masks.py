"""A deployment id a sourced env file hands the release is a runner mask before any command gets it.

A deployment id is identifying (tools/clients/redact.py; a plan prints only
its sha256, tools/databricks/active_deployment_pin.py). The release takes the
signed rollback record's deployment id from the binding env file a capture, a
proof, a re-pin or an inspect writes, and a quiesce window's blue, start and
proven deployment ids from the window's env files, then hands them to
commands whose lines ``scripts/deploy.sh`` echoes in a public job log
(``--rollback-deployment-id``, ``--expected-rollback-deployment-id``,
``--to``, ``--repinned``). ``mask_deployment_ids`` registers them right after
each file is sourced.

Each harness runs the real function with the real ``redact`` module on one
merged stream (tests/fixtures/runner_mask_harness.py); the tools are stand-ins
that write the env file. tests/unit/test_runtime_id_runner_masks.py covers the
helper and the App's own ids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_contract_text, deploy_entrypoint_text
from tests.fixtures.runner_mask_harness import (
    WRAPPERS,
    carrying,
    env_writer,
    merged_log,
    shell_function,
)

DEPLOYMENT = "deployment-EXAMPLE-01"
DEPLOYMENT_NAMES = (
    "MIP_APP_ROLLBACK_DEPLOYMENT_ID",
    "MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID",
    "MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID",
    "MIP_QUIESCE_WINDOW_PROVEN_DEPLOYMENT_ID",
)


@pytest.mark.parametrize("name", DEPLOYMENT_NAMES)
def test_each_deployment_id_a_sourced_env_carries_is_masked(tmp_path: Path, name: str) -> None:
    code, lines = merged_log(
        tmp_path,
        f'mask_deployment_ids\necho "\\$ tool --rollback-deployment-id ${{{name}}}"',
        env={name: DEPLOYMENT},
    )

    assert code == 0, lines
    assert carrying(lines, DEPLOYMENT) == [
        f"::add-mask::{DEPLOYMENT}",
        f"$ tool --rollback-deployment-id {DEPLOYMENT}",
    ]


BINDING = {"MIP_APP_ROLLBACK_RECORD_VERSION": "7", "MIP_APP_ROLLBACK_DEPLOYMENT_ID": DEPLOYMENT}
BLUE, START = "deployment-EXAMPLE-blue", "deployment-EXAMPLE-start"
CAPTURE_STATE = (
    "set +u\nAPP_NAME=mip-app\nAPP_FAIL_CLOSED_NAME=mip-app\n_GRANTS_APP_NAME=mip-app\nCLIENT_AGENTIC_TIER=off\n"
    "MIP_APP_URL=https://mip-app.example.invalid\nAPP_LAST_DEPLOY_PAYLOAD=payload.json\n"
    "APP_RESOURCE_BINDING_PAYLOAD=bindings.json\nMIP_APP_DEPLOYMENT_LEASE_ID=lease-1\nGENIE_SPACE_ID=space-1\n"
    "APP_RELEASE_ID=release-1\nQUIESCE_WINDOW_PLAN_JSON=plan.json\nQUIESCE_WINDOW_TREE_SHA256=tree\n"
    'APP_ROLLBACK_BINDING_ENV="$PWD/binding.env"\n'
)
ROLLBACK_COMMAND = 'echo "\\$ tool --rollback-deployment-id ${MIP_APP_ROLLBACK_DEPLOYMENT_ID}"\n'
REPIN_STUB = (
    'repin_and_close_quiesce_window() { echo "\\$ tool --to $1 --expected-rollback-deployment-id '
    '${QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID:-}"; }\n'
)
QUIET = (
    "quiesce_window_has_reason() { return 1; }\nmint_m2m_token() { :; }\narm_quiesce_window_watchdog() { :; }\n"
    "disarm_quiesce_window_watchdog() { :; }\ncancel_quiesce_window_deadman() { :; }\n"
    "read_quiesce_window_deadman_run_id() { :; }\n"
)
# (the function, the tools it runs and the env each writes, the shell that calls it and uses an id, the ids used)
SITES: dict[str, tuple[str, dict[str, dict[str, str]], str, tuple[str, ...]]] = {
    "core-capture": (
        "capture_core_last_good_app",
        {"app_core_rollback": BINDING},
        "capture_core_last_good_app\n" + ROLLBACK_COMMAND,
        (DEPLOYMENT,),
    ),
    "agentic-capture": (
        "capture_last_good_app",
        {"app_deployment_rollback": BINDING},
        "capture_last_good_app\n" + ROLLBACK_COMMAND,
        (DEPLOYMENT,),
    ),
    "signed-blue-refresh": (
        "refresh_signed_blue_binding",
        {"app_deployment_rollback": BINDING},
        "refresh_signed_blue_binding\n" + ROLLBACK_COMMAND,
        (DEPLOYMENT,),
    ),
    "window-open": (
        "open_quiesce_window",
        {
            "quiesce_window": {
                "MIP_QUIESCE_WINDOW_DEADMAN_RUN_ID": "41",
                "MIP_QUIESCE_WINDOW_BLUE_RESTORABLE": "1",
                "MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID": BLUE,
                "MIP_QUIESCE_WINDOW_TREE_SHA256": "tree",
            }
        },
        'open_quiesce_window\necho "\\$ tool --expected-rollback-deployment-id ${QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID}"\n',
        (BLUE,),
    ),
    "window-await": (
        "repin_proven_quiesce_window",
        {"quiesce_window": {"MIP_QUIESCE_WINDOW_PROVEN_DEPLOYMENT_ID": START}},
        REPIN_STUB + 'repin_proven_quiesce_window "$PWD/window.env"\n',
        (START,),
    ),
    "window-compensation": (
        "compensate_open_quiesce_window",
        {
            "quiesce_window": {
                "MIP_QUIESCE_WINDOW_JOURNAL_STATE": "window_proven",
                "MIP_QUIESCE_WINDOW_BLUE_RESTORABLE": "1",
                "MIP_QUIESCE_WINDOW_START_DEPLOYMENT_ID": START,
                "MIP_QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID": BLUE,
                "MIP_QUIESCE_WINDOW_TREE_SHA256": "tree",
            }
        },
        REPIN_STUB + "compensate_open_quiesce_window\n",
        (START, BLUE),
    ),
    "window-repin": (
        "repin_and_close_quiesce_window",
        {"app_core_rollback": BINDING, "quiesce_window": {}},
        "QUIESCE_WINDOW_BLUE_DEPLOYMENT_ID=blue-already-masked\nrepin_and_close_quiesce_window proven-already-masked\n"
        + ROLLBACK_COMMAND,
        (DEPLOYMENT,),
    ),
}


def _site(repo: Path, site: str, *, redact: bool = True) -> tuple[int, list[str], tuple[str, ...]]:
    name, tools, call, ids = SITES[site]
    for module, values in tools.items():
        env_writer(repo, module, values)
    code, lines = merged_log(repo, WRAPPERS + QUIET + shell_function(name) + CAPTURE_STATE + call, redact=redact)
    return code, lines, ids


@pytest.mark.parametrize("site", sorted(SITES))
def test_a_sourced_deployment_id_is_masked_before_the_first_command_handed_it(tmp_path: Path, site: str) -> None:
    code, lines, ids = _site(tmp_path, site)

    assert code == 0, lines
    for value in ids:
        printed = carrying(lines, value)
        assert printed[0] == f"::add-mask::{value}", (value, lines)
        assert len(printed) >= 2 and all(line.startswith("$ ") for line in printed[1:]), (value, lines)


@pytest.mark.parametrize("site", sorted(SITES))
def test_a_deployment_id_that_cannot_be_masked_is_handed_to_no_command(tmp_path: Path, site: str) -> None:
    code, lines, ids = _site(tmp_path, site, redact=False)

    assert code == 99, lines
    assert not [line for line in lines for value in ids if value in line], lines


def test_the_signed_blue_proof_masks_the_record_it_sources_before_anything_else() -> None:
    # Step 0a's proof is inline in its step file, not a function to drive; pin its order.
    text = deploy_entrypoint_text()
    proof = text.index('        prove_or_restore_core_blue "$APP_ROLLBACK_BINDING_ENV"\n')
    sourced = '      . "$APP_ROLLBACK_BINDING_ENV"\n      set +a\n      mask_deployment_ids\n      APP_SIGNED_BLUE_AVAILABLE=1\n'
    assert text.count(sourced) == 1 and text.index(sourced) > proof


def test_every_sourced_binding_and_window_env_is_followed_by_the_mask() -> None:
    """A new place that sources one of these files must mask what it read."""

    lines = deploy_contract_text().splitlines()
    sources = [
        index
        for index, line in enumerate(lines)
        if line.strip() in ('. "$APP_ROLLBACK_BINDING_ENV"', '. "$refreshed_env"', '. "$env_file"')
    ]
    assert len(sources) == 10, [lines[index] for index in sources]
    unmasked = []
    for index in sources:
        following = [line.strip() for line in lines[index + 1 : index + 4]]
        if not any(line.startswith("mask_deployment_ids") for line in following):
            unmasked.append(next(text for text in reversed(lines[:index]) if text.endswith("() {")))
    # The two that hand no deployment id on: a failed open's dead-man run id, and
    # the dead-man recovery, whose re-pin takes the deployment from the journal itself.
    assert unmasked == ["read_quiesce_window_deadman_run_id() {", "recover_deadman_quiesce_window() {"]
