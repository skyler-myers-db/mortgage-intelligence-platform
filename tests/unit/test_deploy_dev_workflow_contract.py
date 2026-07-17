"""Contracts for the manual dev deployment workflow."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
DEPLOY_SCRIPT = REPO / "scripts" / "deploy.sh"


def _workflow_run_block(path: Path, step_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    step = text.index(f"- name: {step_name}")
    run = text.index("        run: |", step)
    end = text.find("\n      - name:", run + 1)
    return textwrap.dedent(text[run + len("        run: |\n") : end if end != -1 else None])


def _install_environment_recorder(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "child-env.log"
    recorder = bin_dir / "python"
    recorder.write_text(
        "#!/usr/bin/env bash\n"
        "{\n"
        "  echo '=== child ==='\n"
        "  /usr/bin/env | /usr/bin/sort\n"
        '} >> "$CHILD_ENV_LOG"\n',
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    return bin_dir, log


def _deploy_exit_trap_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("restore_rendered_sql_fail_closed() {")
    trap_line = "trap restore_rendered_sql_fail_closed EXIT"
    end = text.index(trap_line, start) + len(trap_line)
    return text[start:end]


def _runtime_grant_revoke_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("revoke_agent_runtime_bootstrap_grants() {")
    end = text.index("restore_rendered_sql_fail_closed() {", start)
    return text[start:end]


def _app_failure_compensation_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("converge_app_treatment_access() {")
    end = text.index("quiesce_app_treatment_after_failed_stop() {", start)
    return text[start:end]


def _deploy_auth_function_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("dotenv_value() {")
    end = text.index("# Step 0: preflight", start)
    return text[start:end]


def _proof_heartbeat_launcher_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("start_proof_signing_heartbeat() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _read_env_log(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )


def _run_deploy_auth_harness(tmp_path: Path, *, mode: str) -> dict[str, Any]:
    repo = tmp_path / mode
    tools_dir = repo / "tools"
    home = repo / "home"
    tools_dir.mkdir(parents=True)
    home.mkdir()
    reviewed_host = "https://reviewed-workspace.example"
    env_lines = [
        f"DATABRICKS_HOST={reviewed_host}",
        "DATABRICKS_CLIENT_ID=normal-app-client",
        "DATABRICKS_CLIENT_SECRET=normal-app-secret",
        "DATABRICKS_ADMIN_CLIENT_ID=admin-app-client",
        "DATABRICKS_ADMIN_CLIENT_SECRET=admin-app-secret",
        "DATABRICKS_VERIFIER_CLIENT_ID=verifier-client",
        "DATABRICKS_VERIFIER_CLIENT_SECRET=verifier-secret",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=runtime-secret",
    ]
    if mode == "pat":
        env_lines.append("DATABRICKS_TOKEN=reviewed-deployer-pat")
    (repo / ".env.local").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    (repo / "databricks.yml").write_text(
        "workspace:\n"
        f"  host: {reviewed_host}\n"
        "targets:\n"
        "  dev:\n"
        "    workspace:\n"
        f"      host: {reviewed_host}\n",
        encoding="utf-8",
    )
    (home / ".databrickscfg").write_text(
        "[DEFAULT]\n"
        "host = https://conflicting-default.example\n"
        "token = conflicting-default-token\n"
        "[REVIEWED]\n"
        f"host = {reviewed_host}\n"
        "token = reviewed-profile-token\n",
        encoding="utf-8",
    )
    mock_mint = tools_dir / "oauth_m2m_mint.py"
    mock_mint.write_text(
        """from __future__ import annotations
import json
import os
import sys
from pathlib import Path

with Path(os.environ["MINT_ENV_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(dict(os.environ)) + "\\n")
output = Path(sys.argv[sys.argv.index("--output-file") + 1])
output.write_text("minted-token\\n", encoding="utf-8")
""",
        encoding="utf-8",
    )
    deploy_log = repo / "deploy.env"
    mint_log = repo / "mint.json"
    m2m_log = repo / "m2m.env"
    runtime_log = repo / "runtime.env"
    authorized_runtime_log = repo / "runtime-authorized.env"
    profile_export = "export DATABRICKS_CONFIG_PROFILE=REVIEWED" if mode == "profile" else ""
    harness = repo / "auth-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
RED=""
DIM=""
RST=""
DRY_RUN=0
TARGET=dev
PYTHON={shlex.quote(sys.executable)}
MINT_ENV_LOG={shlex.quote(str(mint_log))}
M2M_ENV_LOG={shlex.quote(str(m2m_log))}
RUNTIME_ENV_LOG={shlex.quote(str(runtime_log))}
AUTHORIZED_RUNTIME_ENV_LOG={shlex.quote(str(authorized_runtime_log))}
export MINT_ENV_LOG M2M_ENV_LOG RUNTIME_ENV_LOG AUTHORIZED_RUNTIME_ENV_LOG
export MIP_COTALITY_ID_MASK_SECRET=cotality-secret
export MIP_GENIE_ACTION_SECRET_CURRENT=action-secret
export MIP_GENIE_ACTION_SECRET_PREVIOUS=previous-action-secret
export MIP_AI_GATEWAY_PROOF_SIGNING_KEY=proof-signing-secret
export MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY=model-signing-secret
export SALESFORCE_CLIENT_SECRET=salesforce-secret
export SALESFORCE_PASSWORD=salesforce-password
export SALESFORCE_SECURITY_TOKEN=salesforce-token
{profile_export}
{_deploy_auth_function_block()}
bind_deployment_workspace_auth
resolve_m2m_credential DATABRICKS_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_CLIENT_SECRET shell
resolve_m2m_credential DATABRICKS_ADMIN_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_ADMIN_CLIENT_SECRET shell
resolve_m2m_credential DATABRICKS_VERIFIER_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_VERIFIER_CLIENT_SECRET shell
resolve_m2m_credential DATABRICKS_AGENT_RUNTIME_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET shell
DATABRICKS_ACCOUNT_CLIENT_ID=hostile-account-client
DATABRICKS_ACCOUNT_CLIENT_SECRET=hostile-account-secret
export -n DATABRICKS_ACCOUNT_CLIENT_ID DATABRICKS_ACCOUNT_CLIENT_SECRET
env | sort > {shlex.quote(str(deploy_log))}
mint_m2m_token MIP_BEARER_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET
mint_m2m_token MIP_ADMIN_BEARER_TOKEN DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET
run_as_m2m_identity verifier DATABRICKS_VERIFIER_CLIENT_ID DATABRICKS_VERIFIER_CLIENT_SECRET \
  bash -c 'env | sort > "$1"' _ "$M2M_ENV_LOG"
run_as_m2m_identity agent-runtime DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
  DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET bash -c 'env | sort > "$1"' _ "$RUNTIME_ENV_LOG"
MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING=1 run_as_m2m_identity \
  agent-runtime DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
  DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
  bash -c 'env | sort > "$1"' _ "$AUTHORIZED_RUNTIME_ENV_LOG"
""",
        encoding="utf-8",
    )
    run_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "DATABRICKS_HOST": "https://ambient-wrong.example",
        "DATABRICKS_TOKEN": "ambient-wrong-token",
        "DATABRICKS_AUTH_TYPE": "pat",
    }
    result = subprocess.run(
        ["env", "-i", *(f"{key}={value}" for key, value in run_env.items()), "bash", str(harness)],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return {
        "deploy": _read_env_log(deploy_log),
        "mints": [json.loads(line) for line in mint_log.read_text(encoding="utf-8").splitlines()],
        "m2m": _read_env_log(m2m_log),
        "runtime": _read_env_log(runtime_log),
        "runtime_authorized": _read_env_log(authorized_runtime_log),
    }


def _run_bind_harness(
    tmp_path: Path,
    *,
    env_local: str,
    bundle_host: str,
    ambient: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    repo = tmp_path / "bind"
    bin_dir = repo / "bin"
    home = repo / "home"
    bin_dir.mkdir(parents=True)
    home.mkdir()
    (repo / ".env.local").write_text(env_local, encoding="utf-8")
    (repo / "databricks.yml").write_text(
        "targets:\n" "  dev:\n" "    workspace:\n" f"      host: {bundle_host}\n",
        encoding="utf-8",
    )
    mutation_marker = repo / "network-mutation-attempted"
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text(
        "#!/usr/bin/env bash\n" f"touch {shlex.quote(str(mutation_marker))}\n",
        encoding="utf-8",
    )
    fake_databricks.chmod(0o755)
    harness = repo / "bind-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
RED=""
RST=""
DRY_RUN=0
TARGET=dev
PYTHON={shlex.quote(sys.executable)}
{_deploy_auth_function_block()}
bind_deployment_workspace_auth
databricks apps list
""",
        encoding="utf-8",
    )
    run_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(home),
        **ambient,
    }
    return (
        subprocess.run(
            [
                "env",
                "-i",
                *(f"{key}={value}" for key, value in run_env.items()),
                "bash",
                str(harness),
            ],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        ),
        mutation_marker,
    )


def _run_deploy_exit_trap_harness(
    tmp_path: Path,
    *,
    original_rc: int,
    stop_result: int,
    quiesce_result: int,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    compensation_log = tmp_path / "compensation.log"
    harness = tmp_path / "deploy-exit-trap-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
RED=""
YLW=""
DIM=""
RST=""
PYTHON=python3
COMPENSATION_LOG={compensation_log!s}
STOP_RESULT={stop_result}
QUIESCE_RESULT={quiesce_result}
stop_app_after_failed_deploy() {{
  printf 'stop\\n' >> "$COMPENSATION_LOG"
  return "$STOP_RESULT"
}}
quiesce_app_treatment_after_failed_stop() {{
  printf 'quiesce\\n' >> "$COMPENSATION_LOG"
  return "$QUIESCE_RESULT"
}}
{_deploy_exit_trap_block()}
exit {original_rc}
""",
        encoding="utf-8",
    )
    return (
        subprocess.run(
            ["bash", str(harness)],
            text=True,
            capture_output=True,
            check=False,
        ),
        compensation_log,
    )


def _run_app_failure_compensation_harness(
    tmp_path: Path,
    *,
    state: str,
    rollback_result: int,
    stop_result: int,
) -> tuple[subprocess.CompletedProcess[str], str]:
    calls = tmp_path / f"app-compensation-{state}.log"
    fake_python = tmp_path / f"fake-python-{state}.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        f'if [[ "$*" == *app_deployment_rollback* ]]; then exit {rollback_result}; fi\n'
        f'if [[ "$*" == *stop_app_fail_closed* ]]; then exit {stop_result}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / f"app-compensation-{state}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_FAIL_CLOSED_ARMED=1
APP_FAIL_CLOSED_NAME=mip-app
APP_UPGRADE_STATE={shlex.quote(state)}
APP_ROLLBACK_SECRET_SCOPE=mip
APP_SIGNED_BLUE_AVAILABLE=1
TREATMENT_RUNTIME_QUIESCED={0 if state in {"blue_active", "green_verified", "green_treatment_pending_capture"} else 1}
APP_SP_CLIENT_ID=app-client
_EXISTING_APP_SP_CLIENT_ID=app-client
_GRANTS_WAREHOUSE_ID=warehouse-id
_GRANTS_CATALOG=mip
MIP_APP_URL=https://mip.example
MIP_BEARER_TOKEN=token
PYTHON={shlex.quote(str(fake_python))}
RED=""
YLW=""
RST=""
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_app_failure_compensation_block()}
stop_app_after_failed_deploy
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, calls.read_text(encoding="utf-8") if calls.exists() else ""


