"""A fresh client's live release runs on the profile's bootstrap warehouse until step 4 (B1, wave 7 part 2).

CI's shape: the ``.env.local`` carries the client profile with
``MIP_BOOTSTRAP_WAREHOUSE_ID`` and no ``DATABRICKS_WAREHOUSE_ID`` (no profile
field; tools/clients/ci_profile.env_local_text writes none), the run is a live
``--phase release`` under ``GITHUB_ACTIONS=true``, and the deployer's bundle
state binds no warehouse. The release sandbox (tests/fixtures/release_sandbox.py)
runs the real ``scripts/deploy.sh`` against a first install, and its
``bundle_state bootstrap-warehouse-id`` runs the real check against a fresh
client's workspace. The run must:

* prove the bootstrap read-only before the lease and report
  ``sql warehouse: first-install-bootstrap``, the id masked before any use
  (the profile resolver masks every identifying value, the check its output)
  and printed only inside the commands it is handed;
* run step 0a's treatment-table proof and the step-0a Genie create on the
  bootstrap warehouse, and every warehouse reader up to the step-4 apply too;
* switch to the bundle's own warehouse right after the apply (the bundle
  summary's ``mip_serverless_sql``), so every later reader, the step-10 Genie
  rebind included, gets that one, and the bootstrap id is never used again;
* register that warehouse as a runner mask in the very parse that reads it
  from the summary, before any command is handed it: a first install's
  preflight found no bundle warehouse to mask, and the log is public;
* register the new App's resource id and its service principal's application
  and SCIM ids as runner masks where step 4 resolves them, and the captured
  deployment's id where the capture hands it over, each before any command is
  handed one: the platform mints them, so no client profile holds them
  (tests/unit/test_runtime_id_runner_masks.py drives each place on one stream);
* keep what the CLI renders when ``apps start`` and ``apps deploy`` return off
  the log altogether (the App with its service principal's name and OAuth
  client ids; the created deployment with its id, source path and creator),
  and register the created deployment's id as a runner mask right there, before
  the capture names it (tests/unit/test_cli_result_private.py drives the helper).
"""

from __future__ import annotations

import re

import pytest

from tests.fixtures.client_profiles import core_tier_profile
from tests.fixtures.release_sandbox import Release, core_client_ids, first_install_estate, release
from tests.fixtures.release_sandbox_cli import DEPLOY_RESULT_ONLY, START_RESULT_ONLY
from tests.fixtures.release_sandbox_python import APP, CAPTURED_DEPLOYMENT, LEASE_ID
from tests.fixtures.release_sandbox_python import WAREHOUSE as BUNDLE_WAREHOUSE

BOOT = "b0b1b2b3b4b5b6b7"
GENIE = "tools.databricks.provision_genie_space"
TREATMENT = "tools.databricks.ensure_campaign_treatment_table"
APPLY = ["tools.databricks.bundle_env", "deploy"]
# The App the first install creates: its resource id and its service principal's two ids.
APP_IDS = (APP["id"], APP["service_principal_client_id"], APP["service_principal_id"])
# mask_runtime_ids (scripts/deploy.sh): the values go in on stdin, never in the call.
MASK = "emit_mask(sys.stdin.read())"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(scope="module")
def fresh(tmp_path_factory: pytest.TempPathFactory) -> Release:
    return release(
        tmp_path_factory.mktemp("fresh-client-release"),
        core_tier_profile(MIP_BOOTSTRAP_WAREHOUSE_ID=BOOT),
        first_install_estate(),
        client_ids=core_client_ids(),
        operator_warehouse=None,
        smoke=True,
        flags=("--phase", "release"),
        env={"GITHUB_ACTIONS": "true"},
    )


def _detail(run: Release) -> str:
    return run.result.stdout[-6000:] + run.result.stderr[-6000:]


def _warehouse_of(argv: list[str]) -> str:
    return argv[argv.index("--warehouse-id") + 1]


def _split_at_the_apply(run: Release) -> tuple[list[list[str]], list[list[str]]]:
    """Every ``-m`` call before the step-4 apply, and every one after it."""

    modules = run.modules()
    (apply,) = [index for index, module in enumerate(modules) if module[:2] == APPLY]
    return modules[:apply], modules[apply + 1 :]


def test_the_release_completes_on_the_bootstrap_until_the_apply(fresh: Release) -> None:
    run = fresh
    out = run.result.stdout
    assert run.result.returncode == 0, _detail(run)
    assert "  sql warehouse: first-install-bootstrap\n" in out
    checks = [module for module in run.modules() if module[0] == "tools.databricks.bundle_state"]
    assert checks == [
        ["tools.databricks.bundle_state", "warehouse-id"],
        ["tools.databricks.bundle_state", "bootstrap-warehouse-id"],
    ], "the state is read, then the bootstrap proven, once each, before the lease"
    # The profile resolver masks every identifying value at resolve; the real
    # check masks the id it prints again.
    assert run.result.stderr.count(f"::add-mask::{BOOT}") == 2
    assert run.state["lease_released"] == LEASE_ID


