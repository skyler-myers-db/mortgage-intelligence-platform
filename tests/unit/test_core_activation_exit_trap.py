"""A refused core pre-activation proof never stops the signed blue it protects (M2-PR7b review).

The core activation block (``# >>> core tier: activation and capture >>>``,
read from ``deploy_entrypoint_text()``) runs over a signed blue in its step-0a
state (``blue_quiesced``, the quiesce window ``blue_serving``, treatment
quiesced) with the real EXIT-trap compensation
(``deploy_app_release_lifecycle.sh``), the real window library and the real
pre-activation proof functions. A fake ``$PYTHON`` records every tool call:
``blue_restore_proof prepare-activation`` refuses (an unverified frontend
without its ack, a rebuild that is not blue's set, no recorded payload), and
the core tier-b restore refuses ``no_restore_payload`` as it would for an
adopted blue no release ever proved.

The refusal must exit while blue still serves: the trap converges runtime
access with the App up and never restores blue over itself, never stops it.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import block, function

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib"
FAKE_PYTHON = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS"
out=""; prev=""
for arg in "$@"; do
  if [[ "$prev" == "--out-env" ]]; then out="$arg"; fi
  prev="$arg"
done
case "$*" in
  *"blue_restore_proof prepare-activation"*)
    exit "${PROOF_RC:-0}" ;;
  *"app_core_rollback restore "*)
    echo "[mip-core-blue] REFUSED no_restore_payload" >&2; exit 2 ;;
  *"stop_app_fail_closed"*)
    [[ -z "$out" ]] || printf 'MIP_APP_STOP_OUTCOME=stopped\n' > "$out" ;;
esac
exit 0
"""
PRELUDE = """set -Eeuo pipefail
RED=""; YLW=""; DIM=""; RST=""
DRY_RUN=0
step() { echo "step: $*"; }
run_with_proof_signing_authority() { "$@"; }
run_with_account_identity() { "$@"; }
tier_is_agentic() { [[ "${CLIENT_AGENTIC_TIER:-on}" == "on" ]]; }
mint_m2m_token() { :; }
mint_signed_app_proof_token() { :; }
wait_for_app_deployable() { :; }
converge_app_treatment_access() { echo "treatment $1" >> "$CALLS"; }
converge_runtime_app_release_access() { echo "release-runtime" >> "$CALLS"; }
# The real deploy_app_snapshot's first statement is the proof (pinned below).
deploy_app_snapshot() { prove_blue_restorable_before_activation; echo "apps deploy" >> "$CALLS"; exit 0; }
CLIENT_AGENTIC_TIER=off; CLIENT_INSTALL_CLASS=demo; APP_RUNTIME_ENV=sandbox
_GRANTS_APP_NAME=mip-app; APP_NAME=mip-app; APP_FAIL_CLOSED_NAME=mip-app
APP_SP_CLIENT_ID=app-client; APP_EXPECTED_IDENTITY_ARGS=(); CORE_BLUE_RESTORE_PAYLOAD_ARGS=()
APP_ROLLBACK_SECRET_SCOPE=scope; MIP_LAKEBASE_INSTANCE=instance
MIP_APP_DEPLOYMENT_LEASE_ID=lease; SOURCE_GIT_SHA=sha; APP_RELEASE_ID=aaaaaaaaaaaa-bbbbbbbbbbbb
MIP_APP_URL=https://app.example.invalid; MIP_APP_ROLLBACK_DEPLOYMENT_ID=deployment-blue
_GRANTS_WAREHOUSE_ID=warehouse; _GRANTS_CATALOG=mip
APP_FAIL_CLOSED_ARMED=1; FIRST_INSTALL_APP_CREATED=0; APP_ACCESS_QUARANTINED=0
APP_SIGNED_BLUE_AVAILABLE=1; BLUE_RESTORE_PROVEN=0
TREATMENT_RUNTIME_QUIESCED=1; REVIEWED_FUNCTION_GRANTS_PROVEN=1; LAKEBASE_RUNTIME_ACCESS_PROVEN=1
QUIESCE_WINDOW_BLUE_RESTORABLE=0; SKIP_SMOKE=1
"""
STOPS = ("stop_app_fail_closed", "app_core_rollback restore ", "apps deploy")


def _run(tmp_path: Path, *, upgrade: str, window: str, proof_rc: int) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    fake = tmp_path / "fake-python"
    fake.write_text(FAKE_PYTHON, encoding="utf-8")
    fake.chmod(0o755)
    calls = tmp_path / "calls.log"
    calls.write_text("", encoding="utf-8")
    activation = block("# >>> core tier: activation and capture >>>", "# <<< core tier: activation and capture <<<")
    script = tmp_path / "harness.sh"
    script.write_text(
        PRELUDE
        + f"PYTHON={shlex.quote(str(fake))}\n"
        + "".join(
            f". {shlex.quote(str(LIB / name))}\n"
            for name in ("deploy_quiesce_window_lifecycle.sh", "deploy_app_release_lifecycle.sh")
        )
        + function("run")
        + function("prove_blue_restorable_before_activation")
        + function("rebuild_core_blue_restore_payload")
        + f"APP_UPGRADE_STATE={upgrade}; QUIESCE_WINDOW_STATE={window}\n"
        + "trap 'rc=$?; trap - EXIT; if [[ $rc -ne 0 ]]; then stop_app_after_failed_deploy; "
        + 'echo "compensation=$? state=$APP_UPGRADE_STATE window=$QUIESCE_WINDOW_STATE"; fi; exit $rc\' EXIT\n'
        + activation
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        env={
            **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME"}},
            "TMPDIR": str(tmp_path),
            "CALLS": str(calls),
            "PROOF_RC": str(proof_rc),
            "REAL_PYTHON": sys.executable,
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    return result, [line for line in calls.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize(("upgrade", "window"), [("blue_quiesced", "blue_serving"), ("blue_active", '""')])
def test_a_refused_proof_exits_with_blue_serving_never_restored_over_or_stopped(
    tmp_path: Path, upgrade: str, window: str
) -> None:
    result, calls = _run(tmp_path, upgrade=upgrade, window=window, proof_rc=2)

    assert result.returncode == 2, result.stderr
    assert "green activation refused before it began" in result.stderr
    assert not [call for call in calls if any(stop in call for stop in STOPS)], calls
    proof = next(i for i, call in enumerate(calls) if "prepare-activation" in call)
    assert [call for call in calls[:proof] if call.startswith("treatment")] == [], (
        "the proof precedes the activation quiesce"
    )
    assert calls[-1] == "treatment runtime", "blue's treatment authority converges back with the App up"
    assert "compensation=0" in result.stdout and f"state={upgrade}" in result.stdout


def test_a_proven_blue_is_quiesced_only_after_the_proof_and_then_activated(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, upgrade="blue_quiesced", window="blue_serving", proof_rc=0)

    assert result.returncode == 0, result.stderr
    order = [
        next(i for i, call in enumerate(calls) if needle in call)
        for needle in ("restore-payload", "prepare-activation", "treatment quiesce", "apps deploy")
    ]
    assert order == sorted(order), calls
    assert sum("prepare-activation" in call for call in calls) == 1, "deploy_app_snapshot's own call is a no-op"


def test_the_real_snapshot_activation_still_proves_first() -> None:
    snapshot = function("deploy_app_snapshot")
    assert snapshot.splitlines()[1:4] == [
        '  local label="$1"',
        "  # Attack O2 (M2-PR7): blue's tier-b restore proof, once, before the first activation.",
        "  prove_blue_restorable_before_activation",
    ]