def _run_runtime_grant_cleanup_harness(
    tmp_path: Path,
    *,
    mode: str,
    through_exit: bool,
) -> subprocess.CompletedProcess[str]:
    harness = tmp_path / f"runtime-grant-{mode}-{through_exit}.sh"
    tail = (
        f"{_deploy_exit_trap_block()}\nexit 0"
        if through_exit
        else 'revoke_agent_runtime_bootstrap_grants\nprintf "active=%s\\n" "$AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE"'
    )
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
AGENT_RUNTIME_BOOTSTRAP_GRANTS_ACTIVE=1
_GRANTS_CATALOG=mip
_GRANTS_WAREHOUSE_ID=warehouse-id
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
PYTHON={shlex.quote(sys.executable)}
RED=""
YLW=""
DIM=""
RST=""
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
APP_FAIL_CLOSED_ARMED=0
APP_FAIL_CLOSED_NAME=""
databricks() {{
  if [[ "$*" == *"system.information_schema.schemata"* ]]; then
    if [[ {shlex.quote(mode)} == absent ]]; then
      printf '%s' '{{"status":{{"state":"SUCCEEDED"}},"result":{{"data_array":[[0]]}}}}'
    else
      printf '%s' '{{"status":{{"state":"SUCCEEDED"}},"result":{{"data_array":[[1]]}}}}'
    fi
  elif [[ "$*" == *"SHOW GRANTS "*" ON SCHEMA "* ]]; then
    if [[ {shlex.quote(mode)} == remaining ]]; then
      printf '%s' '{{"status":{{"state":"SUCCEEDED"}},"result":{{"data_array":[["mip.audit","CREATE TABLE"]]}}}}'
    else
      printf '%s' '{{"status":{{"state":"SUCCEEDED"}},"result":{{"data_array":[]}}}}'
    fi
  elif [[ {shlex.quote(mode)} == revoke_failure ]]; then
    printf '%s' '{{"status":{{"state":"FAILED"}}}}'
  else
    printf '%s' '{{"status":{{"state":"SUCCEEDED"}}}}'
  fi
}}
stop_app_after_failed_deploy() {{ return 0; }}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
{_runtime_grant_revoke_block()}
{tail}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )


def _commit_deploy_fixture(repo: Path) -> None:
    (repo / ".gitignore").write_text(
        ".env.local\nfrontend/dist/\n.databricks/\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "add", "scripts/deploy.sh", ".gitignore"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Deploy Test",
            "-c",
            "user.email=deploy-test@example.com",
            "commit",
            "-qm",
            "deploy fixture",
        ],
        cwd=repo,
        check=True,
    )


def test_deploy_dev_runs_real_deploy_script_manual_only() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "cron:" not in text
    assert "./scripts/deploy.sh" in text
    assert "--no-confirm" in text
    assert "Run databricks bundle validate/deploy here" not in text


def test_deploy_script_shell_is_syntactically_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_runtime_bootstrap_grant_cleanup_deactivates_after_exact_readback(
    tmp_path: Path,
) -> None:
    result = _run_runtime_grant_cleanup_harness(
        tmp_path,
        mode="success",
        through_exit=False,
    )

    assert result.returncode == 0, result.stderr
    assert "active=0" in result.stdout


def test_first_install_absent_audit_schema_needs_no_stale_grant_revoke(
    tmp_path: Path,
) -> None:
    result = _run_runtime_grant_cleanup_harness(
        tmp_path,
        mode="absent",
        through_exit=False,
    )

    assert result.returncode == 0, result.stderr
    assert "active=0" in result.stdout


def test_stale_runtime_bootstrap_grants_are_reconciled_before_build_or_bundle() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    lease = script.index("tools.databricks.app_deployment_lease acquire")
    early_cleanup = script.index("could not clear prior agent-runtime bootstrap privileges")
    frontend_build = script.index('step "build frontend')
    bundle_deploy = script.index(
        'step "deploy non-App bundle resources while the prior App snapshot remains live"'
    )

    assert lease < early_cleanup < frontend_build < bundle_deploy
    assert "SHOW GRANTS \\`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\\` ON SCHEMA" in script
    assert "SHOW GRANTS TO" not in script


@pytest.mark.parametrize("mode", ("revoke_failure", "remaining"))
def test_runtime_bootstrap_grant_cleanup_failure_forces_exit_90(
    tmp_path: Path,
    mode: str,
) -> None:
    result = _run_runtime_grant_cleanup_harness(
        tmp_path,
        mode=mode,
        through_exit=True,
    )

    assert result.returncode == 90
    assert "temporary-privilege revocation" in result.stderr


def test_success_path_exit_still_revokes_runtime_bootstrap_grants(tmp_path: Path) -> None:
    result = _run_runtime_grant_cleanup_harness(
        tmp_path,
        mode="success",
        through_exit=True,
    )

    assert result.returncode == 0, result.stderr
    assert "revoking temporary agent-runtime" in result.stderr


def test_deploy_mints_and_remints_distinct_admin_bearer_for_agent_eval() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "dotenv_value MIP_ADMIN_BEARER_TOKEN" not in text
    assert "databricks auth token" not in text
    assert "DATABRICKS_ADMIN_CLIENT_ID DATABRICKS_ADMIN_CLIENT_SECRET" in text
    assert (
        "normal, operator2, admin, verifier, and agent-runtime M2M client IDs "
        "must be pairwise distinct" in text
    )
    assert (
        text.count("mint_m2m_token MIP_ADMIN_BEARER_TOKEN") >= 2
    )  # initial per-run mint + immediate pre-eval remint
    remint_pos = text.index("A full deploy can exceed the workspace OAuth TTL")
    eval_pos = text.index("-m tools.databricks.run_agent_eval")
    assert remint_pos < eval_pos


