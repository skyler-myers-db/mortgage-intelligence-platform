"""W5e C7(c) at the layer the defect lived: scripts/deploy.sh, not the Python call.

A guard refusal in the CI staging install (a non-empty install suffix) used to
exit through deploy.sh's EXIT trap AFTER ``AGENT_PROXY_ACCESS_MUTATED=1`` was
armed, so ``compensate_agent_proxy_access`` ran the first-install
``deny_all_agent_proxy_access``: a workspace-global revoke of the proxy on every
endpoint, Supervisor agent and Genie space, the outage the guard exists to
prevent when the identity is shared with the live install.

The harness runs the REAL text: deploy.sh's ``run``/``step`` helpers and state
prelude, every function-only lifecycle library in deploy.sh's source order,
the real EXIT trap ``restore_rendered_sql_fail_closed``, and the
``install-scope-probe`` block of deploy_step_agentic_provisioning.sh (the
read-only probes, the compensation arm and the first proxy convergence).
``$PYTHON`` is a stub that logs every call and returns the exit code a case
asks for, so no workspace is touched.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import DEPLOY_SCRIPT, DEPLOY_SOURCED_SCRIPTS, DEPLOY_STEP_SCRIPTS

ROOT = Path(__file__).resolve().parents[2]
STEP_FILE = ROOT / "scripts" / "lib" / "deploy_step_agentic_provisioning.sh"
LIFECYCLE_LIBRARIES = tuple(path for path in DEPLOY_SOURCED_SCRIPTS if path not in DEPLOY_STEP_SCRIPTS)
ARM = 'if [[ "$DRY_RUN" -eq 0 ]]; then\n  AGENT_PROXY_ACCESS_MUTATED=1\nfi\n'
CONVERGE = "tools.databricks.agent_proxy_access --mode converge"
DENY_ALL = "tools.databricks.agent_proxy_access --mode deny-all"
STUB_PYTHON = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$HARNESS_LOG"
case "$*" in
  *"install_scope check-proxy"*) exit "${STUB_CHECK_PROXY_RC:-0}" ;;
  *"install_scope check-warehouse"*) exit "${STUB_CHECK_WAREHOUSE_RC:-0}" ;;
  *"agent_proxy_access --mode deny-all"*) exit "${STUB_DENY_ALL_RC:-0}" ;;
esac
exit 0
"""


def _bash() -> str:
    """deploy.sh expands possibly-empty arrays under ``set -u``: bash >= 4.4, as in CI."""

    for candidate in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/usr/bin/bash"):
        if not candidate or not Path(candidate).exists():
            continue
        probe = subprocess.run(
            [candidate, "-c", 'printf "%s %s" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}"'],
            capture_output=True, text=True, check=False, timeout=30,
        )
        major, minor = (int(part) for part in probe.stdout.split())
        if (major, minor) >= (4, 4):
            return candidate
    pytest.skip("no bash >= 4.4 on this host; scripts/deploy.sh itself cannot run here")


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin) + len(end)]


def _probe_block() -> str:
    return _between(STEP_FILE.read_text(encoding="utf-8"), "# >>> install-scope-probe", "# <<< install-scope-probe")


def _harness(tmp_path: Path, block: str) -> Path:
    entrypoint = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    stub = tmp_path / "python-stub.sh"
    stub.write_text(STUB_PYTHON, encoding="utf-8")
    stub.chmod(0o755)
    sources = "\n".join(f". {shlex.quote(str(path))}" for path in LIFECYCLE_LIBRARIES)
    helpers = _between(entrypoint, "\nstep() {", "\n}\n") + _between(entrypoint, "\nrun() {", "\n}\n")
    state = _between(entrypoint, "RESTORE_RENDERED_SQL_FAIL_CLOSED=0\n", 'FIRST_INSTALL_MARKED_PAYLOAD=""\n')
    script = f"""set -euo pipefail
RED='' YLW='' GRN='' DIM='' BOLD='' RST=''
DRY_RUN=0
STEP=0
PYTHON={shlex.quote(str(stub))}
{helpers}
{state}
{sources}
run_with_proof_signing_authority() {{ "$@"; }}
run_with_account_identity() {{ "$@"; }}
run_with_agent_proxy_credentials() {{ "$@"; }}
trap restore_rendered_sql_fail_closed EXIT
MIP_APP_NAME=mip-app-pr105-staging
_GRANTS_APP_NAME="$MIP_APP_NAME"
APP_DEPLOYMENT_LEASE_ID=11111111-1111-4111-8111-111111111111
MIP_APP_DEPLOYMENT_LEASE_ID="$APP_DEPLOYMENT_LEASE_ID"
MIP_DEPLOYMENT_SOURCE_GIT_SHA={"a" * 40}
GENIE_SPACE_ID=0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=operator@summit.example
_GRANTS_WAREHOUSE_ID=wh-ci
MIP_AGENT_SUPERVISOR_ID=ci-agent
MIP_AGENT_SUPERVISOR_ENDPOINT=ci-endpoint
MIP_AGENT_SUPERVISOR_ENDPOINT_ID=ci-endpoint-id
AGENT_PROXY_ACCESS_PRESERVE_ARGS=()
{block}
printf 'block completed\\n' >> "$HARNESS_LOG"
"""
    harness = tmp_path / "install-scope-trap.sh"
    harness.write_text(script, encoding="utf-8")
    return harness


