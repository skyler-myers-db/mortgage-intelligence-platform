"""A refused core pre-activation proof never stops the signed blue it protects (M2-PR7b review).

The core activation block (``# >>> core tier: activation and capture >>>``,
read from ``deploy_entrypoint_text()``) runs over a signed blue with deploy.sh's
real EXIT trap (``restore_rendered_sql_fail_closed``), the real EXIT-trap
compensation (``deploy_app_release_lifecycle.sh``), the real window library
and the real pre-activation proof functions. A fake ``$PYTHON`` records every
tool call: ``blue_restore_proof prepare-activation`` refuses (an unverified
frontend without its ack, a rebuild that is not blue's set, no recorded
payload), and the core tier-b restore refuses ``no_restore_payload`` as it
would for an adopted blue no release ever proved.

Over signed blue, step 10b is reached in the window state ``blue_degraded``:
step 4c's synced-catalog quiesce marks it in either tier. A core estate may
have no Lakebase synced tables at all (only the agentic step creates them),
where the real runtime converge fails ("Cannot grant runtime access before the
Lakebase sync schema exists"). The core tier never converges that access back
(its success path leaves it quiesced), so the refusal must exit with blue still
serving: no stop, no restore over blue, treatment back to runtime and the lease
released, whether or not the synced tables exist.
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
  *"app_core_rollback restore-payload"*)
    prev=""; for arg in "$@"; do [[ "$prev" != "--out" ]] || printf '{}' > "$arg"; prev="$arg"; done ;;
  *"blue_restore_proof prepare-activation"*)
    exit "${PROOF_RC:-0}" ;;
  *"app_core_rollback restore "*)
    echo "[mip-core-blue] REFUSED no_restore_payload" >&2; exit 2 ;;
  *"converge_app_lakebase_sync_access --mode runtime"*)
    # The real tool over an estate with no synced tables (SYNC_RUNTIME_RC=1).
    [[ "${SYNC_RUNTIME_RC:-0}" -eq 0 ]] || \
      echo "RuntimeError: Cannot grant runtime access before the Lakebase sync schema exists" >&2
    exit "${SYNC_RUNTIME_RC:-0}" ;;
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
reconcile_reviewed_function_execute_grants() { echo "function-reconcile" >> "$CALLS"; }
# The real deploy_app_snapshot's first statement is the proof (pinned below).
deploy_app_snapshot() { prove_blue_restorable_before_activation; echo "apps deploy" >> "$CALLS"; exit 0; }
CLIENT_AGENTIC_TIER=off; CLIENT_INSTALL_CLASS=demo; APP_RUNTIME_ENV=sandbox
_GRANTS_APP_NAME=mip-app; APP_NAME=mip-app; APP_FAIL_CLOSED_NAME=mip-app
APP_SP_CLIENT_ID=app-client; APP_SP_SCIM_ID=app-scim; APP_EXPECTED_IDENTITY_ARGS=()
CORE_BLUE_RESTORE_PAYLOAD_ARGS=()
DEPLOYMENT_SYNC_CATALOG=sync; DEPLOYMENT_SYNC_SCHEMA=s; DEPLOYMENT_SYNC_TABLES=t
APP_ROLLBACK_SECRET_SCOPE=scope; MIP_LAKEBASE_INSTANCE=instance; LAKEBASE_DATABASE=mip_app_state
MIP_APP_DEPLOYMENT_LEASE_ID=lease; APP_DEPLOYMENT_LEASE_ID=lease
SOURCE_GIT_SHA=sha; APP_RELEASE_ID=aaaaaaaaaaaa-bbbbbbbbbbbb
MIP_APP_URL=https://app.example.invalid; MIP_APP_ROLLBACK_DEPLOYMENT_ID=deployment-blue
_GRANTS_WAREHOUSE_ID=warehouse; _GRANTS_CATALOG=mip
APP_FAIL_CLOSED_ARMED=1; FIRST_INSTALL_APP_CREATED=0; APP_ACCESS_QUARANTINED=0
APP_SIGNED_BLUE_AVAILABLE=1; BLUE_RESTORE_PROVEN=0; PREACTIVATION_APP_ACL_MUTATED=0
AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=0; RESTORE_RENDERED_SQL_FAIL_CLOSED=0
TREATMENT_RUNTIME_QUIESCED=1; REVIEWED_FUNCTION_GRANTS_PROVEN=1; LAKEBASE_RUNTIME_ACCESS_PROVEN=1
QUIESCE_WINDOW_BLUE_RESTORABLE=0; SKIP_SMOKE=1
"""
STOPS = ("stop_app_fail_closed", "app_core_rollback restore ", "apps deploy")
RELEASE = "app_deployment_lease release"
SYNC_RUNTIME = "converge_app_lakebase_sync_access --mode runtime"