def test_deploy_binds_deployer_auth_and_keeps_normal_app_oauth_shell_scoped() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    bind_call = text.index("\nbind_deployment_workspace_auth\n")
    m2m_resolution = text.index("for _M2M_NAME in")
    first_treatment_proof = text.index(
        'step "keep existing App treatment writes quiesced through non-App release work"'
    )
    assert bind_call < m2m_resolution < first_treatment_proof
    assert "MIP_DEPLOYER_DATABRICKS_HOST" in text
    assert "MIP_DEPLOYER_DATABRICKS_TOKEN" in text
    assert "MIP_DEPLOYER_DATABRICKS_PROFILE" in text
    assert "export -n DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET" in text
    assert 'resolve_m2m_credential "$_M2M_NAME" shell' in text
    assert 'DATABRICKS_HOST="${MIP_DATABRICKS_WORKSPACE_HOST:?}"' in text
    inventory_preflight = text.index("workspace_admin_inventory_principal")
    confirmation = text.index('read -r -p "About to DEPLOY')
    bundle_summary = text.index("databricks bundle summary")
    identity_match = text.index("bundle identity does not match the preflighted workspace-admin")
    assert bind_call < inventory_preflight < confirmation < bundle_summary < identity_match
    audit_lines = text.splitlines()
    audit_invocations: list[str] = []
    for index, line in enumerate(audit_lines):
        if not (
            '"$PYTHON" -m tools.databricks.audit_global_m2m_access' in line
            or line.strip() == "-m tools.databricks.audit_global_m2m_access"
        ):
            continue
        block = [line]
        if line.strip() == "-m tools.databricks.audit_global_m2m_access":
            while block[-1].strip() != ")":
                block.append(audit_lines[index + len(block)])
        else:
            while block[-1].rstrip().endswith("\\"):
                block.append(audit_lines[index + len(block)])
        audit_invocations.append("\n".join(block))
    assert len(audit_invocations) == 7
    assert all("--expected-inventory-principal" in block for block in audit_invocations)
    m2m_block = text[text.index("run_as_m2m_identity() {") : text.index("# Step 0: preflight")]
    assert "env -i" not in m2m_block
    assert "compgen -e" in m2m_block
    assert 'export DATABRICKS_CLIENT_SECRET="$client_secret"' in m2m_block
    assert 'identity_env+=("DATABRICKS_CLIENT_SECRET=' not in m2m_block
    assert "MIP_DEPLOYER_DATABRICKS_TOKEN" not in m2m_block
    for helper in (
        "converge_campaign_treatment_access.py",
        "ensure_campaign_treatment_table.py",
        "stop_app_fail_closed.py",
    ):
        helper_text = (REPO / "tools" / "databricks" / helper).read_text(encoding="utf-8")
        assert "deployment_workspace_client()" in helper_text


def test_pii_salt_is_generated_in_a_secure_payload_and_never_logged() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    block = text[
        text.index('step "provision pii-salt secret scope') : text.index(
            'step "provision dedicated signed App rollback-contract secret scope"'
        )
    ]

    assert "secrets.token_hex(32)" in block
    assert "run_redacted" in block
    assert "@[secure-temp]" in block
    assert "--string-value" not in block
    assert "openssl rand" not in block


def test_retained_model_audit_is_read_only_and_rotation_action_is_absent() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    export = text.index(
        'step "export the exact live Gateway resource contract under runtime authority"'
    )
    read_only = text.index(
        'step "prove effective agent-runtime privilege boundary across every MIP securable"'
    )
    read_only_block = text[read_only : text.index("\n  step ", read_only + 1)]

    assert export < read_only
    assert "rotate-retained-model-attestations" not in text
    assert "MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING=1" not in read_only_block
    assert "--action" not in read_only_block