def _run(tmp_path: Path, *, block: str | None = None, suffix: str = "_ci", **rcs: int) -> tuple[int, list[str], str]:
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HARNESS_LOG": str(log), "BUNDLE_VAR_install_suffix": suffix}
    env.update({f"STUB_{name.upper()}_RC": str(rc) for name, rc in rcs.items()})
    result = subprocess.run(
        [_bash(), str(_harness(tmp_path, _probe_block() if block is None else block))],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=120,
    )
    return result.returncode, log.read_text(encoding="utf-8").splitlines(), result.stderr


def _called(lines: list[str], fragment: str) -> list[str]:
    return [line for line in lines if fragment in line]


@pytest.mark.parametrize("refusing", ["check_proxy", "check_warehouse"])
def test_a_refusal_exits_with_no_proxy_convergence_and_no_deny_all_from_the_trap(tmp_path: Path, refusing: str) -> None:
    code, calls, _err = _run(tmp_path, **{refusing: 2})

    assert code == 2
    assert not _called(calls, "tools.databricks.agent_proxy_access"), "neither --mode converge nor --mode deny-all ran"
    assert "block completed" not in calls
    assert _called(calls, "app_deployment_lease release"), "the EXIT trap ran, found nothing armed, and released the lease"
    if refusing == "check_proxy":
        assert not _called(calls, "install_scope check-warehouse"), "the first refusal stops the deploy"


def test_the_probes_run_read_only_before_the_arm_and_the_first_convergence(tmp_path: Path) -> None:
    """Order inside the marked block. An arm moved ABOVE the block is caught by
    tests/unit/test_deploy_dev_workflow_contract.py::
    test_agent_proxy_acl_lifecycle_is_bound_and_compensated_before_lease_release
    (``AGENT_PROXY_ACCESS_MUTATED=1`` absent before the lease is armed); the two
    tests together pin probe-before-arm.
    """

    code, calls, err = _run(tmp_path)

    assert code == 0, err
    order = [next(i for i, line in enumerate(calls) if fragment in line)
             for fragment in ("install_scope check-proxy", "install_scope check-warehouse", CONVERGE)]
    assert order == sorted(order)
    assert "--supervisor-endpoint ci-endpoint" in calls[order[0]]
    assert "--application-id verifier-client --warehouse-id wh-ci" in calls[order[1]]
    block = _probe_block()
    assert block.index("install_scope check-warehouse") < block.index(ARM) < block.index("run converge_agent_proxy_boundary")


def test_the_live_install_never_runs_the_probes(tmp_path: Path) -> None:
    code, calls, err = _run(tmp_path, suffix="")

    assert code == 0, err
    assert not _called(calls, "install_scope")
    assert len(_called(calls, CONVERGE)) == 1


def test_an_armed_compensation_reaches_deny_all_and_a_suffixed_refusal_fails_loudly(tmp_path: Path) -> None:
    """The harness can see the defect: an arm moved above the probes runs deny-all from the trap.

    That deny-all names this install's Supervisor endpoint as the only owned one,
    and when it refuses (exit 2) the compensation fails loudly: no denial wait,
    exit 90, and the signed lease is retained.
    """

    probe = _probe_block()
    rearmed = probe.replace(ARM, "").replace("# >>> install-scope-probe\n", "# >>> install-scope-probe\n" + ARM)
    assert rearmed != probe

    code, calls, err = _run(tmp_path, block=rearmed, check_proxy=2, deny_all=2)

    assert code == 90, err
    deny = _called(calls, DENY_ALL)
    assert len(deny) == 1 and deny[0].endswith("--install-owned-endpoint ci-endpoint")
    assert not _called(calls, "verify_agent_proxy_identity_boundary")
    assert not _called(calls, "app_deployment_lease release")
    assert "deny-all refused before any revoke" in err


def test_the_live_deny_all_names_no_owned_endpoint_and_still_proves_denial(tmp_path: Path) -> None:
    """An armed live-install failure keeps its unguarded deny-all and the denial proof."""

    code, calls, _err = _run(tmp_path, block="AGENT_PROXY_ACCESS_MUTATED=1\nfalse\n", suffix="", deny_all=1)

    assert code == 90
    deny = _called(calls, DENY_ALL)
    assert len(deny) == 1 and "--install-owned-endpoint" not in deny[0]
    assert _called(calls, "verify_agent_proxy_identity_boundary"), "the live path is unchanged"