def test_step_0a_and_its_genie_create_run_on_the_bootstrap_warehouse(fresh: Release) -> None:
    before, _after = _split_at_the_apply(fresh)

    (treatment,) = [module for module in before if module[0] == TREATMENT]
    assert "--allow-absent" in treatment and _warehouse_of(treatment) == BOOT
    (create,) = [module for module in before if module[0] == GENIE and "--plan-pin" not in module]
    assert _warehouse_of(create) == BOOT
    readers = [module for module in before if "--warehouse-id" in module]
    assert {_warehouse_of(module) for module in readers} == {BOOT}, readers


def test_step_4_switches_to_the_bundles_warehouse_and_step_10_rebinds_genie_to_it(fresh: Release) -> None:
    _before, after = _split_at_the_apply(fresh)

    readers = [module for module in after if "--warehouse-id" in module]
    assert readers and {_warehouse_of(module) for module in readers} == {BUNDLE_WAREHOUSE}, readers
    (rebind,) = [module for module in after if module[0] == GENIE]
    assert _warehouse_of(rebind) == BUNDLE_WAREHOUSE
    # Each child's exported DATABRICKS_WAREHOUSE_ID follows the same switch,
    # made from the summary the apply is followed by.
    calls = fresh.python_calls
    apply = next(index for index, call in enumerate(calls) if call["argv"][1:3] == APPLY)
    assert calls[apply + 1]["argv"][1:3] == ["tools.databricks.bundle_env", "summary"]
    before = {call["env"]["DATABRICKS_WAREHOUSE_ID"] for call in calls[: apply + 2]}
    assert BOOT in before and before <= {BOOT, None}, "unset before the preflight block, then the bootstrap"
    exported = {call["env"]["DATABRICKS_WAREHOUSE_ID"] for call in calls[apply + 2 :] if call["argv"][:1] == ["-m"]}
    assert exported == {BUNDLE_WAREHOUSE}, exported
    assert not any(BOOT in " ".join(module) for module in after), "the bootstrap id is never used after the apply"


def test_the_bootstrap_id_is_printed_only_inside_the_commands_it_is_handed(fresh: Release) -> None:
    printed = [line for line in fresh.result.stdout.splitlines() if BOOT in line]
    assert len(printed) >= 2 and all(f"--warehouse-id {BOOT}" in line for line in printed), printed
    assert all(ANSI.sub("", line).startswith("$ ") for line in printed), "a command echo, never prose"
    assert [line for line in fresh.result.stderr.splitlines() if BOOT in line] == [f"::add-mask::{BOOT}"] * 2


def test_the_bundles_own_warehouse_is_masked_at_the_switch_before_any_command_is_handed_it(fresh: Release) -> None:
    calls = fresh.python_calls
    apply = next(index for index, call in enumerate(calls) if call["argv"][1:3] == APPLY)
    parse = calls[apply + 2]["argv"]
    assert parse[0] == "-c" and "mip_serverless_sql" in parse[1], "the summary's parse follows the summary"
    assert "print(emit_mask(" in parse[1], "the id leaves the parse only through the mask"
    assert not any(BUNDLE_WAREHOUSE in " ".join(call["argv"]) for call in calls[: apply + 2]), "unused before it"
    # stderr: the runner reads the command there, and the capture of the parse's stdout keeps only the id.
    assert [line for line in fresh.result.stderr.splitlines() if BUNDLE_WAREHOUSE in line] == [
        f"::add-mask::{BUNDLE_WAREHOUSE}"
    ]
    out = fresh.result.stdout.splitlines()
    applied = next(index for index, line in enumerate(out) if "-m tools.databricks.bundle_env deploy " in line)
    printed = [index for index, line in enumerate(out) if BUNDLE_WAREHOUSE in line]
    assert printed and printed[0] > applied, "its first print is a command echo after the switch"
    echoes = [ANSI.sub("", out[index]) for index in printed]
    assert all(line.startswith("$ ") and f"warehouse-id {BUNDLE_WAREHOUSE}" in line for line in echoes), echoes


def _carriers(run: Release, value: str) -> list[int]:
    """The index of every interpreter call handed ``value`` on its command line."""

    return [index for index, call in enumerate(run.python_calls) if value in " ".join(call["argv"])]


def _is_mask(call: dict[str, object]) -> bool:
    argv = call["argv"]
    return isinstance(argv, list) and argv[:1] == ["-c"] and MASK in argv[1]


