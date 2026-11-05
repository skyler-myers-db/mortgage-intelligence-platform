"""The core release's shell sites in the command of record (M2-PR7b).

The functions are the real ones (``deploy_entrypoint_text()``), driven in the
block harness (tests/fixtures/deploy_block_harness.py) with recording stub
tools: the core tier rebuilds an adopted blue's restore payload before the
pre-activation proof and hands it in only when the rebuild wrote one; the
unverified-frontend ack reaches the proof and the restore as a one-run
export; the signed-blue restore and step 0a dispatch to the core commands;
and the agentic tier's calls are unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import drive, function
from tests.fixtures.deploy_script import deploy_entrypoint_text

RELEASE_ID = f"{'0' * 12}-{'1' * 12}"
FRONTEND_ACK = "f" * 64
BASE = {
    "_GRANTS_APP_NAME": "synthetic-app",
    "APP_FAIL_CLOSED_NAME": "synthetic-app",
    "APP_ROLLBACK_SECRET_SCOPE": "synthetic-app-rollback",
    "MIP_LAKEBASE_INSTANCE": "synthetic-state",
    "CLIENT_AGENTIC_TIER": "off",
    "CLIENT_INSTALL_CLASS": "demo",
    "APP_RUNTIME_ENV": "sandbox",
    "SOURCE_GIT_SHA": "0" * 40,
    "APP_RELEASE_ID": RELEASE_ID,
    "MIP_APP_DEPLOYMENT_LEASE_ID": "lease-1",
    "MIP_APP_URL": "https://synthetic.example.invalid",
    "MIP_APP_ROLLBACK_DEPLOYMENT_ID": "deployment-blue",
    "_GRANTS_WAREHOUSE_ID": "warehouse-1",
    "_GRANTS_CATALOG": "mip",
    "BLUE_RESTORE_PROVEN": "0",
    "APP_SIGNED_BLUE_AVAILABLE": "1",
    "APP_ACCESS_QUARANTINED": "0",
    "LAKEBASE_RUNTIME_ACCESS_PROVEN": "1",
}


def _tools(repo: Path, rc: dict[str, int] | None = None) -> Path:
    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    recorded = repo / "calls.jsonl"
    for module in ("app_core_rollback", "blue_restore_proof", "app_deployment_rollback"):
        (package / f"{module}.py").write_text(
            "import json, os, sys\n"
            f"open({str(recorded)!r}, 'a').write(json.dumps([{module!r}, *sys.argv[1:]]) + '\\n')\n"
            "if sys.argv[1] == 'restore-payload' and os.environ.get('STUB_REBUILD') == '1':\n"
            "    open(sys.argv[sys.argv.index('--out') + 1], 'w').write('{}')\n"
            f"sys.exit({(rc or {}).get(module, 0)})\n",
            encoding="utf-8",
        )
    return recorded


def _run(tmp_path: Path, body: str, *, env: dict[str, str] | None = None, rc: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    recorded = _tools(repo, rc)
    shell = (
        'mint_m2m_token() { :; }\nmint_signed_app_proof_token() { :; }\n'
        'run_with_proof_signing_authority() { "$@"; }\nrun_with_account_identity() { "$@"; }\n'
        'converge_runtime_app_release_access() { echo converged-release-access; }\n'
        'converge_app_treatment_access() { echo "treatment $1"; }\n'
        'stop_and_quiesce_unproven_app() { echo stopped-and-quiesced; }\n'
        + function("tier_is_agentic")
        + function("prove_blue_restorable_before_activation")
        + function("rebuild_core_blue_restore_payload")
        + function("restore_signed_blue_while_quiesced")
        + function("core_blue_command")
        + function("restore_core_blue_while_quiesced")
        + function("prove_or_restore_core_blue")
        + body
    )
    values = {**BASE, **(env or {})}
    exported = {name: value for name, value in values.items() if name in {"STUB_REBUILD"}}
    shell = "".join(f"export {name}={value}\n" for name, value in exported.items()) + shell
    result = drive(repo, shell, dry_run=0, env=values, epilogue="")
    calls = [json.loads(line) for line in recorded.read_text().splitlines()] if recorded.exists() else []
    return result, calls


def _flags(argv: list[str]) -> dict[str, str]:
    return dict(zip(argv[2::2], argv[3::2], strict=False))


@pytest.mark.parametrize("rebuilt", [True, False])
def test_the_core_tier_rebuilds_blues_restore_payload_and_hands_it_in_only_when_written(
    tmp_path: Path, rebuilt: bool
) -> None:
    result, calls = _run(
        tmp_path,
        "prove_blue_restorable_before_activation\n",
        env={"STUB_REBUILD": "1" if rebuilt else "0"},
    )

    assert result.returncode == 0, result.stderr
    rebuild, proof = calls
    assert rebuild[:2] == ["app_core_rollback", "restore-payload"]
    flags = _flags(rebuild)
    assert (flags["--agentic-tier"], flags["--install-class"], flags["--app-env"]) == ("off", "demo", "sandbox")
    assert proof[:2] == ["blue_restore_proof", "prepare-activation"] and "--agentic-tier" in proof
    assert ("--restore-payload" in proof) is rebuilt
    if rebuilt:
        assert proof[proof.index("--restore-payload") + 1] == flags["--out"]


def test_a_failed_rebuild_refuses_before_the_proof_and_the_activation(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path, "prove_blue_restorable_before_activation\n", rc={"app_core_rollback": 1}
    )

    assert result.returncode == 2 and [call[:2] for call in calls] == [["app_core_rollback", "restore-payload"]]
    assert "blue's restore payload could not be read or rebuilt" in result.stderr


def test_no_rebuild_without_a_signed_blue_and_none_in_the_agentic_tier(tmp_path: Path) -> None:
    _result, first_install = _run(
        tmp_path / "a", "prove_blue_restorable_before_activation\n", env={"APP_SIGNED_BLUE_AVAILABLE": "0"}
    )
    assert [call[:2] for call in first_install] == [["blue_restore_proof", "prepare-activation"]]
    result, agentic = _run(
        tmp_path / "b",
        "prove_blue_restorable_before_activation\n",
        env={"CLIENT_AGENTIC_TIER": "on", "MIP_ACK_RESTORE_UNVERIFIED_FRONTEND": FRONTEND_ACK},
    )
    assert result.returncode == 0, result.stderr
    (proof,) = agentic
    assert "--agentic-tier" not in proof and "--restore-unverified-frontend-ack" not in proof
    assert "--restore-payload" not in proof, "the agentic tier's proof argv is unchanged"


def test_the_unverified_frontend_ack_reaches_the_core_proof_restore_and_step_0a(tmp_path: Path) -> None:
    env = {"MIP_ACK_RESTORE_UNVERIFIED_FRONTEND": FRONTEND_ACK}
    _result, calls = _run(tmp_path / "proof", "prove_blue_restorable_before_activation\n", env=env)
    assert calls[-1][calls[-1].index("--restore-unverified-frontend-ack") + 1] == FRONTEND_ACK
    _result, calls = _run(tmp_path / "restore", "restore_signed_blue_while_quiesced\n", env=env)
    assert calls[0][calls[0].index("--restore-unverified-frontend-ack") + 1] == FRONTEND_ACK
    _result, calls = _run(tmp_path / "ensure", "prove_or_restore_core_blue /tmp/out.env\n", env=env)
    assert calls[0][:2] == ["app_core_rollback", "ensure"]
    assert calls[0][calls[0].index("--restore-unverified-frontend-ack") + 1] == FRONTEND_ACK


def test_the_core_tier_restores_its_core_blue_never_the_v5_v6_record(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, "restore_signed_blue_while_quiesced\necho state=$APP_UPGRADE_STATE\n")

    assert result.returncode == 0, result.stderr
    (restore,) = calls
    assert restore[:2] == ["app_core_rollback", "restore"]
    flags = _flags(restore)
    assert flags["--agentic-tier"] == "off" and flags["--expected-rollback-deployment-id"] == "deployment-blue"
    assert "--restore-unverified-frontend-ack" not in restore
    assert "converged-release-access" in result.stdout and "treatment runtime" in result.stdout
    assert "state=blue_active" in result.stdout


def test_a_caller_with_no_resolved_tier_keeps_the_agentic_restore(tmp_path: Path) -> None:
    """Only exactly off restores the core blue: the v5/v6 restore's own harnesses resolve no tier."""

    for tier in ("on", ""):
        result, calls = _run(
            tmp_path / (tier or "unset"),
            'refresh_signed_blue_binding() { echo agentic-restore; return 1; }\n'
            "if restore_signed_blue_while_quiesced; then echo restored; else echo refused; fi\n",
            env={"CLIENT_AGENTIC_TIER": tier},
        )
        assert "agentic-restore" in result.stdout and calls == [], (tier, result.stderr)


