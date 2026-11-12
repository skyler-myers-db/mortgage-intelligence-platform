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
  preflight found no bundle warehouse to mask, and the log is public.
"""

from __future__ import annotations

import re

import pytest

from tests.fixtures.client_profiles import core_tier_profile
from tests.fixtures.release_sandbox import Release, core_client_ids, first_install_estate, release
from tests.fixtures.release_sandbox_python import LEASE_ID
from tests.fixtures.release_sandbox_python import WAREHOUSE as BUNDLE_WAREHOUSE

BOOT = "b0b1b2b3b4b5b6b7"
GENIE = "tools.databricks.provision_genie_space"
TREATMENT = "tools.databricks.ensure_campaign_treatment_table"
APPLY = ["tools.databricks.bundle_env", "deploy"]
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