@pytest.mark.parametrize("mode", ("pat", "profile"))
def test_deploy_auth_handoff_is_single_workspace_and_child_isolated(
    tmp_path: Path,
    mode: str,
) -> None:
    logs = _run_deploy_auth_harness(tmp_path, mode=mode)
    deploy = logs["deploy"]
    mints = logs["mints"]
    m2m = logs["m2m"]
    runtime = logs["runtime"]
    runtime_authorized = logs["runtime_authorized"]
    expected_host = "https://reviewed-workspace.example"

    assert deploy["MIP_DATABRICKS_WORKSPACE_HOST"] == expected_host
    for credential in (
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_ADMIN_CLIENT_ID",
        "DATABRICKS_ADMIN_CLIENT_SECRET",
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
        "DATABRICKS_ACCOUNT_CLIENT_ID",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
    ):
        assert credential not in deploy
    if mode == "pat":
        assert deploy["DATABRICKS_HOST"] == expected_host
        assert deploy["DATABRICKS_TOKEN"] == "reviewed-deployer-pat"
        assert deploy["DATABRICKS_AUTH_TYPE"] == "pat"
        assert "DATABRICKS_CONFIG_PROFILE" not in deploy
    else:
        assert deploy["DATABRICKS_CONFIG_PROFILE"] == "REVIEWED"
        assert "DATABRICKS_HOST" not in deploy
        assert "DATABRICKS_TOKEN" not in deploy
        assert "DATABRICKS_AUTH_TYPE" not in deploy

    assert len(mints) == 2
    assert [mint["DATABRICKS_CLIENT_ID"] for mint in mints] == [
        "normal-app-client",
        "admin-app-client",
    ]
    for mint in mints:
        assert mint["DATABRICKS_HOST"] == expected_host
        assert mint["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
        assert "DATABRICKS_TOKEN" not in mint
        assert "DATABRICKS_CONFIG_PROFILE" not in mint
        assert "MIP_DEPLOYER_DATABRICKS_TOKEN" not in mint
        assert "MIP_DEPLOYER_DATABRICKS_PROFILE" not in mint
        assert "MIP_BEARER_TOKEN" not in mint
        assert "MIP_ADMIN_BEARER_TOKEN" not in mint
        assert "DATABRICKS_VERIFIER_CLIENT_SECRET" not in mint
        assert "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in mint

    assert m2m["DATABRICKS_HOST"] == expected_host
    assert m2m["DATABRICKS_CLIENT_ID"] == "verifier-client"
    assert m2m["DATABRICKS_CLIENT_SECRET"] == "verifier-secret"
    assert m2m["DATABRICKS_AUTH_TYPE"] == "oauth-m2m"
    assert "DATABRICKS_TOKEN" not in m2m
    assert "DATABRICKS_CONFIG_PROFILE" not in m2m
    assert "MIP_DEPLOYER_DATABRICKS_TOKEN" not in m2m
    assert "MIP_DEPLOYER_DATABRICKS_PROFILE" not in m2m
    assert "MIP_BEARER_TOKEN" not in m2m
    assert "MIP_ADMIN_BEARER_TOKEN" not in m2m
    assert "DATABRICKS_ADMIN_CLIENT_SECRET" not in m2m
    assert "DATABRICKS_ACCOUNT_CLIENT_SECRET" not in m2m
    assert m2m["MIP_AI_GATEWAY_PROOF_SIGNING_KEY"] == "proof-signing-secret"
    assert "MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING" not in m2m
    assert "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY" not in m2m
    assert "MIP_COTALITY_ID_MASK_SECRET" not in m2m
    assert "MIP_GENIE_ACTION_SECRET_CURRENT" not in m2m
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS" not in m2m
    assert "SALESFORCE_CLIENT_SECRET" not in m2m
    assert "SALESFORCE_PASSWORD" not in m2m
    assert "SALESFORCE_SECURITY_TOKEN" not in m2m

    assert runtime["DATABRICKS_CLIENT_ID"] == "runtime-client"
    assert runtime["DATABRICKS_CLIENT_SECRET"] == "runtime-secret"
    for secret in (
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        "MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING",
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY",
        "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
        "DATABRICKS_ADMIN_CLIENT_SECRET",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        "SALESFORCE_CLIENT_SECRET",
        "SALESFORCE_PASSWORD",
        "SALESFORCE_SECURITY_TOKEN",
    ):
        assert secret not in runtime

    assert runtime_authorized["DATABRICKS_CLIENT_ID"] == "runtime-client"
    assert runtime_authorized["DATABRICKS_CLIENT_SECRET"] == "runtime-secret"
    assert runtime_authorized["MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING"] == "1"
    assert (
        runtime_authorized["MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY"]
        == "model-signing-secret"
    )
    assert "MIP_AI_GATEWAY_PROOF_SIGNING_KEY" not in runtime_authorized


def test_deploy_refuses_mixed_ambient_pat_and_dotenv_host_before_network(
    tmp_path: Path,
) -> None:
    result, mutation_marker = _run_bind_harness(
        tmp_path,
        env_local="DATABRICKS_HOST=https://dotenv-workspace.example\n",
        bundle_host="https://ambient-workspace.example",
        ambient={
            "DATABRICKS_HOST": "https://ambient-workspace.example",
            "DATABRICKS_TOKEN": "ambient-pat",
        },
    )

    assert result.returncode == 2
    assert "refusing to combine an ambient PAT with a different .env.local host" in result.stderr
    assert not mutation_marker.exists()


def test_deploy_refuses_authenticated_host_outside_bundle_target_before_network(
    tmp_path: Path,
) -> None:
    result, mutation_marker = _run_bind_harness(
        tmp_path,
        env_local=(
            "DATABRICKS_HOST=https://reviewed-workspace.example\n" "DATABRICKS_TOKEN=reviewed-pat\n"
        ),
        bundle_host="https://different-target.example",
        ambient={},
    )

    assert result.returncode == 2
    assert (
        "authenticated workspace host does not match databricks.yml target 'dev'" in result.stderr
    )
    assert not mutation_marker.exists()


def test_deploy_dev_seeds_databricks_auth_without_printing_secrets() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    for secret in (
        "secrets.DATABRICKS_HOST",
        "secrets.DATABRICKS_TOKEN",
        "secrets.DATABRICKS_WAREHOUSE_ID",
        "secrets.GENIE_SPACE_ID",
    ):
        assert secret in text

    assert "$HOME/.databrickscfg" in text
    assert "auth_type = pat" in text
    assert 'chmod 600 "$HOME/.databrickscfg"' in text
    assert "chmod 600 .env.local" in text
    assert "cat .env.local" not in text
    assert 'echo "$DATABRICKS_TOKEN"' not in text
    assert 'echo "MIP_COTALITY_ID_MASK_SECRET=' not in text
    assert 'echo "MIP_GENIE_ACTION_SECRET_CURRENT=' not in text
    assert 'echo "MIP_GENIE_ACTION_SECRET_PREVIOUS=' not in text


def test_deploy_dev_binds_required_and_rotation_secrets() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    for binding in (
        "MIP_COTALITY_ID_MASK_SECRET: ${{ secrets.MIP_COTALITY_ID_MASK_SECRET }}",
        "MIP_GENIE_ACTION_SECRET_CURRENT: ${{ secrets.MIP_GENIE_ACTION_SECRET_CURRENT }}",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS: ${{ secrets.MIP_GENIE_ACTION_SECRET_PREVIOUS }}",
        "MIP_GENIE_ACTION_SECRET_KID: ${{ vars.MIP_GENIE_ACTION_SECRET_KID }}",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID: ${{ vars.MIP_GENIE_ACTION_SECRET_PREVIOUS_KID }}",
    ):
        assert binding in text
    assert "MIP_COTALITY_ID_MASK_SECRET MIP_GENIE_ACTION_SECRET_CURRENT" in text
    assert "MIP_GENIE_ACTION_SECRET_PREVIOUS_KID=${MIP_GENIE_ACTION_SECRET_PREVIOUS_KID}" in text


def test_deploy_dev_has_cost_and_permission_guards() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "permissions:" in text
    assert "contents: read" in text
    assert "concurrency:" in text
    assert "group: mip-dev-deploy" in text
    assert "cancel-in-progress: false" in text


def test_deploy_dev_requires_explicit_admin_rbac_and_mints_distinct_app_bearers() -> None:
    text = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "MIP_ADMIN_EMAILS: ${{ vars.MIP_ADMIN_EMAILS }}" in text
    assert "MIP_ADMIN_GROUP_NAME: ${{ vars.MIP_ADMIN_GROUP_NAME }}" in text
    assert "DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}" in text
    assert "DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}" in text
    assert "DATABRICKS_ADMIN_CLIENT_ID: ${{ secrets.DATABRICKS_ADMIN_CLIENT_ID }}" in text
    assert "DATABRICKS_ADMIN_CLIENT_SECRET: ${{ secrets.DATABRICKS_ADMIN_CLIENT_SECRET }}" in text
    assert "secrets.MIP_ADMIN_BEARER_TOKEN" not in text
    assert "Mint initial per-run app Bearers" not in text
    assert "--github-env MIP_BEARER_TOKEN" not in text
    assert "--github-env MIP_ADMIN_BEARER_TOKEN" not in text
    assert "DATABRICKS_OPERATOR2_CLIENT_SECRET" not in text
    assert "mint_m2m_token MIP_BEARER_TOKEN" in script
    assert "mint_m2m_token MIP_ADMIN_BEARER_TOKEN" in script
    assert (
        "Normal, operator2, admin, verifier, and agent-runtime M2M client IDs "
        "must be pairwise distinct." in text
    )
    assert (
        "MIP_APPROVER_IDENTITIES=${DATABRICKS_CLIENT_ID},${DATABRICKS_OPERATOR2_CLIENT_ID}" in text
    )
    assert "MIP_ADMIN_IDENTITIES=${DATABRICKS_ADMIN_CLIENT_ID}" in text
    assert "Configure MIP_ADMIN_EMAILS or MIP_ADMIN_GROUP_NAME" not in text


def test_deploy_uses_dedicated_verifier_for_gateway_proof_writes() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for secret in (
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
    ):
        assert f"{secret}: ${{{{ secrets.{secret} }}}}" in workflow
        assert secret in script
    assert "--identity-role verifier" in script
    assert '--expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in script
    assert '--warehouse-id "$_GRANTS_WAREHOUSE_ID"' in script
    assert "run_as_m2m_identity" in script
    assert "jobs/lakebase_migrate.py" in script
    assert 'export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="$DATABRICKS_VERIFIER_CLIENT_ID"' in script
    boundary = script.index(
        'step "prove verifier effective authorization boundary before exact Gateway proof"'
    )
    proof = script.index('step "verify AI Gateway exact inference-row proof')
    assert boundary < proof
    assert "-m tools.databricks.verify_verifier_identity_boundary" in script[boundary:proof]
    assert '--protected-service-principal-id "$APP_SP_SCIM_ID"' in script[boundary:proof]
    assert "DATABRICKS_ACCOUNT_ID: ${{ secrets.DATABRICKS_ACCOUNT_ID }}" in workflow


def test_deploy_uses_fifth_isolated_identity_for_agent_resource_ownership() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for secret in (
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
    ):
        assert f"{secret}: ${{{{ secrets.{secret} }}}}" in workflow
        assert secret in script
    runtime_block = script[
        script.index(
            'step "provision Supervisor and Gateway under the dedicated agent-runtime'
        ) : script.index(
            'step "reconcile runtime read-only and verifier-only Lakebase proof-ledger grants"'
        )
    ]
    assert "run_as_m2m_identity" in runtime_block
    assert "DATABRICKS_AGENT_RUNTIME_CLIENT_ID" in runtime_block
    assert "--skip-sync" in runtime_block
    assert "--skip-app-permissions" in runtime_block
    assert "tools.databricks.cutover_agent_runtime_supervisor prepare" in runtime_block
    exact_export = runtime_block.index("tools.databricks.export_gateway_runtime_contract")
    activate_offset = runtime_block.index(
        "activate App snapshot on the runtime-owned Gateway before retirement"
    )
    assert exact_export < activate_offset
    assert '--shell-env "$AGENTIC_ENV_FILE"' in runtime_block
    assert '--runtime-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"' in runtime_block
    assert "tools.databricks.cutover_agent_runtime_supervisor retire" in script
    assert "tools.databricks.cutover_agent_runtime_supervisor finalize" in script
    activate = script.index("activate App snapshot on the runtime-owned Gateway before retirement")
    retire = script.index(
        "retire pinned blue runtime resources only after every green release gate"
    )
    assert activate < retire
    assert "tools/verify_deployed_app_contract.py" in script[activate:retire]
    assert '--deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:' in script[activate:retire]
    assert "tools/verify_app_agent_green_path.py" in script[activate:retire]
    assert "tools.databricks.verify_hosted_agent_tool_execution" in script[activate:retire]
    assert script.index("read independent governed fn_build_cohort expectation") < retire
    assert script.index("tools.databricks.verify_ai_gateway_exact_proof") < retire
    assert script.index("run live Agent Evaluation") < retire
    assert script.index("FINAL_APP_PROVEN=1") < retire
    assert "tools.databricks.verify_agent_runtime_identity_boundary" in runtime_block
    assert runtime_block.count("tools.databricks.audit_global_m2m_access") >= 2
    assert "--expected-serving-permission CAN_MANAGE" in runtime_block
    assert "--expected-serving-permission CAN_QUERY" in runtime_block
    assert '--genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"' in runtime_block
    assert '--serving-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT"' in runtime_block
    assert '--serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"' in runtime_block
    final_audit = script.index(
        'step "re-audit final agent-runtime global access after blue retirement"'
    )
    assert retire < final_audit
    assert script.count("tools.databricks.audit_global_m2m_access") >= 4
    for function in ("fn_build_cohort", "fn_segment_counts", "fn_lead_queue_url"):
        assert (
            f"GRANT EXECUTE ON FUNCTION ${{_GRANTS_CATALOG}}.gold.{function} "
            "TO \\`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\\`" in script
        )
    assert "GRANT CREATE MODEL ON SCHEMA" in script
    assert "GRANT CREATE TABLE ON SCHEMA" in script
    assert "REVOKE CREATE MODEL ON SCHEMA" in script
    assert "REVOKE CREATE TABLE ON SCHEMA" in script
    assert "revoke_agent_runtime_bootstrap_grants" in script
    isolation_audit = script.index(
        'step "re-audit dedicated agent-runtime isolation before resource ownership"'
    )
    resource_provision = script.index(
        'step "provision Supervisor and Gateway under the dedicated agent-runtime identity"'
    )
    assert isolation_audit < resource_provision
    isolation_block = script[isolation_audit:resource_provision]
    assert "--identity-role agent_runtime" in isolation_block
    assert '--expected-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"' in isolation_block
    assert "--no-mint-secret" in isolation_block


def test_deploy_accepts_numeric_app_service_principal_ids() -> None:
    """The live Databricks Apps API emits service_principal_id as a number."""

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'str(json.load(sys.stdin).get("service_principal_id") or "").strip()' in script


def test_deploy_reconciles_every_app_facing_identity_only_after_app_creation() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    bundle_create = script.index('step "deploy full bundle for first App creation"')
    app_identity = script.index(
        'APP_SP_SCIM_ID="$(printf',
        bundle_create,
    )
    grants_start = script.index(
        'step "reconcile normal operator access to the deployed App"',
        app_identity,
    )
    verifier_start = script.index(
        'step "bootstrap dedicated AI Gateway verifier Lakebase OAuth role"',
        grants_start,
    )
    grant_block = script[grants_start:verifier_start]

    assert bundle_create < app_identity < grants_start < verifier_start
    for role, client_id in (
        ("normal", "DATABRICKS_CLIENT_ID"),
        ("operator2", "DATABRICKS_OPERATOR2_CLIENT_ID"),
        ("admin", "DATABRICKS_ADMIN_CLIENT_ID"),
    ):
        assert f"--identity-role {role}" in grant_block
        assert f'--expected-application-id "${client_id}"' in grant_block
    assert grant_block.count('--app-name "$_GRANTS_APP_NAME"') == 3
    assert grant_block.count("--no-mint-secret") == 3
    assert "--pre-app-bootstrap" not in grant_block


def test_gateway_proof_failure_only_blocks_strict_release_deploys() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    proof_step = script.index('step "verify AI Gateway exact inference-row proof')
    next_step = script.index("wait_for_app_deployable", proof_step)
    proof_block = script[proof_step:next_step]
    assert "--require-verifier-derived-auth" in proof_block
    assert '--warehouse-id "$_GRANTS_WAREHOUSE_ID"' in proof_block
    assert "AI_GATEWAY_PROOF_ARGS+=(--require-verified)" in proof_block
    assert "if ! run_as_m2m_identity \\" in proof_block
    assert "strict AI Gateway exact proof failed" in proof_block
    assert "exit 1" in proof_block
    assert "continuing with the capability honestly configured/unavailable" in proof_block
    assert "${YLW}" in proof_block
    assert "${YEL}" not in script


def test_gateway_grant_delivery_is_retryable_and_only_blocks_strict_release() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    workflow = NIGHTLY.read_text(encoding="utf-8")

    grant_start = script.index("AI_GATEWAY_GRANTS_READY=1")
    proof_start = script.index('step "verify AI Gateway exact inference-row proof', grant_start)
    grant_block = script[grant_start:proof_start]
    assert 'if ! run "$PYTHON" -m tools.databricks.grant_ai_gateway_inference_table' in grant_block
    assert "strict AI Gateway inference-table grant convergence failed" in grant_block
    assert "delivery/grants are pending" in grant_block
    assert "honestly configured/unavailable" in grant_block
    assert "Reconcile delayed AI Gateway inference-table grants" in workflow
    assert workflow.count("-m tools.databricks.grant_ai_gateway_inference_table") >= 1


def test_fresh_deploy_creates_verifier_lakebase_role_before_first_migration() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    absent_or_converged = script.index(
        'step "prove absent or converge governed treatment table before first App creation"'
    )
    quiesce = script.index(
        'step "quiesce app treatment writes immediately before treatment-table DDL"'
    )
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    role_bootstrap = script.index(
        'step "bootstrap dedicated AI Gateway verifier Lakebase OAuth role"'
    )
    first_migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    assert absent_or_converged < bundle_apply < role_bootstrap < first_migration < quiesce
    first_install_block = script[absent_or_converged:bundle_apply]
    assert "tools.databricks.ensure_campaign_treatment_table" in first_install_block
    assert "--allow-absent" in first_install_block
    bootstrap_block = script[role_bootstrap:first_migration]
    assert "-m tools.databricks.provision_m2m_oauth" in bootstrap_block
    assert "--identity-role verifier" in bootstrap_block
    assert '--expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in bootstrap_block
    assert "--no-mint-secret" in bootstrap_block
    assert "--gateway-endpoint" not in bootstrap_block
    assert "--warehouse-id" not in bootstrap_block


def test_fresh_deploy_creates_governed_uc_tables_before_table_grants() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    bundle = (REPO / "databricks.yml").read_text(encoding="utf-8")

    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    post_bundle_quiesce = script.index(
        'step "quiesce app treatment writes immediately before treatment-table DDL"'
    )
    uc_init = script.index(
        'run_job_with_retry databricks bundle run mip_init_catalog_schemas -t "$TARGET"'
    )
    constraint_convergence = script.index(
        "tools.databricks.ensure_campaign_treatment_table", uc_init
    )
    table_grants = script.index('step "apply UC grants to the app service principal')
    skip_silver_branch = script.index('if [[ "$SKIP_SILVER" -eq 1 ]]')

    assert (
        bundle_apply
        < migration
        < post_bundle_quiesce
        < uc_init
        < constraint_convergence
        < table_grants
        < skip_silver_branch
    )
    assert script.count("--mode quiesce") >= 4
    assert "quiesce_app_treatment_after_failed_stop" in script
    assert (
        'step "keep existing App treatment writes quiesced through non-App release work"' in script
    )
    assert 'step "quiesce app treatment writes immediately before treatment-table DDL"' in script
    assert (
        'step "keep treatment writes quiesced until the green App is proven and captured"' in script
    )
    first_green_capture = script.index('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"')
    atomic_restore_and_capture = script.index(
        'step "atomically restore treatment authority and persist the last-good App contract"'
    )
    assert atomic_restore_and_capture < first_green_capture
    assert "mip_init_catalog_schemas:" in bundle
    assert "path: sql/_rendered/ddl/001_catalogs_schemas.sql" in bundle


def test_first_install_dry_run_does_not_require_a_live_app_identity() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    resolve_start = script.index('if [[ "$DRY_RUN" -eq 0 ]]; then\n  APP_RESOURCE_JSON=')
    quiesce = script.index(
        'step "quiesce app treatment writes immediately before treatment-table DDL"'
    )
    resolve_block = script[resolve_start:quiesce]
    assert 'databricks apps get "$_GRANTS_APP_NAME"' in resolve_block
    assert 'APP_SP_CLIENT_ID="dry-run-app-client-id"' in resolve_block
    assert 'APP_SP_SCIM_ID="dry-run-app-scim-id"' in resolve_block


def test_first_install_dry_run_executes_no_databricks_operations(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--local", str(REPO), str(checkout)],
        check=True,
    )
    deploy_copy = checkout / "scripts" / "deploy.sh"
    deploy_copy.write_text(DEPLOY_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    payload_copy = checkout / "tools" / "databricks" / "app_deploy_payload.py"
    payload_copy.write_text(
        (REPO / "tools" / "databricks" / "app_deploy_payload.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "scripts/deploy.sh", "tools/databricks/app_deploy_payload.py"],
        cwd=checkout,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Dry Run Contract",
            "-c",
            "user.email=dry-run@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "test current dry-run contract",
        ],
        cwd=checkout,
        check=True,
    )
    (checkout / ".venv").symlink_to(REPO / ".venv", target_is_directory=True)
    with (checkout / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write(".venv\n")
    (checkout / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=warehouse-1\n"
        "GENIE_SPACE_ID=space-1\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "databricks.log"
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text(
        "#!/usr/bin/env bash\n"
        'printf \'%s\\n\' "$*" >> "$DATABRICKS_STUB_LOG"\n'
        'if [[ "$1" == "--version" ]]; then\n'
        "  echo 'Databricks CLI v-test'\n"
        "  exit 0\n"
        "fi\n"
        'echo "unexpected Databricks dry-run invocation: $*" >&2\n'
        "exit 97\n",
        encoding="utf-8",
    )
    fake_databricks.chmod(0o755)
    env = {
        **{
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("COV_CORE", "COVERAGE"))
        },
        "APP_ENV": "local",
        "DATABRICKS_STUB_LOG": str(log),
        "GENIE_SPACE_ID": "space-1",
        "MIP_ADMIN_EMAILS": "operator@example.invalid",
        "MIP_COTALITY_ID_MASK_SECRET": "stable-local-mask-secret",
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-local-action-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }

    result = subprocess.run(
        [
            "bash",
            "scripts/deploy.sh",
            "-t",
            "dev",
            "--dry-run",
            "--no-confirm",
            "--skip-silver",
            "--skip-smoke",
        ],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["--version"]
    assert "would grant: GRANT USE CATALOG" in result.stdout
    assert "would verify: campaign treatment table append-only" in result.stdout
    assert "would inspect/create: scope mip and write-once pii-salt-v1" in result.stdout
    assert "deploy Databricks App snapshot with Agent Evaluation proof" in result.stdout


def test_deploy_dev_wires_separate_required_gateway_signing_keys() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    secret_binding = (
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY: " "${{ secrets.MIP_AI_GATEWAY_PROOF_SIGNING_KEY }}"
    )
    assert workflow.count(secret_binding) == 2
    model_binding = (
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY: "
        "${{ secrets.MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY }}"
    )
    assert workflow.count(model_binding) == 2
    required_loop = workflow[workflow.index('missing=""') : workflow.index("python - <<'PY'")]
    assert "MIP_GENIE_ACTION_SECRET_CURRENT" in required_loop
    assert "MIP_AI_GATEWAY_PROOF_SIGNING_KEY" in required_loop
    assert "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY" in required_loop
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "model-attestation and verifier-proof keys must be distinct" in script
    assert "MIP_ALLOW_RUNTIME_MODEL_ATTESTATION_SIGNING" in script


def test_deploy_holds_signed_workspace_lease_through_durable_capture() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    acquire = script.index("tools.databricks.app_deployment_lease acquire")
    heartbeat = script.index("tools.databricks.app_deployment_lease heartbeat")
    first_bundle = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    stale_grant_cleanup = script.index("could not clear prior agent-runtime bootstrap privileges")
    capture = script.index('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"')
    release = script.index("tools.databricks.app_deployment_lease release")
    assert release < acquire < heartbeat < stale_grant_cleanup < first_bundle < capture
    assert "APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" in script
    assert 'kill -0 "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID"' in script
    assert '--deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:' in script
    assert "MIP_APP_DEPLOYMENT_LEASE_ID" in (
        REPO / "tools/databricks/app_deploy_payload.py"
    ).read_text(encoding="utf-8")
    assert '--bundle-summary "${APP_BUNDLE_SUMMARY:' in script


def test_proof_signing_heartbeat_is_direct_child_and_renews(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "heartbeat_probe.py"
    result_file = tmp_path / "heartbeat.json"
    pid_file = tmp_path / "heartbeat.pid"
    probe.write_text(
        textwrap.dedent(
            """
            import json
            import os
            import sys
            from pathlib import Path

            from tools.databricks import app_deployment_lease as lease

            expected_parent = int(sys.argv[1])
            output = Path(sys.argv[2])
            real_parent_check = lease._parent_is_expected
            checks = []
            renewals = []

            def bounded_parent_check(parent_pid):
                actual = real_parent_check(parent_pid)
                checks.append(actual)
                return actual if len(checks) <= 2 else False

            lease._parent_is_expected = bounded_parent_check
            lease.time.sleep = lambda _seconds: None
            lease.renew = lambda *_args, **kwargs: renewals.append(kwargs)
            lease._heartbeat(
                object(),
                app_name="mip-app",
                lease_id="lease-id",
                source_git_sha="a" * 40,
                parent_pid=expected_parent,
            )
            output.write_text(
                json.dumps(
                    {
                        "argv": sys.argv,
                        "checks": checks,
                        "expected_parent": expected_parent,
                        "pid": os.getpid(),
                        "ppid": os.getppid(),
                        "proof_key": os.environ.get(
                            "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"
                        ),
                        "renewals": renewals,
                    }
                ),
                encoding="utf-8",
            )
            """
        ),
        encoding="utf-8",
    )
    python = shlex.quote(sys.executable)
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        DRY_RUN=0
        DIM=''
        RED=''
        RST=''
        APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=''
        MIP_AI_GATEWAY_PROOF_SIGNING_KEY='scoped-proof-key'
        export -n MIP_AI_GATEWAY_PROOF_SIGNING_KEY
        {_proof_heartbeat_launcher_block()}
        deployer_pid=$$
        start_proof_signing_heartbeat \
          {python} {shlex.quote(str(probe))} "$deployer_pid" \
          {shlex.quote(str(result_file))}
        printf '%s\n' "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID" > \
          {shlex.quote(str(pid_file))}
        wait "$APP_DEPLOYMENT_LEASE_HEARTBEAT_PID"
        {python} -c 'import os; raise SystemExit(
            "MIP_AI_GATEWAY_PROOF_SIGNING_KEY" in os.environ
        )'
        """
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"
    }
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPO), env.get("PYTHONPATH", "")) if value
    )

    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert payload["pid"] == int(pid_file.read_text(encoding="utf-8"))
    assert payload["ppid"] == payload["expected_parent"]
    assert payload["checks"] == [True, True, True]
    assert len(payload["renewals"]) == 1
    assert payload["proof_key"] == "scoped-proof-key"
    assert "scoped-proof-key" not in payload["argv"]


def test_nightly_exporters_receive_only_model_attestation_public_keys() -> None:
    workflow = NIGHTLY.read_text(encoding="utf-8")

    assert "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY" not in workflow
    assert workflow.count("vars.MIP_GATEWAY_MODEL_ATTESTATION_VERIFY_KEY") == 2
    for marker in (
        "- name: Resolve source-bound Gateway runtime contract",
        "python -m tools.databricks.export_gateway_runtime_contract",
    ):
        assert marker in workflow


def test_deploy_unexports_private_signing_keys_before_first_child_process() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    prefix = script[: script.index('REPO_ROOT="$(cd')]

    assert (
        "export -n MIP_AI_GATEWAY_PROOF_SIGNING_KEY "
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY" in prefix
    )
    for secret in (
        "DATABRICKS_CLIENT_SECRET",
        "DATABRICKS_ADMIN_CLIENT_SECRET",
        "DATABRICKS_VERIFIER_CLIENT_SECRET",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
    ):
        assert secret in prefix


def test_deploy_workflow_identity_check_inherits_only_public_client_ids() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    step_start = workflow.index("- name: Configure Databricks dev credentials")
    python_pos = workflow.index("python - <<'PY'", step_start)
    prefix = workflow[step_start:python_pos]
    python_end = workflow.index("          PY", python_pos)
    suffix = workflow[python_end : workflow.index("- name: Deploy dev Databricks App")]

    for secret in (
        "DATABRICKS_TOKEN",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY",
    ):
        assert f"export -n {secret}" in prefix or secret in next(
            line
            for line in prefix.splitlines()
            if line.lstrip().startswith("export -n ") and secret in line
        )
    for client_id in (
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_OPERATOR2_CLIENT_ID",
        "DATABRICKS_ADMIN_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_ACCOUNT_CLIENT_ID",
    ):
        assert client_id in workflow[python_pos:python_end]
        assert client_id in suffix


def test_deploy_workflow_executes_identity_check_without_secret_inheritance(
    tmp_path: Path,
) -> None:
    bin_dir, log = _install_environment_recorder(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    required = {
        "DATABRICKS_HOST": "https://workspace.example",
        "DATABRICKS_TOKEN": "deployer-pat",
        "DATABRICKS_WAREHOUSE_ID": "warehouse-id",
        "GENIE_SPACE_ID": "genie-id",
        "MIP_COTALITY_ID_MASK_SECRET": "mask-secret",
        "MIP_GENIE_ACTION_SECRET_CURRENT": "action-secret",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS": "old-action-secret",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": "proof-private",
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY": "model-private",
        "MIP_DEFAULT_CATALOG": "mip",
        "DATABRICKS_CLIENT_ID": "operator-a",
        "DATABRICKS_OPERATOR2_CLIENT_ID": "operator-b",
        "DATABRICKS_ADMIN_CLIENT_ID": "admin",
        "DATABRICKS_VERIFIER_CLIENT_ID": "verifier",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "runtime",
        "DATABRICKS_ACCOUNT_HOST": "https://accounts.example",
        "DATABRICKS_ACCOUNT_ID": "account-id",
        "DATABRICKS_ACCOUNT_CLIENT_ID": "account-client",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET": "account-secret",
    }
    result = subprocess.run(
        ["bash", "-c", _workflow_run_block(DEPLOY_DEV, "Configure Databricks dev credentials")],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(home),
            "CHILD_ENV_LOG": str(log),
            **required,
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    child = _read_env_log(log)
    for client_id in (
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_OPERATOR2_CLIENT_ID",
        "DATABRICKS_ADMIN_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_ACCOUNT_CLIENT_ID",
    ):
        assert child[client_id] == required[client_id]
    for secret in (
        "DATABRICKS_TOKEN",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY",
    ):
        assert secret not in child


def test_deploy_dev_wires_optional_approved_uc_owner_contract() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "rebase_unverified_app:" in workflow
    assert (
        "MIP_REBASE_UNVERIFIED_APP: ${{ inputs.rebase_unverified_app && '1' || '0' }}" in workflow
    )
    assert "MIP_REQUIRE_AI_GATEWAY_CLAIMABLE: '1'" in workflow
    assert workflow.count("MIP_DEFAULT_CATALOG: ${{ vars.MIP_DEFAULT_CATALOG || 'mip' }}") == 2
    for name in (
        "MIP_AI_GATEWAY_AGENT_MODEL_FAMILY",
        "MIP_AI_GATEWAY_AGENT_EXPERIMENT_BASE",
        "MIP_AI_GATEWAY_TABLE_PREFIX",
    ):
        assert workflow.count(f"{name}: ${{{{ vars.{name} }}}}") == 2
        assert f'echo "{name}=${{{name}}}"' in workflow

    binding = "MIP_UC_APPROVED_OWNER_PRINCIPALS: " "${{ vars.MIP_UC_APPROVED_OWNER_PRINCIPALS }}"
    assert workflow.count(binding) == 2
    assert "MIP_UC_APPROVED_OWNER_PRINCIPALS=${MIP_UC_APPROVED_OWNER_PRINCIPALS}" in workflow
    assert "DATABRICKS_ACCOUNT_CLIENT_ID: ${{ secrets.DATABRICKS_ACCOUNT_CLIENT_ID }}" in workflow
    assert (
        "DATABRICKS_ACCOUNT_CLIENT_SECRET: " "${{ secrets.DATABRICKS_ACCOUNT_CLIENT_SECRET }}"
    ) in workflow
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "dotenv_value MIP_UC_APPROVED_OWNER_PRINCIPALS" in script
    assert 'resolve_m2m_credential "$_ACCOUNT_AUTH_NAME"' in script
    assert "account-SCIM OAuth client must be distinct from" in script
    assert script.index("existing target App service principal") < script.index(
        'step "keep existing App treatment writes quiesced through non-App release work"'
    )
    assert "Account-SCIM OAuth client must be distinct from every app-facing" in workflow
    assert (
        "DATABRICKS_ACCOUNT_CLIENT_ID"
        in workflow[
            workflow.index("Configure Databricks dev credentials") : workflow.index(
                "Deploy dev Databricks App"
            )
        ]
    )
    app_yaml = (REPO / "app.yaml").read_text(encoding="utf-8")
    assert "MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED" in app_yaml
    assert 'value: "0"' in app_yaml
    assert "--enable-campaign-treatment-runtime" in script
    assert (
        '--gateway-agent-model "${MIP_AI_GATEWAY_AGENT_MODEL_FAMILY:-${MIP_DEFAULT_CATALOG:-mip}.audit.mortgage_growth_supervisor_proxy}"'
        in script
    )
    assert (
        '--gateway-table-prefix "${MIP_AI_GATEWAY_TABLE_PREFIX:-mip_agent_gateway_growth_agent}"'
        in script
    )
    assert (
        '--gateway-model-family "${MIP_AI_GATEWAY_AGENT_MODEL_FAMILY:-${MIP_DEFAULT_CATALOG:-mip}.audit.mortgage_growth_supervisor_proxy}"'
        in script
    )
    assert "export MIP_CAMPAIGN_TREATMENT_RUNTIME_ENABLED=1" not in script
    first_snapshot = script.index(
        'deploy_app_snapshot "deploy first-install Databricks App snapshot from uploaded bundle source"'
    )
    atomic_restore_and_capture = script.index(
        "atomically restore treatment authority and persist the last-good App contract"
    )
    constraint_convergence = script.index("tools.databricks.ensure_campaign_treatment_table")
    assert constraint_convergence < first_snapshot < atomic_restore_and_capture
    preserve_old = script.index(
        'step "preserve prior App source and runtime binding until green activation"'
    )
    activate_green = script.index(
        'deploy_app_snapshot "activate App snapshot on the runtime-owned Gateway before retirement"'
    )
    assert first_snapshot < preserve_old < activate_green
    assert 'if [[ -z "${_EXISTING_APP_SP_CLIENT_ID:-}" ]]; then' in script
    assert 'kind != "apps"' in script
    assert 'BUNDLE_NON_APP_ARGS+=(--select "$_bundle_selector")' in script
    assert script.index('APP_UPGRADE_STATE="blue_active"') < preserve_old
    rollback_ensure = script.index("tools.databricks.app_deployment_rollback ensure")
    blue_quiescing = script.index('APP_UPGRADE_STATE="blue_quiescing"')
    activating = script.index('APP_UPGRADE_STATE="green_activating_quiesced"')
    green_capture = script.index('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"')
    green_treatment_restoring = script.index(
        'APP_UPGRADE_STATE="green_treatment_pending_capture"', activate_green
    )
    green_verified = script.index('APP_UPGRADE_STATE="green_verified"', green_capture)
    retire_old = script.index(
        'step "retire pinned blue runtime resources only after every green release gate"'
    )
    assert rollback_ensure < preserve_old < blue_quiescing < activating < activate_green
    assert activate_green < green_treatment_restoring < atomic_restore_and_capture < green_capture
    assert green_capture < retire_old < green_verified
    assert "app_deployment_rollback restore" in script
    assert "persist the last-good App contract" in script
    assert "preserving the verified blue App" in script
    assert "preserving the already-verified green App" in script
    bundle_index = script.index('run "$PYTHON" -m tools.databricks.bundle_env deploy -t "$TARGET"')
    fail_closed_arm = script.index("APP_FAIL_CLOSED_ARMED=1")
    first_treatment_proof = script.index(
        'step "quiesce app treatment writes immediately before treatment-table DDL"'
    )
    assert fail_closed_arm < bundle_index < first_treatment_proof
    assert "stop_app_after_failed_deploy" in script
    assert "tools.databricks.stop_app_fail_closed" in script
    assert "quiesce_app_treatment_after_failed_stop" in script
    assert "--mode quiesce" in script
    assert "original failure was followed by unproven App shutdown" in script
    assert "exit 90" in script
    final_proven = script.index("FINAL_APP_PROVEN=0")
    smoke_success = script.index("FINAL_APP_PROVEN=1", final_proven)
    final_capture = script.index(
        'capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"', smoke_success
    )
    absent_proof_restore = script.index(
        "restore the signed last-good App because final smoke proof is absent",
        final_capture,
    )
    assert final_proven < smoke_success < final_capture < absent_proof_restore
    assert 'if [[ "$DRY_RUN" -eq 0 && "$FINAL_APP_PROVEN" -eq 1 ]]' in script
    assert script.index("APP_FAIL_CLOSED_ARMED=0", bundle_index) > script.index(
        'deploy_app_snapshot "deploy Databricks App snapshot with Agent Evaluation proof"'
    )


def test_playwright_job_wires_genie_id_into_runtime_contract_export() -> None:
    workflow = NIGHTLY.read_text(encoding="utf-8")
    job = workflow[
        workflow.index("  playwright-e2e-live:") : workflow.index(
            "  kill-drill-simulated:",
            workflow.index("  playwright-e2e-live:"),
        )
    ]
    export_step = job[
        job.index("- name: Resolve source-bound Gateway runtime contract") : job.index(
            "- name: Resolve deployed app URL"
        )
    ]

    assert "GENIE_SPACE_ID: ${{ secrets.GENIE_SPACE_ID }}" in export_step


@pytest.mark.parametrize(
    ("original_rc", "stop_result", "quiesce_result", "expected_rc", "expected_log"),
    (
        (0, 1, 1, 0, ""),
        (7, 0, 1, 7, "stop\n"),
        (7, 1, 0, 90, "stop\nquiesce\n"),
        (7, 1, 1, 90, "stop\nquiesce\n"),
    ),
)
def test_deploy_exit_trap_preserves_failure_or_fails_closed(
    tmp_path: Path,
    original_rc: int,
    stop_result: int,
    quiesce_result: int,
    expected_rc: int,
    expected_log: str,
) -> None:
    result, compensation_log = _run_deploy_exit_trap_harness(
        tmp_path,
        original_rc=original_rc,
        stop_result=stop_result,
        quiesce_result=quiesce_result,
    )

    assert result.returncode == expected_rc, result.stderr
    actual_log = compensation_log.read_text(encoding="utf-8") if compensation_log.exists() else ""
    assert actual_log == expected_log
    if expected_rc == 90:
        assert "original failure was followed by unproven App shutdown" in result.stderr


@pytest.mark.parametrize("state", ("blue_active", "green_verified"))
def test_app_failure_compensation_preserves_proven_deployment(
    tmp_path: Path,
    state: str,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state=state,
        rollback_result=1,
        stop_result=1,
    )

    assert result.returncode == 0, result.stderr
    assert calls == ""


def test_app_failure_compensation_restores_blue_during_green_activation(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="green_activating_quiesced",
        rollback_result=0,
        stop_result=1,
    )

    assert result.returncode == 0, result.stderr
    assert "app_deployment_rollback restore" in calls
    assert "stop_app_fail_closed" not in calls


def test_app_failure_compensation_stops_when_exact_restore_fails(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="green_activating_quiesced",
        rollback_result=1,
        stop_result=0,
    )

    assert result.returncode == 0, result.stderr
    assert calls.index("app_deployment_rollback restore") < calls.index("stop_app_fail_closed")


def test_app_failure_compensation_stops_green_with_unproven_treatment_restore(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="green_treatment_pending_capture",
        rollback_result=0,
        stop_result=0,
    )

    assert result.returncode == 0, result.stderr
    assert calls.index("stop_app_fail_closed") < calls.index("app_deployment_rollback restore")
    assert "stop_app_fail_closed" in calls


def test_deploy_dev_wires_optional_salesforce_external_id_upsert_without_preflight() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    for binding in (
        "SALESFORCE_EXTERNAL_ID_FIELD: ${{ vars.SALESFORCE_EXTERNAL_ID_FIELD }}",
        "SALESFORCE_CLIENT_SECRET: ${{ secrets.SALESFORCE_CLIENT_SECRET }}",
        "SALESFORCE_PASSWORD: ${{ secrets.SALESFORCE_PASSWORD }}",
    ):
        assert binding in workflow
    required_preflight = workflow[
        workflow.index('missing=""') : workflow.index('if [ -n "$missing" ]')
    ]
    assert "SALESFORCE" not in required_preflight


def test_deploy_script_requires_cotality_mask_secret_for_non_dev_targets(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'APP_RUNTIME_ENV="${APP_ENV:-}"' in text
    assert "MIP_COTALITY_ID_MASK_SECRET is required for target" in text
    assert "source-known compatibility namespace is allowed only for local/test" in text
    assert text.index("tools.databricks.provision_runtime_secrets") < text.index(
        "tools.databricks.bundle_env validate"
    )
    assert 'RUNTIME_SECRET_SCOPE="${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"' in text
    assert 'export BUNDLE_VAR_runtime_secret_scope="$RUNTIME_SECRET_SCOPE"' in text
    assert '--scope "$RUNTIME_SECRET_SCOPE"' in text
    assert "Apps deploy payload\n# carries only value_from resource names" in text

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n" "DATABRICKS_WAREHOUSE_ID=abc123\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("MIP_COTALITY_ID_MASK_SECRET", None)
    env.pop("MIP_GENIE_ACTION_SECRET", None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "customer", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'customer' (APP_ENV=customer)"
        in result.stderr
    )
    assert "step 1: preflight" in result.stdout
    assert "step 2:" not in result.stdout


def test_deploy_script_rejects_placeholder_current_action_secret_for_sandbox(
    tmp_path: Path,
) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n"
        "MIP_COTALITY_ID_MASK_SECRET=stable-sandbox-mask-secret\n"
        "MIP_GENIE_ACTION_SECRET=legacy-does-not-count\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "REDACTED",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    for name in ("APP_ENV", "MIP_GENIE_ACTION_SECRET"):
        env.pop(name, None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "dev", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_GENIE_ACTION_SECRET_CURRENT is required for target 'dev' (APP_ENV=sandbox)"
        in result.stderr
    )
    assert "step 2:" not in result.stdout


def test_deploy_script_requires_cotality_mask_secret_for_customer_runtime_env(
    tmp_path: Path,
) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n" "DATABRICKS_WAREHOUSE_ID=abc123\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "APP_ENV": "customer",
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("MIP_COTALITY_ID_MASK_SECRET", None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "dev", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'dev' (APP_ENV=customer)"
        in result.stderr
    )
    assert "step 2:" not in result.stdout


def test_deploy_script_rejects_legacy_genie_secret_as_mask_secret(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n"
        "MIP_GENIE_ACTION_SECRET=legacy-does-not-count\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("MIP_COTALITY_ID_MASK_SECRET", None)
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "customer", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'customer' (APP_ENV=customer)"
        in result.stderr
    )
    assert "cotality id-mask secret: configured" not in result.stdout
    assert "step 2:" not in result.stdout


def test_deploy_script_rejects_placeholder_cotality_mask_secret(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    bin_dir = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    bin_dir.mkdir()
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    (repo / ".env.local").write_text(
        "DATABRICKS_HOST=https://example.cloud.databricks.com\n"
        "DATABRICKS_WAREHOUSE_ID=abc123\n"
        "MIP_COTALITY_ID_MASK_SECRET=REDACTED\n",
        encoding="utf-8",
    )
    _commit_deploy_fixture(repo)
    fake_databricks = bin_dir / "databricks"
    fake_databricks.write_text("#!/usr/bin/env bash\necho databricks fake\n", encoding="utf-8")
    fake_databricks.chmod(0o755)

    env = {
        **os.environ,
        "MIP_GENIE_ACTION_SECRET_CURRENT": "stable-customer-campaign-secret",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ["bash", str(deploy_copy), "-t", "customer", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'customer' (APP_ENV=customer)"
        in result.stderr
    )
    assert "cotality id-mask secret: configured" not in result.stdout
    assert "step 2:" not in result.stdout


def test_exact_source_gate_allows_standard_ignored_artifacts(tmp_path: Path) -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'APP_GIT_SHA="$SOURCE_GIT_SHA"' in text
    assert text.count("verify_exact_deploy_source") >= 3
    assert text.rindex("verify_exact_deploy_source") < text.index(
        'tools.databricks.bundle_env deploy -t "$TARGET"'
    )
    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    script_dir.mkdir(parents=True)
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(text, encoding="utf-8")
    deploy_copy.chmod(0o755)
    _commit_deploy_fixture(repo)
    (repo / ".env.local").write_text("local-only=true\n", encoding="utf-8")
    (repo / "frontend" / "dist").mkdir(parents=True)
    (repo / "frontend" / "dist" / "index.html").write_text("built", encoding="utf-8")
    (repo / ".databricks").mkdir()
    (repo / ".databricks" / "cache.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(deploy_copy), "--verify-source-only"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "exact source:" in result.stdout
    assert "tracked and untracked source clean" in result.stdout


def test_release_automation_never_executes_package_helpers_by_file_path() -> None:
    direct_entrypoint = re.compile(
        r"tools/(?:databricks/[A-Za-z0-9_]+|sync_lifecycle_warehouse)\.py"
    )
    offenders: list[str] = []
    for path in (DEPLOY_SCRIPT, NIGHTLY):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if direct_entrypoint.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")

    assert offenders == []


@pytest.mark.parametrize(
    "entrypoint",
    [
        "tools/databricks/export_gateway_runtime_contract.py",
        "tools/databricks/grant_ai_gateway_inference_table.py",
        "tools/databricks/provision_agentic_resources.py",
    ],
)
def test_package_dependent_databricks_entrypoints_support_direct_help(
    entrypoint: str,
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / entrypoint), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_exact_source_gate_rejects_dirty_uploaded_source(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    repo = tmp_path / "repo"
    script_dir = repo / "scripts"
    script_dir.mkdir(parents=True)
    deploy_copy = script_dir / "deploy.sh"
    deploy_copy.write_text(DEPLOY_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    deploy_copy.chmod(0o755)
    _commit_deploy_fixture(repo)
    expected_path = "scripts/deploy.sh" if dirty_kind == "tracked" else "unreviewed.py"
    if dirty_kind == "tracked":
        deploy_copy.write_text(
            deploy_copy.read_text(encoding="utf-8") + "\n# dirty source\n",
            encoding="utf-8",
        )
    else:
        (repo / expected_path).write_text("print('not committed')\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(deploy_copy), "--verify-source-only"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "refusing deployment from dirty source" in result.stderr
    assert expected_path in result.stderr