def test_a_refused_core_restore_fails_closed_without_the_agentic_restore(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        "if restore_signed_blue_while_quiesced; then echo restored; else echo refused; fi\n",
        rc={"app_core_rollback": 2},
    )

    assert "refused" in result.stdout and "restored" not in result.stdout
    assert [call[0] for call in calls] == ["app_core_rollback"]
    assert "treatment runtime" not in result.stdout


def test_step_0a_and_the_exit_trap_dispatch_by_tier() -> None:
    text = deploy_entrypoint_text()
    assert (
        '      if tier_is_agentic; then\n'
        '        prove_or_restore_signed_blue "$APP_ROLLBACK_BINDING_ENV"\n'
        "      else\n"
    ) in text
    assert '        prove_or_restore_core_blue "$APP_ROLLBACK_BINDING_ENV"\n' in text
    trap = function("stop_app_after_failed_deploy")
    cleanup = trap[trap.index("    green_captured_cleanup_pending)") :]
    assert cleanup.splitlines()[1:6] == [
        '      if [[ "${CLIENT_AGENTIC_TIER:-}" == "off" ]]; then',
        "        # A core green holds no Gateway, Supervisor or proxy access (M2-PR7b).",
        '        echo "${YLW}[deploy] the core green is signed; converging its runtime operator access after a later failure.${RST}" >&2',
        "        converge_runtime_app_release_access && return 0",
        "        stop_and_quiesce_unproven_app || true",
    ]
    restore = function("restore_signed_blue_while_quiesced")
    assert '  if [[ "${CLIENT_AGENTIC_TIER:-}" == "off" ]]; then\n    restore_core_blue_while_quiesced\n' in restore
    assert restore.index("restore_core_blue_while_quiesced") < restore.index("refresh_signed_blue_binding")
    assert restore.index("contract migration window is open") < restore.index("restore_core_blue_while_quiesced")