def _run(
    tmp_path: Path, *, upgrade: str, window: str, proof_rc: int, sync_rc: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
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
        + function("restore_rendered_sql_fail_closed")
        # The trap's first call records the state its compensation is given.
        + 'disarm_quiesce_window_watchdog() { echo "trap state=$APP_UPGRADE_STATE window=$QUIESCE_WINDOW_STATE" >> "$CALLS"; }\n'
        + f"APP_UPGRADE_STATE={upgrade}; QUIESCE_WINDOW_STATE={window}\n"
        # deploy.sh's own EXIT trap (it exits 90 itself when compensation is unproven).
        + "trap restore_rendered_sql_fail_closed EXIT\n"
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
            "SYNC_RUNTIME_RC": str(sync_rc),
            "REAL_PYTHON": sys.executable,
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    return result, [line for line in calls.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize(
    ("upgrade", "window", "sync_rc"),
    [
        # Step 10b's real state over signed blue: step 4c marked blue_degraded,
        # with (0) and without (1) Lakebase synced tables on the estate.
        ("blue_quiesced", "blue_degraded", 0),
        ("blue_quiesced", "blue_degraded", 1),
        ("blue_quiesced", "blue_serving", 0),
        ("blue_active", '""', 0),
    ],
)
def test_a_refused_proof_exits_with_blue_serving_never_restored_over_or_stopped(
    tmp_path: Path, upgrade: str, window: str, sync_rc: int
) -> None:
    result, calls = _run(tmp_path, upgrade=upgrade, window=window, proof_rc=2, sync_rc=sync_rc)

    assert result.returncode == 2, result.stderr
    assert "green activation refused before it began" in result.stderr
    assert not [call for call in calls if any(stop in call for stop in STOPS)], calls
    proof = next(i for i, call in enumerate(calls) if "prepare-activation" in call)
    assert [call for call in calls[:proof] if call.startswith("treatment")] == [], (
        "the proof precedes the activation quiesce"
    )
    assert f"trap state={upgrade} window={window.strip(chr(34))}" in calls, "the activation never began"
    treatment = [call for call in calls if call.startswith("treatment")]
    assert treatment == ["treatment runtime"], "blue's treatment authority converges back with the App up"
    assert not [call for call in calls if "converge_campaign_treatment_access" in call], (
        "no secondary treatment quiescence: the compensation was proven"
    )
    assert not [call for call in calls if SYNC_RUNTIME in call], (
        "the core tier leaves step 4c's synced-catalog access quiesced, as its success path does"
    )
    assert RELEASE in calls[-1], "the lease is released, so the re-run need not wait for expiry"


def test_a_proven_blue_is_quiesced_only_after_the_proof_and_then_activated(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, upgrade="blue_quiesced", window="blue_degraded", proof_rc=0)

    assert result.returncode == 0, result.stderr
    order = [
        next(i for i, call in enumerate(calls) if needle in call)
        for needle in ("restore-payload", "prepare-activation", "treatment quiesce", "apps deploy")
    ]
    assert order == sorted(order), calls
    assert sum("prepare-activation" in call for call in calls) == 1, "deploy_app_snapshot's own call is a no-op"
    rebuild = next(call.split() for call in calls if "restore-payload" in call)
    payload = Path(rebuild[rebuild.index("--out") + 1])
    proof = next(call for call in calls if "prepare-activation" in call)
    assert f"--restore-payload {payload}" in proof, "the rebuilt payload reached the proof"
    assert not payload.exists(), "recorded now, so its temp file (operator values) is discarded"


def test_the_real_snapshot_activation_still_proves_first() -> None:
    snapshot = function("deploy_app_snapshot")
    assert snapshot.splitlines()[1:4] == [
        '  local label="$1"',
        "  # Attack O2 (M2-PR7): blue's tier-b restore proof, once, before the first activation.",
        "  prove_blue_restorable_before_activation",
    ]


def test_step_10b_over_signed_blue_is_reached_degraded_in_the_core_tier() -> None:
    """The harness state above is the real one: step 4c marks blue degraded at top level."""

    grants = (LIB / "deploy_step_lakebase_and_uc_grants.sh").read_text(encoding="utf-8")
    close = grants.index("\nclose_quiesce_window\n")
    quiesce = grants.index('\nstep "quiesce legacy and target Lakebase synced-catalog access"\n')
    # Unindented: in neither an if nor a tier branch, so the core tier runs it too.
    assert "\nmark_blue_degraded " in grants[close:quiesce]