def test_the_new_apps_ids_are_masked_where_step_4_resolves_them_before_any_command_is_handed_one(
    fresh: Release,
) -> None:
    calls = fresh.python_calls
    # The last of step 4's three parses of the created App; the mask is the very next interpreter call.
    (parse,) = [index for index, call in enumerate(calls) if '.get("service_principal_id")' in " ".join(call["argv"])]
    mask = parse + 1
    assert _is_mask(calls[mask]), calls[mask]["argv"]
    assert not any(value in " ".join(calls[mask]["argv"]) for value in APP_IDS), "stdin, never the command line"
    for value in APP_IDS:
        carriers = _carriers(fresh, value)
        assert carriers and carriers[0] > mask, (value, carriers[:1], mask)
        # stderr: the runner reads the command there. Once, at the first resolve.
        assert [line for line in fresh.result.stderr.splitlines() if value in line] == [f"::add-mask::{value}"]
    out = fresh.result.stdout.splitlines()
    bound = next(index for index, line in enumerate(out) if "bundle_env deployment bind" in line)
    printed = [index for index, line in enumerate(out) if any(value in line for value in APP_IDS)]
    assert len(printed) >= 20 and printed[0] > bound, "every print follows the resolve, and there are many"
    first = ANSI.sub("", out[printed[0]])
    assert first.startswith("$ ") and f"--app-application-id {APP_IDS[1]}" in first, first


def test_the_captured_deployment_id_is_masked_before_the_prune_is_handed_it(fresh: Release) -> None:
    calls = fresh.python_calls
    (capture,) = [index for index, call in enumerate(calls) if call["argv"][1:3] == ["tools.databricks.app_core_rollback", "capture"]]
    assert _is_mask(calls[capture + 1]), calls[capture + 1]["argv"]
    (prune,) = _carriers(fresh, CAPTURED_DEPLOYMENT)
    assert prune > capture + 1 and calls[prune]["argv"][1] == "tools.databricks.app_release_prune"
    # stderr: twice, and nothing else. Once from the result the activation's CLI call returned
    # (the CLI stand-in prints the created deployment as the real CLI does), once at the capture.
    assert [line for line in fresh.result.stderr.splitlines() if CAPTURED_DEPLOYMENT in line] == [
        f"::add-mask::{CAPTURED_DEPLOYMENT}"
    ] * 2
    printed = [ANSI.sub("", line) for line in fresh.result.stdout.splitlines() if CAPTURED_DEPLOYMENT in line]
    assert len(printed) == 1 and printed[0].startswith("$ ") and "--rollback-deployment-id" in printed[0], printed


def test_what_the_cli_renders_for_the_start_and_the_activation_never_reaches_the_log(fresh: Release) -> None:
    # The real CLI prints the App when ``apps start`` returns and the created deployment when
    # ``apps deploy`` does; the stand-in prints both, with values no read hands the shell.
    assert [call["argv"][:2] for call in fresh.cli_calls if call["argv"][:2] in (["apps", "start"], ["apps", "deploy"])] == [
        ["apps", "start"],
        ["apps", "deploy"],
    ]
    rendered = [*START_RESULT_ONLY.values(), DEPLOY_RESULT_ONLY["creator"], DEPLOY_RESULT_ONLY["deployment_artifacts"]["source_code_path"]]
    for value in rendered:
        assert value not in fresh.transcript, value
    assert f'"deployment_id": "{CAPTURED_DEPLOYMENT}"' not in fresh.transcript, "no rendered deployment"
    out = [ANSI.sub("", line) for line in fresh.result.stdout.splitlines()]
    assert "$ start_stopped_app_before_activation" in out
    assert [line for line in out if "databricks apps deploy" in line] == [
        next(line for line in out if line.startswith("$ keep_cli_result_private databricks apps deploy mip-app --json @"))
    ]
    # The created deployment's id is a runner mask in the interpreter call right after the
    # last one before the activation (the static-source converge), ahead of the capture.
    calls = fresh.python_calls
    (converge,) = [index for index, call in enumerate(calls) if call["argv"][1:2] == ["tools.databricks.converge_static_app_source"]]
    result_mask = calls[converge + 1]["argv"]
    assert result_mask[0] == "-c" and "emit_mask(" in result_mask[1] and '"deployment_id"' in result_mask[1], result_mask
    assert CAPTURED_DEPLOYMENT not in " ".join(result_mask), "the result goes in on stdin, never the command line"
    (capture,) = [index for index, call in enumerate(calls) if call["argv"][1:3] == ["tools.databricks.app_core_rollback", "capture"]]
    assert converge + 1 < capture


def test_nothing_to_mask_starts_no_interpreter(fresh: Release) -> None:
    # A first install's journal read names no App: the helper registers nothing and runs nothing.
    masks = [index for index, call in enumerate(fresh.python_calls) if _is_mask(call)]
    assert len(masks) == 2, masks
