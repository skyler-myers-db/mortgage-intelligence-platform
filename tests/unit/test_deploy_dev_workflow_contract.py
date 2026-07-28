"""Contracts for the manual dev deployment workflow."""

from __future__ import annotations

import base64
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
import yaml

from backend.services.ai_gateway_proof_attestation import (
    derive_gateway_proof_verify_key,
)
from backend.services.databricks_jobs import MANAGED_JOBS
from tools.databricks import lakebase_oauth_role_bootstrap_admission as admission
from tools.databricks import lakebase_oauth_role_bootstrap_orchestration as orchestration

REPO = Path(__file__).resolve().parents[2]
DEPLOY_DEV = REPO / ".github" / "workflows" / "deploy-dev.yml"
NIGHTLY = REPO / ".github" / "workflows" / "nightly.yml"
DEPLOY_SCRIPT = REPO / "scripts" / "deploy.sh"
DEPLOY_LIB_SCRIPTS = (
    REPO / "scripts" / "lib" / "deploy_agent_proxy_lifecycle.sh",
    REPO / "scripts" / "lib" / "deploy_verifier_gateway_lifecycle.sh",
    REPO / "scripts" / "lib" / "deploy_cutover_journal_lifecycle.sh",
    REPO / "scripts" / "lib" / "deploy_supervisor_creation_lifecycle.sh",
)
BUNDLE_CONFIG = REPO / "databricks.yml"


@pytest.mark.parametrize("target", ("ci", "unknown", "staging"))
def test_deploy_script_rejects_nonmutable_target_before_preflight(target: str) -> None:
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--target", target, "--dry-run"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "target must be exactly dev or prod" in result.stderr
    assert "step 1" not in result.stdout


@pytest.mark.parametrize(
    "targets",
    (("-t", "dev", "--target", "prod"), ("--target=dev", "--target=prod")),
)
def test_deploy_script_rejects_duplicate_target_before_preflight(
    targets: tuple[str, ...],
) -> None:
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), *targets, "--dry-run"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "target may be supplied only once" in result.stderr
    assert "step 1" not in result.stdout


def test_live_workflows_pin_cli_with_safe_partial_app_deploy_support() -> None:
    setup_action = "databricks/setup-cli@bc7e6aabb6006d8d1758bd25ee1a100935c9cb7c"

    for workflow_path in (DEPLOY_DEV, NIGHTLY):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        setup_steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).partition("@")[0].casefold() == "databricks/setup-cli"
        ]
        assert len(setup_steps) == 1
        assert setup_steps[0]["uses"] == setup_action
        assert not setup_steps[0].get("with")

    bundle = yaml.safe_load(BUNDLE_CONFIG.read_text(encoding="utf-8"))
    assert bundle["bundle"]["databricks_cli_version"] == ">= 1.7.0"


def _workflow_run_block(path: Path, step_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    step = text.index(f"- name: {step_name}")
    run = text.index("        run: |", step)
    end = text.find("\n      - name:", run + 1)
    return textwrap.dedent(text[run + len("        run: |\n") : end if end != -1 else None])


def _workflow_bundle_run_targets(workflow: dict[str, Any]) -> set[str]:
    """Return literal Databricks bundle job targets from parsed workflow scripts."""

    targets: set[str] = set()
    for job in workflow["jobs"].values():
        for workflow_step in job.get("steps", []):
            run_block = workflow_step.get("run")
            if not isinstance(run_block, str):
                continue
            normalized = re.sub(r"\\\r?\n[ \t]*", " ", run_block)
            for line in normalized.splitlines():
                if not re.search(r"\bdatabricks\s+bundle\s+run\b", line):
                    continue
                lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
                lexer.whitespace_split = True
                lexer.commenters = "#"
                tokens = list(lexer)
                for index in range(len(tokens) - 3):
                    if tokens[index : index + 3] != ["databricks", "bundle", "run"]:
                        continue
                    target = tokens[index + 3]
                    assert re.fullmatch(
                        r"[A-Za-z0-9_]+", target
                    ), "nightly bundle targets must be literal governed job names"
                    targets.add(target)
    return targets


def _sql_without_comments(sql: str) -> str:
    """Remove SQL comments so governance tests only accept executable DDL."""

    output: list[str] = []
    index = 0
    state = "code"
    delimiters = {"single": "'", "double": '"', "backtick": "`"}
    while index < len(sql):
        current = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if state == "code":
            if current == "-" and following == "-":
                state = "line_comment"
                index += 2
                continue
            if current == "/" and following == "*":
                state = "block_comment"
                index += 2
                continue
            if current in {"'", '"', "`"}:
                state = {"'": "single", '"': "double", "`": "backtick"}[current]
            output.append(current)
            index += 1
            continue
        if state == "line_comment":
            if current == "\n":
                output.append(current)
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if current == "*" and following == "/":
                state = "code"
                index += 2
                continue
            if current == "\n":
                output.append(current)
            index += 1
            continue

        delimiter = delimiters[state]
        output.append(current)
        if current == delimiter:
            if following == delimiter:
                output.append(following)
                index += 2
                continue
            state = "code"
        index += 1
    return "".join(output)


def _continued_command_tokens(block: str, command_fragment: str) -> list[str]:
    """Return one executable continued shell command as parsed argv tokens."""

    lines = block.splitlines()
    starts = [index for index, line in enumerate(lines) if command_fragment in line]
    assert len(starts) == 1, f"expected one command containing {command_fragment!r}"
    command_lines: list[str] = []
    index = starts[0]
    while index < len(lines):
        line = lines[index].strip()
        command_lines.append(line[:-1].rstrip() if line.endswith("\\") else line)
        if not line.endswith("\\"):
            break
        index += 1
    else:  # pragma: no cover - an unterminated command is itself the assertion failure
        raise AssertionError(f"unterminated command containing {command_fragment!r}")
    return shlex.split(" ".join(command_lines))


def _deploy_contract_text() -> str:
    """Return deploy.sh with reviewed sourced libraries expanded in place."""

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    for source in DEPLOY_LIB_SCRIPTS:
        source_line = f'. "$REPO_ROOT/scripts/lib/{source.name}"'
        assert script.count(source_line) == 1
        script = script.replace(source_line, source.read_text(encoding="utf-8"))
    return script


def _shell_function(name: str) -> str:
    script = _deploy_contract_text()
    start = script.index(f"{name}() {{")
    end = script.index("\n}\n", start) + len("\n}\n")
    return script[start:end]


def _write_deploy_fixture(path: Path, text: str) -> None:
    """Copy the command-of-record and every required sourced lifecycle library."""

    path.write_text(text, encoding="utf-8")
    lib_dir = path.parent / "lib"
    lib_dir.mkdir(exist_ok=True)
    for source in DEPLOY_LIB_SCRIPTS:
        (lib_dir / source.name).write_text(
            source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def test_sql_contract_scanner_rejects_commented_ddl_without_corrupting_literals() -> None:
    sql = """
    -- CREATE TABLE IF NOT EXISTS mip.gold.hidden_line (id INT);
    /* CREATE TABLE IF NOT EXISTS mip.gold.hidden_block (id INT); */
    SELECT '/api/*' AS route, '-- not a comment' AS label;
    CREATE TABLE IF NOT EXISTS mip.gold.visible (id INT);
    /* CREATE TABLE IF NOT EXISTS mip.gold.hidden_unclosed (id INT);
    """

    executable = _sql_without_comments(sql)

    assert "hidden_line" not in executable
    assert "hidden_block" not in executable
    assert "hidden_unclosed" not in executable
    assert "'/api/*'" in executable
    assert "'-- not a comment'" in executable
    assert re.search(
        r"(?im)^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+mip\.gold\.visible\b",
        executable,
    )


def _install_environment_recorder(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "child-env.log"
    recorder = bin_dir / "python"
    recorder.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == '-m' && "
        "\"${2:-}\" == 'tools.databricks.agent_proxy_credential_bundle' ]]; then\n"
        f'  exec {shlex.quote(sys.executable)} "$@"\n'
        "fi\n"
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


def _first_install_capture_finalize_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("finalize_signed_first_install_capture() {")
    end = text.index("refresh_first_install_journal_status() {", start)
    return text[start:end]


def _first_install_cleanup_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("refresh_first_install_journal_status() {")
    end = text.index("restore_rendered_sql_fail_closed() {", start)
    return text[start:end]


def _app_failure_compensation_block() -> str:
    text = _deploy_contract_text()
    start = text.index("converge_app_treatment_access() {")
    end = text.index("quiesce_app_treatment_after_failed_stop() {", start)
    return text[start:end]


def _deploy_auth_function_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("dotenv_value() {")
    end = text.index("# Step 0: preflight", start)
    return text[start:end]


def _identity_casefold_function_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("same_identity_casefold() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _proof_heartbeat_launcher_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index("start_proof_signing_heartbeat() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _first_snapshot_decision_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = text.index('if [[ "$APP_UPGRADE_STATE" == "first_install" ]]; then')
    end = text.index(
        "\nfi\n\n# -----------------------------------------------------------------------------",
        start,
    )
    end += len("\nfi\n")
    return text[start:end]


def _unsigned_candidate_rollback_block() -> str:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    release_gate = text.index('if [[ "$DRY_RUN" -eq 0 && "$FINAL_APP_PROVEN" -eq 1 ]]; then')
    start = text.index('elif [[ "$DRY_RUN" -eq 0 ]]; then', release_gate)
    end = text.index(
        "\nfi\n\n# -----------------------------------------------------------------------------",
        start,
    )
    block = text[start : end + len("\nfi\n")]
    return block.replace("elif [[", "if [[", 1)


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
        "DATABRICKS_RELEASE_PROBE_CLIENT_ID=release-probe-client",
        "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET=release-probe-secret",
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
resolve_m2m_credential DATABRICKS_RELEASE_PROBE_CLIENT_ID shell
resolve_m2m_credential DATABRICKS_RELEASE_PROBE_CLIENT_SECRET shell
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


def _run_deploy_lease_cleanup_harness(
    tmp_path: Path,
    *,
    original_rc: int,
    credential_quarantined: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    release_log = tmp_path / f"lease-release-{original_rc}.log"
    harness = tmp_path / f"lease-release-{original_rc}.sh"
    quarantine_marker = tmp_path / f"credential-quarantine-{original_rc}.marker"
    if credential_quarantined:
        quarantine_marker.write_text("retain lease\n", encoding="utf-8")
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
APP_DEPLOYMENT_LEASE_ID=lease-id
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
OAUTH_CREDENTIAL_QUARANTINE_FILE={shlex.quote(str(quarantine_marker))}
_GRANTS_APP_NAME=mip-app
RED=""
YLW=""
DIM=""
RST=""
PYTHON=python3
stop_app_after_failed_deploy() {{ return 0; }}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
run_with_proof_signing_authority() {{ printf '%s\n' "$*" > {shlex.quote(str(release_log))}; }}
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
        release_log,
    )


def _run_app_failure_compensation_harness(
    tmp_path: Path,
    *,
    state: str,
    rollback_result: int,
    stop_result: int,
    stop_outcome: str = "stopped",
    app_principal: str = "app-client",
    stop_outcome_record: str | None = None,
    access_quarantined: bool = False,
    release_acl_result: int = 0,
    function_grants_proven: bool = True,
    lakebase_runtime_access_proven: bool = True,
    first_install_created: bool = False,
    journal_status: str = "recover",
    expected_identity: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    calls = tmp_path / f"app-compensation-{state}.log"
    fake_python = tmp_path / f"fake-python-{state}.sh"
    outcome_record = stop_outcome_record or f"MIP_APP_STOP_OUTCOME={stop_outcome}\n"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        f'if [[ "$*" == *"app_deployment_rollback restore"* ]]; then exit {rollback_result}; fi\n'
        f'if [[ "$*" == *converge_app_release_access* ]]; then exit {release_acl_result}; fi\n'
        'if [[ "$*" == *"app_first_install_journal status"* ]]; then\n'
        '  out_env=""\n'
        "  while (( $# )); do\n"
        '    if [[ "$1" == --out-env && $# -ge 2 ]]; then out_env="$2"; break; fi\n'
        "    shift\n"
        "  done\n"
        '  [[ -n "$out_env" ]] || exit 64\n'
        "  {\n"
        f"    printf '%s\\n' MIP_FIRST_INSTALL_JOURNAL_STATUS={journal_status}\n"
        "    printf '%s\\n' MIP_FIRST_INSTALL_APP_ID=app-object-id\n"
        "    printf '%s\\n' MIP_FIRST_INSTALL_APP_CLIENT_ID=app-client-id\n"
        "    printf '%s\\n' MIP_FIRST_INSTALL_APP_SCIM_ID=app-scim-id\n"
        '  } > "$out_env"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "$*" == *stop_app_fail_closed* ]]; then\n'
        f"  [[ {stop_result} -eq 0 ]] || exit {stop_result}\n"
        '  outcome_file=""\n'
        "  while (( $# )); do\n"
        '    if [[ "$1" == --out-env && $# -ge 2 ]]; then outcome_file="$2"; break; fi\n'
        "    shift\n"
        "  done\n"
        '  [[ -n "$outcome_file" ]] || exit 64\n'
        f'  printf %s {shlex.quote(outcome_record)} > "$outcome_file"\n'
        "  exit 0\n"
        "fi\n"
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
APP_EXPECTED_IDENTITY_ARGS=()
APP_UPGRADE_STATE={shlex.quote(state)}
APP_ROLLBACK_SECRET_SCOPE=mip
APP_SIGNED_BLUE_AVAILABLE=1
AGENT_PROXY_ACCESS_MUTATED=0
MIP_APP_ROLLBACK_RECORD_VERSION=6
MIP_APP_ROLLBACK_PROXY_MODE=exact-proxy
MIP_APP_ROLLBACK_DEPLOYMENT_ID=blue-deployment
MIP_APP_ROLLBACK_SUPERVISOR_ID=blue-supervisor
MIP_APP_ROLLBACK_SUPERVISOR_CREATOR=runtime-client
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT=blue-supervisor-endpoint
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID=blue-supervisor-endpoint-id
MIP_APP_ROLLBACK_RUNTIME_APPLICATION_ID=runtime-client
MIP_APP_ROLLBACK_GENIE_SPACE_ID=genie-space
MIP_APP_ROLLBACK_PROXY_APPLICATION_ID=proxy-client
TREATMENT_RUNTIME_QUIESCED={0 if state in {"blue_active", "green_verified", "green_treatment_pending_capture"} else 1}
APP_SP_CLIENT_ID={shlex.quote(app_principal)}
_EXISTING_APP_SP_CLIENT_ID={shlex.quote(app_principal)}
APP_ACCESS_QUARANTINED={1 if access_quarantined else 0}
REVIEWED_FUNCTION_GRANTS_PROVEN={1 if function_grants_proven else 0}
LAKEBASE_RUNTIME_ACCESS_PROVEN={1 if lakebase_runtime_access_proven else 0}
FIRST_INSTALL_APP_CREATED={1 if first_install_created else 0}
FIRST_INSTALL_COMPENSATION_AUTHORIZED=0
FIRST_INSTALL_APP_BOUND=0
FIRST_INSTALL_JOURNAL_STATUS={shlex.quote(journal_status)}
MIP_APP_DEPLOYMENT_LEASE_ID=lease-id
SOURCE_GIT_SHA={'a' * 40}
MIP_LAKEBASE_INSTANCE=mip-lakebase
DATABRICKS_RELEASE_PROBE_CLIENT_ID=release-probe
DATABRICKS_CLIENT_ID=normal
DATABRICKS_OPERATOR2_CLIENT_ID=operator2
DATABRICKS_ADMIN_CLIENT_ID=admin
_GRANTS_WAREHOUSE_ID=warehouse-id
_GRANTS_CATALOG=mip
MIP_APP_URL=https://mip.example
MIP_BEARER_TOKEN=token
GENIE_SPACE_ID=genie-space
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
DATABRICKS_AGENT_PROXY_CLIENT_SECRET=proxy-secret
DATABRICKS_AGENT_PROXY_CREDENTIAL_ID=proxy-credential
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_ACCOUNT_HOST=https://accounts.cloud.databricks.com
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=admin@example.com
PYTHON={shlex.quote(str(fake_python))}
RED=""
YLW=""
RST=""
{"APP_EXPECTED_IDENTITY_ARGS=(--expected-app-id app-object-id --expected-client-id app-client-id --expected-scim-id app-scim-id)" if expected_identity else ""}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
run_with_lakebase_bootstrap_authority() {{ "$@"; }}
run_with_agent_proxy_credentials() {{ "$@"; }}
mint_m2m_token() {{ printf 'mint %s\n' "$*" >> {shlex.quote(str(calls))}; }}
{_first_install_cleanup_block()}
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


def _run_first_install_cleanup_harness(
    tmp_path: Path,
    *,
    journal_status: str,
    bound: bool,
    unbind_result: int = 0,
    delete_result: int = 0,
    role_recovery_result: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    calls = tmp_path / f"first-install-cleanup-{journal_status}-{bound}.log"
    fake_python = tmp_path / f"first-install-cleanup-{journal_status}-{bound}.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        'if [[ "$*" == *"app_first_install_journal status"* ]]; then\n'
        '  out_env=""\n'
        "  while (( $# )); do\n"
        '    if [[ "$1" == --out-env && $# -ge 2 ]]; then out_env="$2"; break; fi\n'
        "    shift\n"
        "  done\n"
        '  [[ -n "$out_env" ]] || exit 64\n'
        "  {\n"
        f"    printf '%s\\n' MIP_FIRST_INSTALL_JOURNAL_STATUS={journal_status}\n"
        "    printf '%s\\n' MIP_FIRST_INSTALL_APP_ID=app-object-id\n"
        "    printf '%s\\n' MIP_FIRST_INSTALL_APP_CLIENT_ID=app-client-id\n"
        "    printf '%s\\n' MIP_FIRST_INSTALL_APP_SCIM_ID=app-scim-id\n"
        '  } > "$out_env"\n'
        "  exit 0\n"
        "fi\n"
        f'if [[ "$*" == *"converge_lakebase_oauth_role"* ]]; then exit {role_recovery_result}; fi\n'
        f'if [[ "$*" == *"bundle_env deployment unbind"* ]]; then exit {unbind_result}; fi\n'
        f'if [[ "$*" == *"app_first_install_journal delete"* ]]; then exit {delete_result}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / f"first-install-cleanup-{journal_status}-{bound}.harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
FIRST_INSTALL_APP_CREATED=1
FIRST_INSTALL_APP_BOUND={1 if bound else 0}
FIRST_INSTALL_JOURNAL_STATUS={shlex.quote(journal_status)}
APP_FAIL_CLOSED_NAME=mip-app
MIP_APP_DEPLOYMENT_LEASE_ID=lease-id
SOURCE_GIT_SHA={'a' * 40}
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-lakebase
LAKEBASE_DATABASE=mip_app_state
TARGET=dev
PYTHON={shlex.quote(str(fake_python))}
RED=""
YLW=""
RST=""
run_with_proof_signing_authority() {{ "$@"; }}
run_with_lakebase_bootstrap_authority() {{ "$@"; }}
{_first_install_cleanup_block()}
cleanup_failed_first_install_app
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


def _run_signed_capture_retirement_failure_harness(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], str]:
    calls = tmp_path / "signed-capture-retirement.log"
    fake_python = tmp_path / "signed-capture-retirement-python.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        'if [[ "$*" == *"app_first_install_journal complete"* ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "signed-capture-retirement.harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
APP_LAST_DEPLOY_PAYLOAD=""
APP_BUNDLE_SUMMARY=""
APP_ROLLBACK_BINDING_ENV=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
CUTOVER_JOURNAL_ENV_FILE=""
APP_DEPLOYMENT_LEASE_ENV=""
APP_RESOURCE_BINDING_SUMMARY=""
APP_RESOURCE_BINDING_PAYLOAD=""
APP_RESOURCE_BINDING_BEFORE=""
APP_RESOURCE_BINDING_AFTER=""
FIRST_INSTALL_JOURNAL_ENV=""
FIRST_INSTALL_MARKED_PAYLOAD=""
_PII_SECRET_PAYLOAD=""
APP_DEPLOYMENT_LEASE_ID=""
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
FIRST_INSTALL_APP_CREATED=1
FIRST_INSTALL_APP_BOUND=1
FIRST_INSTALL_JOURNAL_STATUS=prepared
TREATMENT_RUNTIME_QUIESCED=1
APP_UPGRADE_STATE=green_treatment_pending_capture
APP_NAME=mip-app
MIP_APP_DEPLOYMENT_LEASE_ID=lease-id
SOURCE_GIT_SHA={'a' * 40}
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-lakebase
RED=""
YLW=""
DIM=""
RST=""
PYTHON={shlex.quote(str(fake_python))}
step() {{ :; }}
run_with_proof_signing_authority() {{ "$@"; }}
run_with_lakebase_bootstrap_authority() {{ "$@"; }}
stop_app_after_failed_deploy() {{
  printf 'stop:%s\\n' "$APP_UPGRADE_STATE" >> {shlex.quote(str(calls))}
  return 0
}}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
{_first_install_capture_finalize_block()}
{_first_install_cleanup_block()}
{_deploy_exit_trap_block()}
finalize_signed_first_install_capture
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
        ["git", "add", "scripts", ".gitignore"],
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


@pytest.mark.parametrize("path", (DEPLOY_SCRIPT, *DEPLOY_LIB_SCRIPTS))
def test_deploy_shell_sources_are_syntactically_valid(path: Path) -> None:
    result = subprocess.run(
        ["bash", "-n", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_deploy_sources_reviewed_lifecycle_libraries_without_option_or_trap_drift() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    source_lines = [f'. "$REPO_ROOT/scripts/lib/{path.name}"' for path in DEPLOY_LIB_SCRIPTS]

    assert [line for line in script.splitlines() if line in source_lines] == source_lines
    exact_source_gate = script.index("\nverify_exact_deploy_source\n")
    assert all(exact_source_gate < script.index(line) for line in source_lines)
    for path in DEPLOY_LIB_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert "\nset " not in text
        assert "\ntrap " not in text
        assert "\nexit " not in text


def test_supervisor_creation_lifecycle_recovers_before_cleanup_and_separates_authority() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    recovery = script.index("\nrun recover_pending_supervisor_creation\n")
    stale_retirement = script.index('\nif [[ "$STALE_CUTOVER_JOURNAL_PENDING" -eq 1 ]]', recovery)
    historical_cleanup = script.index(
        "tools.databricks.reconcile_historical_agent_endpoints cleanup",
        stale_retirement,
    )
    retry_acl = script.index("\nrun reconcile_retry_supervisor_app_acl\n", historical_cleanup)
    planned_creation = script.index(
        "\nrun create_planned_supervisor_if_needed\n",
        retry_acl,
    )
    ordinary_provisioning = script.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"',
        planned_creation,
    )
    handoff_clearance = script.index(
        "\nrun finalize_supervisor_creation_handoff\n",
        ordinary_provisioning,
    )
    env_import = script.index(
        '\nif [[ "$DRY_RUN" -eq 0 ]]; then',
        handoff_clearance,
    )

    assert recovery < stale_retirement < historical_cleanup < retry_acl
    assert retry_acl < planned_creation < ordinary_provisioning
    assert ordinary_provisioning < handoff_clearance < env_import

    creation = _shell_function("create_planned_supervisor_if_needed")
    finalize_blue = creation.index("supervisor_creation_runtime finalize-signed-blue")
    plan = creation.index("supervisor_creation_control plan-prepare")
    create = creation.index("supervisor_creation_runtime create")
    claim = creation.index("supervisor_creation_control claim-result")
    complete = creation.index("supervisor_creation_runtime complete")
    verification = creation.index("supervisor_creation_control verify-complete")
    assert finalize_blue < plan < create < claim < complete < verification
    assert "supervisor_creation_control complete" not in creation
    assert creation.count("run_as_m2m_identity") == 3
    assert creation.count("run_with_proof_signing_authority") == 3
    assert "MIP_AI_GATEWAY_PROOF_SIGNING_KEY" not in creation

    recovery_helper = _shell_function("recover_pending_supervisor_creation")
    adoption = recovery_helper.index("supervisor_creation_control adopt")
    classification = recovery_helper.index("supervisor_creation_control classify-policy")
    runtime_completion = recovery_helper.index("supervisor_creation_runtime complete")
    assert adoption < classification < runtime_completion
    assert 'historical)\n      step "defer the revoked historical Supervisor tuple' in (
        recovery_helper
    )
    assert (
        recovery_helper.index("historical)")
        < recovery_helper.index("return 0", recovery_helper.index("historical)"))
        < runtime_completion
    )
    for runtime_mutation in (creation, recovery_helper):
        assert "--canonical-name" in runtime_mutation
        assert "--genie-space-id" in runtime_mutation
        assert "--catalog" in runtime_mutation

    finalizer = _shell_function("finalize_supervisor_creation_handoff")
    assert "supervisor_creation_control complete" in finalizer
    assert finalizer.count("run_with_proof_signing_authority") == 1
    for helper in (
        _shell_function("recover_pending_supervisor_creation"),
        creation,
        finalizer,
    ):
        assert '[[ "$DRY_RUN" -eq 0 ]] || return 0' in helper


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
    verifier_role_recovery = script.index(
        'step "recover interrupted verifier Lakebase role bootstrap"'
    )
    app_inventory = script.index('_EXISTING_APPS_JSON="$(databricks apps list -o json)"')
    app_role_recovery = script.index('step "recover interrupted App Lakebase role bootstrap"')
    journal_status = script.index(
        'step "read signed first-install journal at the immediate recovery boundary"',
        app_role_recovery,
    )
    journal_role_recovery = script.index(
        "recover_journaled_first_install_lakebase_bootstrap",
        journal_status,
    )
    early_cleanup = script.index("could not clear prior agent-runtime bootstrap privileges")
    frontend_build = script.index('step "build frontend')
    bundle_deploy = script.index(
        'step "deploy non-App bundle resources without activating an App candidate"'
    )

    assert (
        lease
        < app_inventory
        < verifier_role_recovery
        < app_role_recovery
        < journal_status
        < journal_role_recovery
        < early_cleanup
        < frontend_build
        < bundle_deploy
    )
    recovery_block = script[verifier_role_recovery:early_cleanup]
    assert recovery_block.count("--recover-bootstrap-only") == 2
    assert recovery_block.count('"$LAKEBASE_DATABASE"') == 2
    assert "MIP_LAKEBASE_DATABASE" not in recovery_block
    assert "SHOW GRANTS \\`${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}\\` ON SCHEMA" in script
    assert "SHOW GRANTS TO" not in script


def test_existing_app_is_stopped_and_identity_pinned_before_binding_update() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    existing_update = script.index('step "update existing Databricks App resource bindings')
    identity_read = script.index("_BINDING_APP_ID _BINDING_APP_CLIENT_ID", existing_update)
    stop = script.index("tools.databricks.stop_app_fail_closed", identity_read)
    stopped_baseline = script.index(
        'databricks apps get "$_GRANTS_APP_NAME" -o json > "$APP_RESOURCE_BINDING_BEFORE"',
        stop,
    )
    update = script.index('databricks apps update "$_GRANTS_APP_NAME"', stop)
    convergence = script.index("tools.databricks.converge_lakebase_oauth_role", update)

    assert existing_update < identity_read < stop < stopped_baseline < update < convergence
    assert '--expected-app-id "$_BINDING_APP_ID"' in script[stop:update]
    assert '--expected-client-id "$_BINDING_APP_CLIENT_ID"' in script[stop:update]
    assert '--expected-scim-id "$_BINDING_APP_SCIM_ID"' in script[stop:update]


def test_all_post_inventory_name_mutations_have_exact_identity_boundaries() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    update = script.index('databricks apps update "$_GRANTS_APP_NAME"')
    assert script.rfind('assert_expected_app_identity "$_GRANTS_APP_NAME"', 0, update) > 0
    assert script.index('assert_expected_app_identity "$_GRANTS_APP_NAME"', update) > update

    wait_helper = _shell_function("wait_for_app_deployable")
    start = wait_helper.index('databricks apps start "$APP_NAME"')
    assert wait_helper.rfind('assert_expected_app_identity "$APP_NAME"', 0, start) > 0
    assert wait_helper.index('assert_expected_app_identity "$APP_NAME"', start) > start

    deploy_helper = _shell_function("deploy_app_snapshot")
    deploy = deploy_helper.index('databricks apps deploy "$APP_NAME"')
    assert deploy_helper.rfind('assert_expected_app_identity "$APP_NAME"', 0, deploy) > 0
    assert deploy_helper.index('assert_expected_app_identity "$APP_NAME"', deploy) > deploy

    no_proof = _unsigned_candidate_rollback_block()
    final_stop = no_proof.index("tools.databricks.stop_app_fail_closed")
    final_quiesce = no_proof.index("converge_app_treatment_access quiesce")
    assert '"${APP_EXPECTED_IDENTITY_ARGS[@]}"' in no_proof[final_stop:final_quiesce]


def test_lakebase_access_proof_brackets_binding_role_rotation_and_migration() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    initial_proof = script.index("LAKEBASE_RUNTIME_ACCESS_PROVEN=0")
    binding_build = script.index("tools.databricks.app_resource_bindings build")
    proof_invalidated = script.index("LAKEBASE_RUNTIME_ACCESS_PROVEN=0", binding_build)
    app_create = script.index("databricks apps create", proof_invalidated)
    role_convergence = script.index(
        "tools.databricks.converge_lakebase_oauth_role",
        app_create,
    )
    migration = script.index("databricks bundle run mip_lakebase_migrate", role_convergence)
    proof_restored = script.index("LAKEBASE_RUNTIME_ACCESS_PROVEN=1", migration)

    assert initial_proof < binding_build < proof_invalidated
    assert "LAKEBASE_RUNTIME_ACCESS_PROVEN=1" not in script[initial_proof:migration]
    assert proof_invalidated < app_create < role_convergence < migration < proof_restored


def test_unproven_lakebase_access_forces_stop_without_signed_blue_restore(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="blue_quiesced",
        rollback_result=0,
        stop_result=0,
        lakebase_runtime_access_proven=False,
    )

    assert result.returncode == 0, result.stderr
    assert "stop_app_fail_closed" in calls
    assert "app_deployment_rollback" not in calls
    assert "converge_campaign_treatment_access" in calls


def test_legacy_rebase_failure_compensation_retains_exact_app_identity(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=0,
        stop_result=0,
        expected_identity=True,
        lakebase_runtime_access_proven=False,
    )

    assert result.returncode == 0, result.stderr
    stop_call = next(line for line in calls.splitlines() if "stop_app_fail_closed" in line)
    assert "--expected-app-id app-object-id" in stop_call
    assert "--expected-client-id app-client-id" in stop_call
    assert "--expected-scim-id app-scim-id" in stop_call


def test_legacy_rebase_compensation_refuses_mutation_after_identity_replacement(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=0,
        stop_result=1,
        expected_identity=True,
        lakebase_runtime_access_proven=False,
    )

    assert result.returncode == 1
    stop_call = next(line for line in calls.splitlines() if "stop_app_fail_closed" in line)
    assert "--expected-app-id app-object-id" in stop_call
    assert "converge_app_release_access" not in calls
    assert "converge_campaign_treatment_access" not in calls


def test_secondary_treatment_compensation_reauthenticates_pinned_app_first() -> None:
    helper = _shell_function("quiesce_app_treatment_after_failed_stop")
    identity_check = helper.index("--assert-identity-only")
    treatment = helper.index("tools.databricks.converge_campaign_treatment_access")

    assert identity_check < treatment
    assert '"${APP_EXPECTED_IDENTITY_ARGS[@]}"' in helper[:treatment]
    assert "refusing secondary treatment mutation" in helper


def test_every_lakebase_role_recovery_gets_bounded_signing_and_account_authority() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    command = '"$PYTHON" -m tools.databricks.converge_lakebase_oauth_role'
    bounded = re.findall(
        r"run_with_lakebase_bootstrap_authority \\\n\s+"
        r'"\$PYTHON" -m tools\.databricks\.converge_lakebase_oauth_role',
        script,
    )

    assert len(bounded) == script.count(command)
    assert 'run_with_account_identity run_with_proof_signing_authority "$@"' in script
    assert not re.search(
        r'(?m)^\s*run "\$PYTHON" -m tools\.databricks\.converge_lakebase_oauth_role',
        script,
    )


def test_every_app_rollback_gets_bounded_signing_and_account_authority() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    command = '"$PYTHON" -m tools.databricks.app_deployment_rollback'
    bounded = re.findall(
        r"run_with_account_identity \\\n\s+"
        r"run_with_proof_signing_authority \\\n\s+"
        r'"\$PYTHON" -m tools\.databricks\.app_deployment_rollback',
        script,
    )
    capture = _shell_function("capture_last_good_app")

    assert len(bounded) == script.count(command)
    assert (
        "run_with_account_identity \\\n"
        '    run_with_proof_signing_authority "$PYTHON" "${args[@]}"' in capture
    )
    assert "tools.databricks.app_deployment_rollback capture" in capture


def test_app_rollback_nested_authorities_export_scoped_credentials(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "rollback-authority-probe.py"
    result_file = tmp_path / "rollback-authority.json"
    probe.write_text(
        "import json, os, sys\n"
        "names = (\n"
        '    "DATABRICKS_ACCOUNT_CLIENT_ID",\n'
        '    "DATABRICKS_ACCOUNT_CLIENT_SECRET",\n'
        '    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",\n'
        ")\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
        "    json.dump({name: os.environ.get(name) for name in names}, handle)\n",
        encoding="utf-8",
    )
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        DRY_RUN=0
        DIM=""
        RED=""
        RST=""
        DATABRICKS_ACCOUNT_CLIENT_ID=account-client
        DATABRICKS_ACCOUNT_CLIENT_SECRET=account-secret
        MIP_AI_GATEWAY_PROOF_SIGNING_KEY=proof-secret
        export -n DATABRICKS_ACCOUNT_CLIENT_ID DATABRICKS_ACCOUNT_CLIENT_SECRET
        export -n MIP_AI_GATEWAY_PROOF_SIGNING_KEY
        {_shell_function("run_with_account_identity")}
        {_shell_function("run_with_proof_signing_authority")}
        run_with_account_identity run_with_proof_signing_authority \
          {shlex.quote(sys.executable)} {shlex.quote(str(probe))} \
          {shlex.quote(str(result_file))}
        {shlex.quote(sys.executable)} -c 'import os; raise SystemExit(any(
            name in os.environ
            for name in (
                "DATABRICKS_ACCOUNT_CLIENT_ID",
                "DATABRICKS_ACCOUNT_CLIENT_SECRET",
                "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
            )
        ))'
        """
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "DATABRICKS_ACCOUNT_CLIENT_ID",
            "DATABRICKS_ACCOUNT_CLIENT_SECRET",
            "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        }
    }

    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result_file.read_text(encoding="utf-8")) == {
        "DATABRICKS_ACCOUNT_CLIENT_ID": "account-client",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET": "account-secret",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": "proof-secret",
    }
    assert "account-secret" not in result.stdout + result.stderr
    assert "proof-secret" not in result.stdout + result.stderr


def test_dual_authority_uc_wrappers_export_exact_bounded_credentials(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "uc-authority-probe.py"
    proxy_result = tmp_path / "proxy-authority.json"
    runtime_result = tmp_path / "runtime-authority.json"
    names = (
        "DATABRICKS_ACCOUNT_CLIENT_ID",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        "DATABRICKS_AGENT_PROXY_CLIENT_ID",
        "DATABRICKS_AGENT_PROXY_CLIENT_SECRET",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
        "DATABRICKS_CLIENT_SECRET",
    )
    probe.write_text(
        "import json, os, sys\n"
        f"names = {names!r}\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
        "    json.dump({name: os.environ.get(name) for name in names}, handle)\n",
        encoding="utf-8",
    )
    command = textwrap.dedent(
        f"""
        set -euo pipefail
        DRY_RUN=0
        DIM=""
        RED=""
        RST=""
        DATABRICKS_ACCOUNT_CLIENT_ID=account-client
        DATABRICKS_ACCOUNT_CLIENT_SECRET=account-secret
        MIP_AI_GATEWAY_PROOF_SIGNING_KEY=proof-secret
        DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
        DATABRICKS_AGENT_PROXY_CLIENT_SECRET=proxy-secret
        DATABRICKS_AGENT_PROXY_CREDENTIAL_ID=proxy-credential
        DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
        DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=runtime-secret
        DATABRICKS_CLIENT_SECRET=unrelated-secret
        export -n {' '.join(names)}
        run() {{ "$@"; }}
        {_shell_function("run_with_account_identity")}
        {_shell_function("run_with_proof_signing_authority")}
        {_shell_function("run_with_agent_proxy_credentials")}
        {_shell_function("run_with_agent_runtime_credentials")}
        run_with_account_identity \
          run_with_proof_signing_authority \
            run_with_agent_proxy_credentials \
              {shlex.quote(sys.executable)} {shlex.quote(str(probe))} \
              {shlex.quote(str(proxy_result))}
        run_with_account_identity \
          run_with_proof_signing_authority \
            run_with_agent_runtime_credentials \
              {shlex.quote(sys.executable)} {shlex.quote(str(probe))} \
              {shlex.quote(str(runtime_result))}
        """
    )
    env = {key: value for key, value in os.environ.items() if key not in names}

    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    proxy = json.loads(proxy_result.read_text(encoding="utf-8"))
    runtime = json.loads(runtime_result.read_text(encoding="utf-8"))
    assert proxy == {
        "DATABRICKS_ACCOUNT_CLIENT_ID": "account-client",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET": "account-secret",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": "proof-secret",
        "DATABRICKS_AGENT_PROXY_CLIENT_ID": "proxy-client",
        "DATABRICKS_AGENT_PROXY_CLIENT_SECRET": "proxy-secret",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_ID": "proxy-credential",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": None,
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET": None,
        "DATABRICKS_CLIENT_SECRET": None,
    }
    assert runtime == {
        "DATABRICKS_ACCOUNT_CLIENT_ID": "account-client",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET": "account-secret",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": "proof-secret",
        "DATABRICKS_AGENT_PROXY_CLIENT_ID": None,
        "DATABRICKS_AGENT_PROXY_CLIENT_SECRET": None,
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_ID": None,
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "runtime-client",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET": "runtime-secret",
        "DATABRICKS_CLIENT_SECRET": None,
    }


def test_lakebase_bootstrap_receives_only_explicit_fresh_m2m_control_names() -> None:
    helper = _shell_function("run_with_lakebase_bootstrap_authority")

    assert 'control_client_id="${DATABRICKS_AGENT_RUNTIME_CLIENT_ID:-}"' in helper
    assert 'control_client_secret="${DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET:-}"' in helper
    assert 'export MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_ID="$control_client_id"' in helper
    assert 'export MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET="$control_client_secret"' in helper
    assert "fresh OAuth-M2M Lakebase bootstrap control credentials are missing" in helper
    assert 'export DATABRICKS_CLIENT_SECRET="$control_client_secret"' not in helper


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
        "normal, operator2, admin, release-probe, verifier, agent-runtime, and agent-proxy "
        "M2M client IDs "
        "must be pairwise distinct" in text
    )
    assert (
        text.count("mint_m2m_token MIP_ADMIN_BEARER_TOKEN") >= 2
    )  # initial per-run mint + immediate pre-eval remint
    remint_pos = text.index("A full deploy can exceed the workspace OAuth TTL")
    eval_pos = text.index("-m tools.databricks.run_agent_eval")
    assert remint_pos < eval_pos


def test_dynamic_app_identity_separation_is_casefolded_before_recovery(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    existing_identity = script.index("_EXISTING_APP_SP_CLIENT_ID _EXISTING_APP_SP_SCIM_ID")
    existing_guard = script.index(
        "if same_identity_casefold \\\n"
        '      "$DATABRICKS_ACCOUNT_CLIENT_ID" "$_EXISTING_APP_SP_CLIENT_ID"',
        existing_identity,
    )
    existing_recovery = script.index(
        'step "recover interrupted App Lakebase role bootstrap"',
        existing_identity,
    )
    owner_audit = script.index(
        'step "preflight agent-runtime foreign UC access before Lakebase bootstrap mutation"',
        existing_identity,
    )
    assert existing_identity < existing_guard < owner_audit < existing_recovery

    app_resolution = script.index('APP_RESOURCE_JSON="$(databricks apps get')
    new_guard = script.index(
        "if same_identity_casefold \\\n" '    "$DATABRICKS_ACCOUNT_CLIENT_ID" "$APP_SP_CLIENT_ID"',
        app_resolution,
    )
    migration = script.index(
        'step "converge App Lakebase OAuth role to exact LOGIN-only profile"',
        app_resolution,
    )
    assert app_resolution < new_guard < migration

    harness = tmp_path / "casefold-app-identity.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            PYTHON={shlex.quote(sys.executable)}
            {_identity_casefold_function_block()}
            same_identity_casefold shared-app-id SHARED-APP-ID
            if same_identity_casefold shared-app-id different-app-id; then
              exit 90
            fi
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_deploy_binds_deployer_auth_and_keeps_normal_app_oauth_shell_scoped() -> None:
    text = _deploy_contract_text()

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
    assert len(audit_invocations) == 10
    assert all("--expected-inventory-principal" in block for block in audit_invocations)
    direct_audit = '"$PYTHON" -m tools.databricks.audit_global_m2m_access'
    assert (
        len(
            re.findall(
                r"run_with_account_identity run_with_proof_signing_authority \\\n"
                r'\s+"\$PYTHON" -m tools\.databricks\.audit_global_m2m_access',
                text,
            )
        )
        == 5
    )
    assert text.count(direct_audit) == 5
    for args_name in (
        "captured_audit_args",
        "app_audit_args",
        "RUNTIME_GLOBAL_ACCESS_ARGS",
        "VERIFIER_GLOBAL_ACCESS_ARGS",
        "APP_GLOBAL_ACCESS_ARGS",
    ):
        assert re.search(
            r"run_with_account_identity run_with_proof_signing_authority "
            rf'\\\n\s+"\$PYTHON" "\$\{{{args_name}\[@\]\}}"',
            text,
        )
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


def test_signed_blue_cutover_pins_survive_bounded_agent_runtime_environment(
    tmp_path: Path,
) -> None:
    observed = tmp_path / "cutover-pin-env.log"
    probe = tmp_path / "cutover-pin-probe.sh"
    probe.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n%s\\n' \"$MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON\" "
        f'"$MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON" > {shlex.quote(str(observed))}\n',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    harness = tmp_path / "cutover-pin-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
DIM=""
RST=""
MIP_DATABRICKS_WORKSPACE_HOST=https://workspace.example
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=runtime-secret
{_shell_function("run_as_m2m_identity")}
MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON='{{"name":"blue-gateway"}}' \
MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON='{{"supervisor_id":"blue-supervisor"}}' \
  run_as_m2m_identity \
    agent-runtime \
    DATABRICKS_AGENT_RUNTIME_CLIENT_ID \
    DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \
    {shlex.quote(str(probe))}
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert observed.read_text(encoding="utf-8").splitlines() == [
        '{"name":"blue-gateway"}',
        '{"supervisor_id":"blue-supervisor"}',
    ]


def test_deploy_requires_control_plane_and_runtime_uc_boundary_proofs() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    early_preflight = text.index(
        'step "preflight agent-runtime foreign UC access before Lakebase bootstrap mutation"'
    )
    first_lakebase_runtime_use = text.index(
        'step "recover interrupted verifier Lakebase role bootstrap"'
    )
    preflight = text.index(
        'step "preflight agent-runtime foreign UC access before runtime-owned UC mutations"'
    )
    first_runtime_use = text.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    historical_reconciliation = text.index(
        'step "capture the verifier immutable identity before retirement admission"'
    )
    dual_authority = text.index(
        'step "prove dual-authority agent-runtime UC boundary before cutover"'
    )
    cutover = text.index(
        'step "prepare runtime-owned Gateway access while preserving the live old Supervisor"'
    )
    preflight_block = text[preflight:historical_reconciliation]
    dual_block = text[dual_authority:cutover]

    early_block = text[early_preflight:first_lakebase_runtime_use]
    assert early_preflight < first_lakebase_runtime_use < preflight
    assert preflight < historical_reconciliation < first_runtime_use < dual_authority < cutover
    assert "-m tools.databricks.audit_agent_runtime_foreign_uc_access" in early_block
    assert '--application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"' in early_block
    assert "--allow-missing-mip-catalog" in early_block
    assert "run_with_account_identity" in early_block
    assert "run_with_lakebase_bootstrap_authority" not in early_block
    assert "-m tools.databricks.audit_agent_runtime_foreign_uc_access" in preflight_block
    assert '--application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"' in preflight_block
    assert '--catalog "${MIP_DEFAULT_CATALOG:-mip}"' in preflight_block
    assert '--expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"' in preflight_block
    assert "run_with_account_identity" in preflight_block
    assert "run_as_m2m_identity" not in preflight_block
    assert "MIP_LAKEBASE_BOOTSTRAP_CONTROL_CLIENT_SECRET" not in preflight_block
    assert "-m tools.databricks.verify_agent_runtime_uc_boundary_dual_authority" in dual_block
    assert '--expected-inventory-principal "$DEPLOY_INVENTORY_PRINCIPAL"' in dual_block
    assert "run_with_account_identity" in dual_block
    assert "run_with_agent_runtime_credentials" in dual_block
    assert "run_as_m2m_identity" not in dual_block
    dual_helper = _shell_function("run_with_agent_runtime_credentials")
    assert 'export DATABRICKS_AGENT_RUNTIME_CLIENT_ID="$client_id"' in dual_helper
    assert 'export DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET="$client_secret"' in dual_helper
    assert 'run "$@"' in dual_helper


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
    read_only = text.index('step "prove dual-authority agent-runtime UC boundary before cutover"')
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
    assert runtime_authorized["MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY"] == "model-signing-secret"
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
    assert "group: mip-dev-live-state" in text
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
        "Normal, operator2, admin, release-probe, verifier, agent-runtime, and agent-proxy "
        "M2M client IDs "
        "must be pairwise distinct." in text
    )
    assert "os.environ[name].strip().casefold()" in text
    assert ").strip().casefold()" in text
    assert (
        "MIP_APPROVER_IDENTITIES=${DATABRICKS_CLIENT_ID},${DATABRICKS_OPERATOR2_CLIENT_ID}" in text
    )
    assert (
        "MIP_ADMIN_IDENTITIES=${DATABRICKS_ADMIN_CLIENT_ID},"
        "${DATABRICKS_RELEASE_PROBE_CLIENT_ID}" in text
    )
    assert "Configure MIP_ADMIN_EMAILS or MIP_ADMIN_GROUP_NAME" not in text


def test_deploy_uses_isolated_release_probe_only_during_signed_capture_gate() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert (
        workflow.count(
            "DATABRICKS_RELEASE_PROBE_CLIENT_ID: "
            "${{ secrets.DATABRICKS_RELEASE_PROBE_CLIENT_ID }}"
        )
        == 2
    )
    assert (
        workflow.count(
            "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET: "
            "${{ secrets.DATABRICKS_RELEASE_PROBE_CLIENT_SECRET }}"
        )
        == 1
    )
    required_loop = workflow[workflow.index('missing=""') : workflow.index("python - <<'PY'")]
    assert "DATABRICKS_RELEASE_PROBE_CLIENT_ID" in required_loop
    assert "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET" not in required_loop
    assert "export MIP_ADMIN_IDENTITIES" in script
    assert "\"$DATABRICKS_RELEASE_PROBE_CLIENT_ID\" <<'PYEOF'" in script

    rebase = script.index('if [[ "${MIP_REBASE_UNVERIFIED_APP:-0}" == "1" && \\')
    absence_gate = script.index("tools.databricks.app_rollback_bootstrap_gate", rebase)
    fail_closed_arm = script.index("APP_FAIL_CLOSED_ARMED=1", absence_gate)
    stop = script.index("tools.databricks.stop_app_fail_closed", rebase)
    quarantine = script.index("tools.databricks.converge_app_release_access", stop)
    quarantined_state = script.index("APP_ACCESS_QUARANTINED=1", quarantine)
    treatment_quiesce = script.index(
        "tools.databricks.converge_campaign_treatment_access", quarantined_state
    )
    rebase_first_install = script.index('APP_UPGRADE_STATE="first_install"', treatment_quiesce)
    first_snapshot_guard = script.index(
        'if [[ "$APP_UPGRADE_STATE" == "first_install" ]]; then', rebase_first_install
    )
    first_snapshot = script.index(
        'deploy_app_snapshot "deploy first-install Databricks App snapshot from uploaded bundle source"',
        first_snapshot_guard,
    )
    first_snapshot_else = script.index("\nelse\n", first_snapshot)
    assert rebase < absence_gate < fail_closed_arm < stop < quarantine
    assert stop < quarantined_state < treatment_quiesce < rebase_first_install
    rebase_stop = script[stop:quarantine]
    assert '"${APP_EXPECTED_IDENTITY_ARGS[@]}"' in rebase_stop
    assert rebase_first_install < first_snapshot_guard < first_snapshot < first_snapshot_else
    assert "_EXISTING_APP_SP_CLIENT_ID" not in script[first_snapshot_guard:first_snapshot]

    candidate = script.index(
        'deploy_app_snapshot "activate App snapshot on the runtime-owned Gateway before retirement"'
    )
    negative = script.index(
        'step "prove agent-runtime negative authorization boundary before positive App probes"',
        candidate,
    )
    probe_access = script.index("--mode probe", negative)
    positive = script.index("tools.verify_deployed_app_contract", probe_access)
    capture = script.index('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"', positive)
    runtime_access = script.index("--mode runtime", capture)
    retire = script.index(
        'step "retire pinned blue runtime resources only after every green release gate"',
        runtime_access,
    )
    assert candidate < negative < probe_access < positive < capture < runtime_access < retire
    probe_block = script[negative:positive]
    assert (
        "mint_m2m_token MIP_BEARER_TOKEN \\\n"
        "      DATABRICKS_RELEASE_PROBE_CLIENT_ID DATABRICKS_RELEASE_PROBE_CLIENT_SECRET" in script
    )
    assert "mint_app_automation_tokens" in probe_block


def test_cached_agentic_env_cannot_override_deployment_sync_contract(tmp_path: Path) -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    cache_source = script.index('. "$AGENTIC_ENV_CACHE"')
    production_restore = script.index(
        'restore_deployment_sync_contract "$AGENTIC_ENV_CACHE"', cache_source
    )
    first_snapshot_guard = script.index(
        'if [[ "$APP_UPGRADE_STATE" == "first_install" ]]; then', production_restore
    )
    assert cache_source < production_restore < first_snapshot_guard

    harness = tmp_path / "sync-contract-harness.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            {_shell_function("restore_deployment_sync_contract")}
            DEPLOYMENT_SYNC_CATALOG=reviewed_catalog
            DEPLOYMENT_SYNC_SCHEMA=reviewed_schema
            DEPLOYMENT_SYNC_TABLES=one,two,three
            MIP_LAKEBASE_SYNC_CATALOG=stale_catalog
            MIP_LAKEBASE_SYNC_SCHEMA=stale_schema
            MIP_LAKEBASE_SYNC_TABLES=stale_one
            restore_deployment_sync_contract .databricks/mip-agentic.env
            printf '%s\n' \\
              "$MIP_LAKEBASE_SYNC_CATALOG" \\
              "$MIP_LAKEBASE_SYNC_SCHEMA" \\
              "$MIP_LAKEBASE_SYNC_TABLES"
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(harness)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "[deploy] ignoring stale Lakebase Sync names from "
        ".databricks/mip-agentic.env; deployment controls are authoritative",
        "reviewed_catalog",
        "reviewed_schema",
        "one,two,three",
    ]


def test_rebase_first_install_waits_for_stopped_existing_app_before_snapshot(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    guard = script.index('if [[ "$APP_UPGRADE_STATE" == "first_install" ]]; then')
    wait = script.index("wait_for_app_deployable", guard)
    snapshot = script.index(
        'deploy_app_snapshot "deploy first-install Databricks App snapshot from uploaded bundle source"',
        wait,
    )
    branch_else = script.index("\nelse\n", snapshot)
    assert guard < wait < snapshot < branch_else

    calls = tmp_path / "first-snapshot.log"
    harness = tmp_path / "first-snapshot.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            APP_UPGRADE_STATE=first_install
            DRY_RUN=0
            _EXISTING_APP_SP_CLIENT_ID=existing-app-client
            wait_for_app_deployable() {{ printf 'wait\n' >> {shlex.quote(str(calls))}; }}
            deploy_app_snapshot() {{ printf 'deploy:%s\n' "$1" >> {shlex.quote(str(calls))}; }}
            step() {{ printf 'preserve\n' >> {shlex.quote(str(calls))}; }}
            {_first_snapshot_decision_block()}
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "wait",
        "deploy:deploy first-install Databricks App snapshot from uploaded bundle source",
    ]


def test_explicit_unsigned_candidate_rollback_delegates_to_quarantine_aware_restore(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "unsigned-rollback.log"
    harness = tmp_path / "unsigned-rollback.sh"
    harness.write_text(
        textwrap.dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            DRY_RUN=0
            APP_NAME=mip-app
            APP_SIGNED_BLUE_AVAILABLE=1
            APP_UPGRADE_STATE=green_unverified
            APP_EXPECTED_IDENTITY_ARGS=(
              --expected-app-id app-object-id
              --expected-client-id app-client-id
              --expected-scim-id app-scim-id
            )
            TREATMENT_RUNTIME_QUIESCED=0
            PYTHON=/nonexistent/python
            step() {{ printf 'step:%s\n' "$1" >> {shlex.quote(str(calls))}; }}
            converge_app_treatment_access() {{ printf 'treatment:%s\n' "$1" >> {shlex.quote(str(calls))}; }}
            restore_signed_blue_while_quiesced() {{ printf 'restore-helper\n' >> {shlex.quote(str(calls))}; }}
            run() {{
              printf 'run:%s\n' "$*" >> {shlex.quote(str(calls))}
              if declare -F "$1" >/dev/null; then "$@"; fi
            }}
            {_unsigned_candidate_rollback_block()}
            """
        ),
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    log = calls.read_text(encoding="utf-8")
    assert log.index("stop the unproven candidate") < log.index("treatment:quiesce")
    assert log.index("treatment:quiesce") < log.index("restore-helper")
    stop_call = next(line for line in log.splitlines() if "stop_app_fail_closed" in line)
    assert "--expected-app-id app-object-id" in stop_call
    assert "--expected-client-id app-client-id" in stop_call
    assert "--expected-scim-id app-scim-id" in stop_call
    assert "mint_m2m_token" not in log


def test_deploy_uses_dedicated_verifier_for_gateway_proof_writes() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    bundle = BUNDLE_CONFIG.read_text(encoding="utf-8")

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
    assert "mip_lakebase_migrate" in script
    assert 'export MIP_AI_GATEWAY_VERIFIER_CLIENT_ID="$DATABRICKS_VERIFIER_CLIENT_ID"' in script
    assert (
        'export BUNDLE_VAR_ai_gateway_verifier_client_id="$DATABRICKS_VERIFIER_CLIENT_ID"' in script
    )
    assert "ai_gateway_verifier_client_id:" in bundle
    assert '"--ai-gateway-verifier-client-id=${var.ai_gateway_verifier_client_id}"' in bundle
    assert '"--require-ai-gateway-verifier"' in bundle
    verifier_export = script.index("export BUNDLE_VAR_ai_gateway_verifier_client_id=")
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    assert verifier_export < bundle_apply < migration
    boundary = script.index(
        'step "prove verifier effective authorization boundary before exact Gateway proof"'
    )
    proof = script.index('step "verify AI Gateway exact inference-row proof')
    assert boundary < proof
    assert "prove_exact_verifier_boundary" in script[boundary:proof]
    verifier_boundary_helper = _shell_function("prove_exact_verifier_boundary")
    assert "-m tools.databricks.verify_verifier_identity_boundary" in verifier_boundary_helper
    assert '--protected-service-principal-id "$APP_SP_SCIM_ID"' in verifier_boundary_helper
    assert "DATABRICKS_ACCOUNT_ID: ${{ secrets.DATABRICKS_ACCOUNT_ID }}" in workflow


def test_deploy_uses_isolated_identity_for_agent_resource_ownership() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    script = _deploy_contract_text()

    for secret in (
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET",
    ):
        assert f"{secret}: ${{{{ secrets.{secret} }}}}" in workflow
        assert secret in script
    runtime_block = script[
        script.index(
            'step "provision the managed Supervisor under the dedicated agent-runtime'
        ) : script.index("AI_GATEWAY_GRANTS_READY=1")
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
    assert "-m tools.verify_deployed_app_contract" in script[activate:retire]
    assert '--deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:' in script[activate:retire]
    assert "-m tools.verify_app_agent_green_path" in script[activate:retire]
    green_probe = script[
        script.index("-m tools.verify_app_agent_green_path", activate) : script.index(
            "read independent governed fn_build_cohort expectation", activate
        )
    ]
    assert '--deployment-lease-id "${MIP_APP_DEPLOYMENT_LEASE_ID:' in green_probe
    assert "tools.databricks.verify_hosted_agent_tool_execution" in script[activate:retire]
    assert script.index("read independent governed fn_build_cohort expectation") < retire
    assert script.index("tools.databricks.verify_ai_gateway_exact_proof") < retire
    assert script.index("run live Agent Evaluation") < retire
    assert script.index("FINAL_APP_PROVEN=1") < retire
    assert "tools.databricks.verify_agent_runtime_identity_boundary" in runtime_block
    assert runtime_block.count("tools.databricks.audit_global_m2m_access") >= 2
    gateway_provision = runtime_block.index(
        'step "provision the governed outer Gateway under agent-runtime authority"'
    )
    proxy_reaudit = runtime_block.index(
        'step "re-audit the Supervisor proxy caller after Gateway provisioning"'
    )
    proxy_uc_audit = runtime_block.index(
        'step "prove dual-authority agent-proxy Unity Catalog boundary"'
    )
    proxy_identity_boundary = runtime_block.index(
        'step "prove agent-proxy target query and negative authorization boundary before cutover"'
    )
    assert gateway_provision < proxy_reaudit < proxy_uc_audit < proxy_identity_boundary
    assert (
        "tools.databricks.verify_agent_proxy_uc_boundary_dual_authority"
        in runtime_block[proxy_reaudit : proxy_uc_audit + 500]
    )
    assert (
        "tools.databricks.verify_agent_proxy_identity_boundary"
        in runtime_block[proxy_identity_boundary : proxy_identity_boundary + 1200]
    )
    pre_cutover_proxy_block = runtime_block[
        proxy_identity_boundary : runtime_block.index(
            "revoke_agent_runtime_bootstrap_grants",
            proxy_identity_boundary,
        )
    ]
    assert "--allow-attested-app-401" in pre_cutover_proxy_block
    assert "--allow-attested-stopped-app-503" in pre_cutover_proxy_block
    assert "--supervisor-endpoint" in pre_cutover_proxy_block
    proxy_uc_block = runtime_block[proxy_uc_audit : proxy_uc_audit + 700]
    assert "run_with_account_identity" in proxy_uc_block
    assert "run_with_proof_signing_authority" in proxy_uc_block
    assert "run_with_agent_proxy_credentials" in proxy_uc_block
    assert "--supervisor-id" in proxy_uc_block
    assert "--supervisor-endpoint-id" in proxy_uc_block
    assert "--genie-space-id" in proxy_uc_block
    runtime_uc_audit = runtime_block.index(
        'step "prove dual-authority agent-runtime UC boundary before cutover"'
    )
    runtime_uc_block = runtime_block[runtime_uc_audit : runtime_uc_audit + 900]
    assert "run_with_account_identity" in runtime_uc_block
    assert "run_with_proof_signing_authority" in runtime_uc_block
    assert "run_with_agent_runtime_credentials" in runtime_uc_block
    assert "--expected-serving-permission CAN_MANAGE" in runtime_block
    assert "--expected-serving-permission CAN_QUERY" in runtime_block
    assert '--genie-space-id "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}"' in runtime_block
    assert '--serving-endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT"' in runtime_block
    assert '--serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"' in runtime_block
    final_audit = script.index(
        'step "re-audit final agent-runtime global access after blue retirement"'
    )
    assert retire < final_audit
    final_proxy_audit = script.index(
        'step "re-audit final Supervisor proxy caller access after blue retirement"'
    )
    final_proxy_uc_audit = script.index(
        'step "re-prove final dual-authority agent-proxy Unity Catalog boundary"'
    )
    final_proxy_identity_boundary = script.index(
        'step "re-prove final agent-proxy target query and negative boundary after blue retirement"'
    )
    proxy_secret_cleanup = script.index(
        'step "remove retired Supervisor proxy OAuth credentials and secret versions"'
    )
    assert (
        final_audit
        < final_proxy_audit
        < final_proxy_uc_audit
        < final_proxy_identity_boundary
        < proxy_secret_cleanup
    )
    assert (
        "tools.databricks.verify_agent_proxy_uc_boundary_dual_authority"
        in script[final_proxy_audit:proxy_secret_cleanup]
    )
    final_proxy_uc_block = script[final_proxy_uc_audit:final_proxy_identity_boundary]
    assert "run_with_account_identity" in final_proxy_uc_block
    assert "run_with_proof_signing_authority" in final_proxy_uc_block
    assert "run_with_agent_proxy_credentials" in final_proxy_uc_block
    assert "--supervisor-id" in final_proxy_uc_block
    assert "--supervisor-endpoint-id" in final_proxy_uc_block
    assert "--genie-space-id" in final_proxy_uc_block
    assert (
        "tools.databricks.verify_agent_proxy_identity_boundary"
        in script[final_proxy_identity_boundary:proxy_secret_cleanup]
    )
    assert "--allow-attested-app-401" in script[final_proxy_identity_boundary:proxy_secret_cleanup]
    assert (
        "--allow-attested-stopped-app-503"
        not in script[final_proxy_identity_boundary:proxy_secret_cleanup]
    )
    assert "--supervisor-endpoint" in script[final_proxy_identity_boundary:proxy_secret_cleanup]
    assert script.count("--allow-attested-app-401") == 5
    assert script.count("--allow-attested-stopped-app-503") == 1
    assert "--allow-stopped-app-401" not in script
    runtime_identity_boundary = runtime_block.index(
        "tools.databricks.verify_agent_runtime_identity_boundary"
    )
    runtime_identity_block = runtime_block[
        runtime_block.rfind(
            "run_with_agent_runtime_credentials",
            0,
            runtime_identity_boundary,
        ) : runtime_block.index(
            'step "grant only the dedicated release probe temporary candidate access"',
            runtime_identity_boundary,
        )
    ]
    assert "run_with_agent_runtime_credentials" in runtime_identity_block
    assert "--allow-attested-app-401" in runtime_identity_block
    verifier_identity_block = _shell_function("prove_exact_verifier_boundary")
    assert "run_with_verifier_credentials" in verifier_identity_block
    assert "--allow-attested-app-401" in verifier_identity_block
    cleanup_block = script[final_proxy_identity_boundary : proxy_secret_cleanup + 700]
    assert "--cleanup-signed-blue" in cleanup_block
    assert "--signed-blue-credential-id" in cleanup_block
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
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
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

    bundle_create = script.index(
        'step "deploy non-App bundle resources without activating an App candidate"'
    )
    app_create = script.index(
        'step "create stopped Databricks App with resolved resource bindings and no source deployment"'
    )
    app_bind = script.index(
        'step "bind the stopped source-free App into bundle deployment state"',
        app_create,
    )
    app_identity = script.index(
        'APP_SP_SCIM_ID="$(printf',
        app_bind,
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

    bootstrap = script[bundle_create:app_identity]
    expected_app_create = "\n".join(
        (
            'run_json_to_file "$APP_CREATE_RESULT" databricks apps create \\',
            '    --json "@$FIRST_INSTALL_MARKED_PAYLOAD"',
        )
    )
    assert expected_app_create in bootstrap
    assert 'databricks apps create "$_GRANTS_APP_NAME"' not in bootstrap
    assert "--no-compute" in bootstrap
    assert "tools.databricks.app_resource_bindings build" in bootstrap
    assert "--require-stopped-without-deployment" in bootstrap
    assert "tools.databricks.bundle_env deployment bind" in bootstrap
    assert 'mip_app "$_GRANTS_APP_NAME"' in bootstrap
    assert bundle_create < app_create < app_bind < app_identity < grants_start < verifier_start
    for role, client_id in (
        ("normal", "DATABRICKS_CLIENT_ID"),
        ("operator2", "DATABRICKS_OPERATOR2_CLIENT_ID"),
        ("admin", "DATABRICKS_ADMIN_CLIENT_ID"),
        ("release_probe", "DATABRICKS_RELEASE_PROBE_CLIENT_ID"),
    ):
        assert f"--identity-role {role}" in grant_block
        assert f'--expected-application-id "${client_id}"' in grant_block
    assert grant_block.count('--app-name "$_GRANTS_APP_NAME"') == 4
    assert grant_block.count("--no-mint-secret") == 4
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
    app_create = script.index('run_json_to_file "$APP_CREATE_RESULT" databricks apps create')
    app_bind = script.index("tools.databricks.bundle_env deployment bind", app_create)
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    role_bootstrap = script.index(
        'step "bootstrap dedicated AI Gateway verifier Lakebase OAuth role"'
    )
    first_migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    assert (
        absent_or_converged
        < bundle_apply
        < app_create
        < app_bind
        < role_bootstrap
        < first_migration
        < quiesce
    )
    first_install_block = script[absent_or_converged:app_create]
    assert "tools.databricks.ensure_campaign_treatment_table" in first_install_block
    assert "--allow-absent" in first_install_block
    bootstrap_block = script[role_bootstrap:first_migration]
    assert "-m tools.databricks.provision_m2m_oauth" in bootstrap_block
    assert "--identity-role verifier" in bootstrap_block
    assert '--expected-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in bootstrap_block
    assert "--no-mint-secret" in bootstrap_block
    assert "--gateway-endpoint" not in bootstrap_block
    assert "--warehouse-id" not in bootstrap_block


def test_full_lakebase_migration_never_runs_after_app_snapshot_activation() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    activation = script.index(
        'deploy_app_snapshot "activate App snapshot on the runtime-owned Gateway before retirement"'
    )

    assert migration < activation
    assert "jobs/lakebase_migrate.py" not in script[activation:]
    assert (
        script.count('run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"')
        == 1
    )


def test_first_install_never_uses_an_app_inclusive_bundle_deploy_before_migration() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    app_create = script.index('run_json_to_file "$APP_CREATE_RESULT" databricks apps create')
    identity_claim = script.index("tools.databricks.app_first_install_journal claim", app_create)
    app_bind = script.index("tools.databricks.bundle_env deployment bind", app_create)
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    post_migration_constraints = script.index(
        "tools.databricks.ensure_campaign_treatment_table",
        migration,
    )
    first_snapshot = script.index(
        'deploy_app_snapshot "deploy first-install Databricks App snapshot from uploaded bundle source"'
    )

    bundle_line_end = script.index("\n", bundle_apply)
    bundle_line = script[bundle_apply:bundle_line_end]
    assert '"${BUNDLE_NON_APP_ARGS[@]}"' in bundle_line
    assert 'kind != "apps"' in script[:bundle_apply]
    assert "--no-compute" in script[app_create:app_bind]
    assert '--json "@$FIRST_INSTALL_MARKED_PAYLOAD"' in script[app_create:app_bind]
    assert "tools.databricks.app_resource_bindings verify" in script[bundle_apply:app_bind]
    assert app_create < identity_claim < app_bind
    assert "deploy full bundle for first App creation" not in script
    assert bundle_apply < app_create < app_bind < migration < post_migration_constraints
    assert post_migration_constraints < first_snapshot


def test_failed_first_install_uses_signed_journal_for_exact_cleanup() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    cleanup = script.index("refresh_first_install_journal_status()")
    trap = script.index("restore_rendered_sql_fail_closed()", cleanup)
    capture = script.index("capture_last_good_app", trap)
    cleanup_block = script[cleanup:trap]

    assert '[[ "$DRY_RUN" -eq 0 && "$FIRST_INSTALL_APP_CREATED" -eq 1 ]]' in cleanup_block
    status = cleanup_block.index("tools.databricks.app_first_install_journal status")
    unbind = cleanup_block.index("tools.databricks.bundle_env deployment unbind")
    delete = cleanup_block.index("tools.databricks.app_first_install_journal delete")
    assert status < unbind < delete
    assert "databricks apps delete" not in cleanup_block
    assert "tools.databricks.app_first_install_journal clear-absent" not in cleanup_block
    assert cleanup_block.count('--rollback-scope "$APP_ROLLBACK_SECRET_SCOPE"') == 3
    assert cleanup_block.count('--lakebase-instance "$MIP_LAKEBASE_INSTANCE"') == 4
    recovery = cleanup_block.index("recover_journaled_first_install_lakebase_bootstrap")
    assert recovery < unbind < delete
    assert "refusing API deletion" in cleanup_block
    assert script.index("cleanup_failed_first_install_app", trap) < capture


def test_first_install_status_decodes_shell_quoted_empty_identity(
    tmp_path: Path,
) -> None:
    fake_python = tmp_path / "first-install-empty-status.sh"
    fake_python.write_text(
        """#!/usr/bin/env bash
while (( $# )); do
  if [[ "$1" == --out-env && $# -ge 2 ]]; then
    out_env="$2"
    break
  fi
  shift
done
{
  printf '%s\n' MIP_FIRST_INSTALL_JOURNAL_STATUS=absent
  printf '%s\n' "MIP_FIRST_INSTALL_APP_ID=''"
  printf '%s\n' "MIP_FIRST_INSTALL_APP_CLIENT_ID=''"
  printf '%s\n' "MIP_FIRST_INSTALL_APP_SCIM_ID=''"
} > "$out_env"
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "first-install-empty-status-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
PYTHON={shlex.quote(str(fake_python))}
APP_FAIL_CLOSED_NAME=mip-app
MIP_APP_DEPLOYMENT_LEASE_ID=lease-id
SOURCE_GIT_SHA={'a' * 40}
APP_ROLLBACK_SECRET_SCOPE=mip-app-rollback
MIP_LAKEBASE_INSTANCE=mip-lakebase
run_with_proof_signing_authority() {{ "$@"; }}
{_first_install_cleanup_block()}
refresh_first_install_journal_status
printf '%s|%s|%s|%s\n' \
  "$FIRST_INSTALL_JOURNAL_STATUS" "$FIRST_INSTALL_APP_ID" \
  "$FIRST_INSTALL_APP_CLIENT_ID" "$FIRST_INSTALL_APP_SCIM_ID"
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "absent|||\n"


def test_first_install_bind_arms_ambiguous_cleanup_before_remote_mutation() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    create = script.index('run_json_to_file "$APP_CREATE_RESULT" databricks apps create')
    arm = script.index("FIRST_INSTALL_APP_BOUND=1", create)
    bind = script.index("tools.databricks.bundle_env deployment bind", create)

    assert create < arm < bind


def test_signed_capture_disarms_first_install_deletion_before_journal_retirement() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    finalizer = script.index("finalize_signed_first_install_capture()")
    finalizer_end = script.index("refresh_first_install_journal_status()", finalizer)
    finalizer_block = script[finalizer:finalizer_end]
    capture = script.index('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"')
    finalize_call = script.index("finalize_signed_first_install_capture", capture)
    disarm = finalizer_block.index("FIRST_INSTALL_APP_CREATED=0")
    captured_state = finalizer_block.index('APP_UPGRADE_STATE="green_captured_cleanup_pending"')
    complete = finalizer_block.index("tools.databricks.app_first_install_journal complete")

    assert disarm < captured_state < complete
    assert capture < finalize_call


def test_signed_capture_retirement_failure_preserves_captured_app_in_exit_trap(
    tmp_path: Path,
) -> None:
    result, calls = _run_signed_capture_retirement_failure_harness(tmp_path)

    assert result.returncode == 1
    assert "app_first_install_journal complete" in calls
    assert "stop:green_captured_cleanup_pending" in calls
    assert "app_first_install_journal delete" not in calls
    assert "bundle_env deployment unbind" not in calls


def test_captured_cleanup_failure_stops_app_and_retains_lease(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "captured-cleanup-failure.log"
    fake_python = tmp_path / "captured-cleanup-failure-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "captured-cleanup-failure.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_FAIL_CLOSED_ARMED=1
APP_FAIL_CLOSED_NAME=mip-app
APP_UPGRADE_STATE=green_captured_cleanup_pending
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
APP_DEPLOYMENT_LEASE_ID=lease-id
_GRANTS_APP_NAME=mip-app
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
FIRST_INSTALL_APP_CREATED=0
LAKEBASE_RUNTIME_ACCESS_PROVEN=1
REVIEWED_FUNCTION_GRANTS_PROVEN=1
PYTHON={shlex.quote(str(fake_python))}
RED=""
YLW=""
RST=""
converge_green_only_app_access() {{ return 1; }}
stop_and_quiesce_unproven_app() {{
  printf 'stopped-and-quiesced\\n' >> {shlex.quote(str(calls))}
  return 0
}}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
cleanup_failed_first_install_app() {{ return 0; }}
compensate_preactivation_app_acl() {{ return 0; }}
compensate_agent_proxy_access() {{ return 0; }}
compensate_verifier_gateway_access() {{ return 0; }}
revoke_agent_runtime_bootstrap_grants() {{ return 0; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("stop_app_after_failed_deploy")}
{_deploy_exit_trap_block()}
false
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 90
    assert "retaining the signed deployment lease" in result.stderr
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert observed == ["stopped-and-quiesced"]
    assert all("app_deployment_lease release" not in call for call in observed)


def test_exact_unsigned_first_install_cleanup_converges_through_signed_helper(
    tmp_path: Path,
) -> None:
    result, calls = _run_first_install_cleanup_harness(
        tmp_path,
        journal_status="recover",
        bound=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls.index("app_first_install_journal status") < calls.index(
        "converge_lakebase_oauth_role"
    )
    assert calls.index("converge_lakebase_oauth_role") < calls.index("bundle_env deployment unbind")
    assert calls.index("bundle_env deployment unbind") < calls.index(
        "app_first_install_journal delete"
    )


def test_first_install_cleanup_refuses_signed_or_replaced_state_before_unbind(
    tmp_path: Path,
) -> None:
    result, calls = _run_first_install_cleanup_harness(
        tmp_path,
        journal_status="signed",
        bound=True,
    )

    assert result.returncode == 1
    assert "not authorized for journal state signed" in result.stderr
    assert "bundle_env deployment unbind" not in calls
    assert "app_first_install_journal delete" not in calls


def test_unclaimed_first_install_identity_is_manual_and_never_auto_deleted(
    tmp_path: Path,
) -> None:
    result, calls = _run_first_install_cleanup_harness(
        tmp_path,
        journal_status="unclaimed",
        bound=True,
    )

    assert result.returncode == 1
    assert "not authorized for journal state unclaimed" in result.stderr
    assert "bundle_env deployment unbind" not in calls
    assert "app_first_install_journal delete" not in calls


def test_ambiguous_first_install_unbind_retains_app_and_journal_for_retry(
    tmp_path: Path,
) -> None:
    result, calls = _run_first_install_cleanup_harness(
        tmp_path,
        journal_status="recover",
        bound=True,
        unbind_result=1,
    )

    assert result.returncode == 1
    assert "refusing API deletion" in result.stderr
    assert "bundle_env deployment unbind" in calls
    assert "app_first_install_journal delete" not in calls


def test_failed_first_install_role_recovery_retains_app_binding_and_journal(
    tmp_path: Path,
) -> None:
    result, calls = _run_first_install_cleanup_harness(
        tmp_path,
        journal_status="recover",
        bound=True,
        role_recovery_result=23,
    )

    assert result.returncode == 1
    assert "retaining App and journal" in result.stderr
    assert "converge_lakebase_oauth_role" in calls
    assert "bundle_env deployment unbind" not in calls
    assert "app_first_install_journal delete" not in calls


def test_committed_app_delete_can_retire_orphaned_first_install_journal(
    tmp_path: Path,
) -> None:
    result, calls = _run_first_install_cleanup_harness(
        tmp_path,
        journal_status="orphan_claimed",
        bound=False,
    )

    assert result.returncode == 0, result.stderr
    assert "bundle_env deployment unbind" not in calls
    assert "app_first_install_journal delete" in calls


def test_initial_retry_routes_claimed_and_unclaimed_absence_separately() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    status = script.index(
        'step "read signed first-install journal at the immediate recovery boundary"'
    )
    unclaimed = script.index('FIRST_INSTALL_JOURNAL_STATUS" == "orphan_unclaimed"', status)
    clear = script.index("tools.databricks.app_first_install_journal clear-absent", unclaimed)
    claimed = script.index('FIRST_INSTALL_JOURNAL_STATUS" == "orphan_claimed"', clear)
    journal_role_recovery = script.index(
        'step "recover interrupted Lakebase bootstrap for journaled App identity"',
        status,
    )
    retire = script.index("tools.databricks.app_first_install_journal delete", claimed)

    assert status < journal_role_recovery < unclaimed < clear < claimed < retire
    recovery_block = script[journal_role_recovery:unclaimed]
    assert "recover_journaled_first_install_lakebase_bootstrap" in recovery_block
    helper = _shell_function("recover_journaled_first_install_lakebase_bootstrap")
    assert '--application-id "$FIRST_INSTALL_APP_CLIENT_ID"' in helper
    assert "run_with_lakebase_bootstrap_authority" in helper


def test_local_deploy_loads_complete_proof_verification_key_registry() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert "dotenv_value MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY" in script
    assert "dotenv_value MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS" in script
    assert "dotenv_value MIP_GATEWAY_MODEL_ATTESTATION_PREVIOUS_VERIFY_KEY" in script
    assert (
        'export MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS="$verifier_historical_keys"' in script
    )


def test_agent_proxy_credential_is_atomic_and_reproved_after_cleanup() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")
    nightly = NIGHTLY.read_text(encoding="utf-8")
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert (
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE: "
        "${{ secrets.DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE }}"
    ) in workflow
    assert "secrets.DATABRICKS_AGENT_PROXY_CLIENT_SECRET" not in workflow
    for legacy_secret in (
        "secrets.DATABRICKS_AGENT_PROXY_CLIENT_ID",
        "secrets.DATABRICKS_AGENT_PROXY_CREDENTIAL_ID",
    ):
        assert legacy_secret not in workflow
        assert legacy_secret not in nightly
    assert workflow.count("secrets.DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE") == 2
    assert nightly.count("secrets.DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE") == 2
    assert (
        workflow.count("python -m tools.databricks.agent_proxy_credential_bundle public-fields")
        == 1
    )
    assert (
        nightly.count("python -m tools.databricks.agent_proxy_credential_bundle public-fields") == 2
    )
    assert "tools.databricks.agent_proxy_credential_bundle all-fields" in script
    live_resolution = script[
        script.index(
            'if [[ "$DRY_RUN" -eq 1 ]]; then', script.index("for _M2M_NAME in")
        ) : script.index('_GRANTS_APP_NAME="${MIP_APP_NAME:-mip-app}"')
    ]
    assert 'DATABRICKS_AGENT_PROXY_CLIENT_ID=""' in live_resolution
    assert 'DATABRICKS_AGENT_PROXY_CREDENTIAL_ID=""' in live_resolution
    assert 'DATABRICKS_AGENT_PROXY_CLIENT_SECRET=""' in live_resolution
    assert "--merge-out-env" in script
    cleanup = script.index(
        "--cleanup-signed-blue",
        script.index(
            'step "remove retired Supervisor proxy OAuth credentials and secret versions"'
        ),
    )
    post_cleanup = script.index(
        "prove exact green Gateway inference after proxy credential retirement",
        cleanup,
    )
    assert 'MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS=""' in script
    assert "APP_SIGNED_BLUE_AVAILABLE" in script[cleanup - 1800 : cleanup]
    assert "--signed-blue-credential-id" in script[cleanup - 1800 : cleanup]
    assert 'MIP_AGENT_PROXY_SECRET_SCOPE" != "${MIP_APP_NAME}-agent-proxy"' in script
    assert "tools.verify_app_agent_green_path" in script[post_cleanup:]
    assert "tools.databricks.verify_hosted_agent_tool_execution" in script[post_cleanup:]


def test_expired_lease_recovery_uses_durable_signed_lease_root() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    recovery = script.index("tools.databricks.app_deployment_lease recovery-root")
    acquire = script.index("tools.databricks.app_deployment_lease acquire", recovery)

    assert recovery < acquire
    assert "MIP_APP_DEPLOYMENT_RECOVERY_ROOT" in script[recovery:acquire]
    assert "MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES" in script
    assert "app_first_install_journal takeover-lease" not in script
    assert 'APP_LEASE_RECOVERY_ENV=""' in script
    assert 'rm -f "$APP_LEASE_RECOVERY_ENV"' in script
    assert "FIRST_INSTALL_TAKEOVER_ENV" not in script


def test_reviewed_foreign_catalog_remediation_uses_stopped_signed_blue_window() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    stop = script.index(
        'step "stop the exact App before foreign-catalog recovery or fresh remediation"'
    )
    remediation = script.index("run_foreign_catalog_binding_remediation", stop)
    preflight = script.index(
        'step "preflight remediated agent-runtime foreign UC access while App is stopped"',
        remediation,
    )
    signed_blue = script.index(
        'step "prove or reconcile the signed last-good App before non-App mutations"',
        preflight,
    )
    lakebase = script.index(
        'step "recover interrupted verifier Lakebase role bootstrap after UC preflight"',
        signed_blue,
    )

    assert stop < remediation < preflight < signed_blue < lakebase
    helper = _shell_function("run_foreign_catalog_binding_remediation")
    assert "converge_foreign_catalog_workspace_bindings" in helper
    assert "recover-local" in helper
    assert "reauthorize" in helper
    assert '"$action"' in helper
    assert "verify" in helper


def _run_foreign_catalog_helper_harness(
    tmp_path: Path,
    *,
    recovery_candidates: str,
    fail_action: str = "",
    fail_code: int = 0,
    recover_code: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "foreign-catalog-helper.log"
    fake_python = tmp_path / "foreign-catalog-helper-python.sh"
    fake_python.write_text(
        """#!/usr/bin/env bash
action=""
for value in "$@"; do
  case "$value" in
    recover-local|reauthorize|snapshot|apply|resume|verify) action="$value"; break ;;
  esac
done
printf '%s\n' "$action" >> "$CALLS"
if [[ "$action" == recover-local ]]; then
  exit "$RECOVER_CODE"
fi
if [[ -n "$FAIL_ACTION" && "$action" == "$FAIL_ACTION" ]]; then
  exit "$FAIL_CODE"
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "foreign-catalog-helper.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
PYTHON={shlex.quote(str(fake_python))}
_GRANTS_APP_NAME=mip-app
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DEPLOY_INVENTORY_PRINCIPAL=deployer@example.com
DATABRICKS_ACCOUNT_ID=account-id
DATABRICKS_ACCOUNT_CLIENT_ID=account-client
MIP_DEFAULT_CATALOG=mip
MIP_APP_DEPLOYMENT_LEASE_ID=current-lease
MIP_APP_DEPLOYMENT_RECOVERY_CANDIDATES={shlex.quote(recovery_candidates)}
MIP_UC_FOREIGN_CATALOG_BINDING_POLICY='{{"version":1,"catalogs":{{}}}}'
RED=""
RST=""
CALLS={shlex.quote(str(calls))}
RECOVER_CODE={recover_code}
FAIL_ACTION={shlex.quote(fail_action)}
FAIL_CODE={fail_code}
export CALLS RECOVER_CODE FAIL_ACTION FAIL_CODE
step() {{ :; }}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("run_foreign_catalog_binding_remediation")}
run_foreign_catalog_binding_remediation
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)],
        text=True,
        capture_output=True,
        check=False,
    )
    recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, recorded


@pytest.mark.parametrize(
    ("recovery_candidates", "fail_action", "fail_code", "recover_code"),
    [
        ("old-lease", "recover-local", 71, 71),
        ("old-lease", "reauthorize", 72, 0),
        ("", "snapshot", 73, 0),
        ("old-lease", "resume", 74, 0),
        ("", "apply", 75, 0),
        ("", "verify", 76, 0),
    ],
)
def test_foreign_catalog_helper_propagates_every_child_failure(
    tmp_path: Path,
    recovery_candidates: str,
    fail_action: str,
    fail_code: int,
    recover_code: int,
) -> None:
    result, calls = _run_foreign_catalog_helper_harness(
        tmp_path,
        recovery_candidates=recovery_candidates,
        fail_action=fail_action,
        fail_code=fail_code,
        recover_code=recover_code,
    )

    assert result.returncode == fail_code, (result.stdout, result.stderr, calls)
    if fail_action in {"resume", "apply"}:
        assert "verify" not in calls


def test_foreign_catalog_helper_searches_full_lineage_before_absence(
    tmp_path: Path,
) -> None:
    result, calls = _run_foreign_catalog_helper_harness(
        tmp_path,
        recovery_candidates="newest,root",
        recover_code=3,
    )

    assert result.returncode == 0
    assert calls == [
        "recover-local",
        "recover-local",
        "snapshot",
        "apply",
        "verify",
    ]


def test_foreign_catalog_remediation_rejects_absent_or_unstable_app() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    status = script.index(
        'step "read signed first-install journal at the immediate recovery boundary"'
    )
    absent = script.index(
        "foreign-catalog remediation requires an existing identity-pinned App",
        status,
    )
    unstable = script.index(
        "foreign-catalog remediation refuses unstable first-install App state",
        absent,
    )
    remediation = script.index(
        'step "stop the exact App before foreign-catalog recovery or fresh remediation"',
        unstable,
    )

    assert status < absent < unstable < remediation


def test_first_install_creation_is_preceded_by_signed_durable_recovery_intent() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    inventory = script.index('_EXISTING_APPS_JSON="$(databricks apps list -o json)"')
    status = script.index(
        'step "read signed first-install journal at the immediate recovery boundary"',
        inventory,
    )
    recovery_function = script.index("recover_interrupted_first_install_app()")
    recovery_stop = script.index("tools.databricks.stop_app_fail_closed", recovery_function)
    recovery_quarantine = script.index(
        "tools.databricks.converge_app_release_access",
        recovery_stop,
    )
    recovery_quiesce = script.index(
        "tools.databricks.converge_campaign_treatment_access",
        recovery_quarantine,
    )
    recovery_delete = script.index(
        "tools.databricks.app_first_install_journal delete",
        recovery_quiesce,
    )
    recover = script.index('FIRST_INSTALL_JOURNAL_STATUS" == "recover"', status)
    recovery_call = script.index("recover_interrupted_first_install_app", recover)
    audit_recover = script.index(
        "tools.databricks.app_first_install_journal recover-claim",
        recover,
    )
    prepare = script.index("tools.databricks.app_first_install_journal prepare")
    create = script.index('run_json_to_file "$APP_CREATE_RESULT" databricks apps create', prepare)
    ambiguous_create_guard = script.index("FIRST_INSTALL_APP_CREATED=1", prepare)
    verify = script.index("tools.databricks.app_resource_bindings verify", create)
    claim = script.index("tools.databricks.app_first_install_journal claim", verify)
    bind = script.index("tools.databricks.bundle_env deployment bind", verify)
    capture = script.index(
        'capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"',
        bind,
    )
    complete = script.index("finalize_signed_first_install_capture", capture)

    assert (
        recovery_function < recovery_stop < recovery_quarantine < recovery_quiesce < recovery_delete
    )
    assert inventory < status < recover < recovery_call < audit_recover
    assert audit_recover < prepare < ambiguous_create_guard < create
    assert create < verify < claim < bind < capture < complete
    assert '--payload "$APP_RESOURCE_BINDING_PAYLOAD"' in script[prepare:create]
    assert '--out-payload "$FIRST_INSTALL_MARKED_PAYLOAD"' in script[prepare:create]
    assert '--json "@$FIRST_INSTALL_MARKED_PAYLOAD"' in script[create:verify]
    assert '--expected "$FIRST_INSTALL_MARKED_PAYLOAD"' in script[verify:bind]
    assert '--created-app "$APP_CREATE_RESULT"' in script[verify:bind]
    assert '"$FIRST_INSTALL_JOURNAL_STATUS" != "signed"' in script[status:prepare]


def test_acquired_deployment_lease_id_is_wired_into_exit_cleanup() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    source_lease = script.index('. "$APP_DEPLOYMENT_LEASE_ENV"')
    cleanup_guard = script.index('-n "${APP_DEPLOYMENT_LEASE_ID:-}"')
    cleanup_release = script.index('--lease-id "$APP_DEPLOYMENT_LEASE_ID"')

    assignment = script.index(
        'APP_DEPLOYMENT_LEASE_ID="${MIP_APP_DEPLOYMENT_LEASE_ID:',
        source_lease,
    )
    heartbeat = script.index('--lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"', assignment)
    writer = script.index(
        '--writer-application-id "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"',
        source_lease - 800,
    )
    gateway_step = script.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    gateway_app = script.index('--app-name "$_GRANTS_APP_NAME"', gateway_step)
    gateway_lease = script.index(
        '--deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"',
        gateway_step,
    )
    gateway_source = script.index('--deployment-source-git-sha "$SOURCE_GIT_SHA"', gateway_step)
    assert cleanup_guard < cleanup_release < source_lease < assignment < heartbeat
    assert writer < source_lease < gateway_step < gateway_app < gateway_lease < gateway_source


def test_every_mutating_agent_cutover_command_is_bound_to_exact_deployment_lease() -> None:
    script = _deploy_contract_text()
    captured_acl = _shell_function("converge_captured_app_gateway_acl")
    assert "app_deployment_lease.held_assertion" in captured_acl
    assert '"$MIP_APP_DEPLOYMENT_LEASE_ID"' in captured_acl
    assert '"$SOURCE_GIT_SHA"' in captured_acl
    segments = [
        script[
            script.index("refresh-journal-attestation") : script.index(
                'if [[ -s "$CUTOVER_JOURNAL_ENV_FILE" ]]',
                script.index("refresh-journal-attestation"),
            )
        ],
        script[
            script.index("AGENT_RUNTIME_PIN_ARGS=(") : script.index(
                'if [[ -n "${MIP_REPLACED_AGENT_SUPERVISOR_ID:-}" ]]',
                script.index("AGENT_RUNTIME_PIN_ARGS=("),
            )
        ],
        script[
            script.index("AGENT_RUNTIME_GREEN_ARGS=(") : script.index(
                'step "prove dual-authority agent-runtime UC boundary',
                script.index("AGENT_RUNTIME_GREEN_ARGS=("),
            )
        ],
        script[
            script.index("cutover_agent_runtime_supervisor finalize") : script.index(
                "cutover_agent_runtime_supervisor clear-journal"
            )
        ],
        script[
            script.index("cutover_agent_runtime_supervisor clear-journal") : script.index(
                'step "re-audit final agent-runtime global access'
            )
        ],
    ]

    for segment in segments:
        assert "--app-name" in segment
        assert '--deployment-lease-id "$MIP_APP_DEPLOYMENT_LEASE_ID"' in segment
        assert '--deployment-source-git-sha "$SOURCE_GIT_SHA"' in segment

    assert script.count("cutover_agent_runtime_supervisor export-journal") == 4


def test_every_cutover_journal_clear_proves_all_endpoint_group_principals() -> None:
    script = _deploy_contract_text()
    fragment = "tools.databricks.cutover_agent_runtime_supervisor clear-journal"
    starts = [match.start() for match in re.finditer(fragment, script)]

    assert len(starts) == 3
    for start in starts:
        tokens = _continued_command_tokens(script[start : start + 1200], fragment)
        expected = {
            "--app-application-id": "$APP_SP_CLIENT_ID",
            "--app-scim-id": "$APP_SP_SCIM_ID",
            "--verifier-application-id": "$DATABRICKS_VERIFIER_CLIENT_ID",
            "--verifier-scim-id": "$MIP_VERIFIER_SCIM_ID",
            "--proxy-application-id": "$DATABRICKS_AGENT_PROXY_CLIENT_ID",
        }
        for flag, value in expected.items():
            assert tokens.count(flag) == 1
            assert tokens[tokens.index(flag) + 1] == value


def test_normal_cutover_journal_clear_follows_every_final_boundary() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    retirement = script.index(
        'step "retire pinned blue runtime resources only after every green release gate"'
    )
    proxy_cleanup = script.index(
        'step "remove retired Supervisor proxy OAuth credentials and secret versions"',
        retirement,
    )
    runtime_audit = script.index(
        'step "re-audit final agent-runtime global access after blue retirement"',
        retirement,
    )
    verifier_audit = script.index(
        'step "re-audit final verifier global access after blue retirement"',
        retirement,
    )
    app_audit = script.index(
        'step "re-audit final App global serving access after blue retirement"',
        retirement,
    )
    clear = script.index(
        'step "clear the authenticated cutover journal after every final boundary proof"',
        retirement,
    )

    assert runtime_audit < proxy_cleanup < verifier_audit < app_audit < clear


def test_captured_cleanup_resumes_exact_signed_retirement_and_defers_journal_clear(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "captured-retirement-resume.log"
    fake_python = tmp_path / "captured-retirement-resume-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "captured-retirement-resume.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
APP_UPGRADE_STATE=green_captured_cleanup_pending
PYTHON={shlex.quote(str(fake_python))}
RED=""
RST=""
_GRANTS_APP_NAME=mip-app
MIP_APP_DEPLOYMENT_LEASE_ID=lease-id
SOURCE_GIT_SHA={'a' * 40}
MIP_DEFAULT_CATALOG=mip
GENIE_SPACE_ID=space-id
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=runtime-secret
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
MIP_VERIFIER_SCIM_ID=verifier-scim
MIP_AGENT_SUPERVISOR_ID=green-supervisor-id
MIP_AGENT_SUPERVISOR_ENDPOINT=green-supervisor
AGENT_RUNTIME_GREEN_ARGS=(--replacement-id green-supervisor-id --replacement-endpoint green-supervisor)
refresh_captured_cutover_journal() {{
  MIP_REPLACED_AGENT_SUPERVISOR_ID=old-supervisor-id
  MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT=old-supervisor
  MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID=old-supervisor-endpoint-id
  MIP_REPLACED_AGENT_SUPERVISOR_CREATOR=runtime-client
  MIP_REPLACED_AGENT_SUPERVISOR_CREATE_TIME=2026-07-20T00:00:00Z
  MIP_REPLACED_AGENT_GATEWAY_ENDPOINT=old-gateway
  MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID=old-gateway-id
  MIP_REPLACED_AGENT_GATEWAY_CREATOR=runtime-client
  MIP_REPLACED_AGENT_GATEWAY_DELETE_ALLOWED=1
}}
run_as_m2m_identity() {{ shift 3; "$@"; }}
{_shell_function("resume_captured_runtime_retirement")}
resume_captured_runtime_retirement
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert len(observed) == 2
    assert "cutover_agent_runtime_supervisor retire" in observed[0]
    assert "--old-id old-supervisor-id" in observed[0]
    assert "--old-endpoint-id old-supervisor-endpoint-id" in observed[0]
    assert "--old-gateway-endpoint old-gateway" in observed[0]
    assert "--old-gateway-endpoint-id old-gateway-id" in observed[0]
    assert "--old-gateway-delete-allowed" in observed[0]
    assert "cutover_agent_runtime_supervisor finalize" in observed[1]
    assert all("clear-journal" not in line for line in observed)


def test_partial_retirement_failure_reproves_survivors_and_keeps_journal(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "partial-retirement-reproof.log"
    harness = tmp_path / "partial-retirement-reproof.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
APP_UPGRADE_STATE=green_captured_cleanup_pending
CAPTURED_RUNTIME_RETIREMENT_COMPLETE=0
CAPTURED_APP_BOUNDARY_PROVEN=1
CAPTURED_PROXY_BOUNDARY_PROVEN=1
CAPTURED_VERIFIER_BOUNDARY_PROVEN=1
resume_captured_runtime_retirement() {{
  printf 'retire-failed\\n' >> {shlex.quote(str(calls))}
  return 1
}}
converge_green_only_app_access() {{
  CAPTURED_APP_BOUNDARY_PROVEN=1
  printf 'app-survivors-proved\\n' >> {shlex.quote(str(calls))}
}}
compensate_agent_proxy_access() {{
  CAPTURED_PROXY_BOUNDARY_PROVEN=1
  printf 'proxy-survivors-proved\\n' >> {shlex.quote(str(calls))}
}}
compensate_verifier_gateway_access() {{
  CAPTURED_VERIFIER_BOUNDARY_PROVEN=1
  printf 'verifier-survivors-proved\\n' >> {shlex.quote(str(calls))}
}}
{_shell_function("complete_captured_runtime_retirement_journal")}
complete_captured_runtime_retirement_journal
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "retire-failed",
        "app-survivors-proved",
        "proxy-survivors-proved",
        "verifier-survivors-proved",
    ]


def test_captured_retirement_clear_is_last_after_cleanup_and_reproof(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "captured-clear-last.log"
    fake_python = tmp_path / "captured-clear-last-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf 'python %s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "captured-clear-last.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
APP_UPGRADE_STATE=green_captured_cleanup_pending
CAPTURED_RUNTIME_RETIREMENT_COMPLETE=0
CAPTURED_APP_BOUNDARY_PROVEN=1
CAPTURED_PROXY_BOUNDARY_PROVEN=1
CAPTURED_VERIFIER_BOUNDARY_PROVEN=1
AGENT_PROXY_ACCESS_MUTATED=1
VERIFIER_GATEWAY_CUTOVER_MUTATED=1
MIP_APP_ROLLBACK_PROXY_CREDENTIAL_IDS=old-credential
MIP_AGENT_PROXY_SECRET_SCOPE=proxy-scope
APP_ROLLBACK_SECRET_SCOPE=rollback-scope
_GRANTS_APP_NAME=mip-app
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=runtime-secret
APP_SP_CLIENT_ID=app-client
APP_SP_SCIM_ID=app-scim-id
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
MIP_VERIFIER_SCIM_ID=verifier-scim-id
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
MIP_APP_DEPLOYMENT_LEASE_ID=lease-id
SOURCE_GIT_SHA={'a' * 40}
PYTHON={shlex.quote(str(fake_python))}
RED=""
RST=""
resume_captured_runtime_retirement() {{
  CAPTURED_RUNTIME_RETIREMENT_COMPLETE=1
  printf 'retire-finalize\\n' >> {shlex.quote(str(calls))}
}}
converge_green_only_app_access() {{
  CAPTURED_APP_BOUNDARY_PROVEN=1
  printf 'app-proof\\n' >> {shlex.quote(str(calls))}
}}
compensate_agent_proxy_access() {{
  CAPTURED_PROXY_BOUNDARY_PROVEN=1
  printf 'proxy-proof\\n' >> {shlex.quote(str(calls))}
}}
compensate_verifier_gateway_access() {{
  CAPTURED_VERIFIER_BOUNDARY_PROVEN=1
  printf 'verifier-proof\\n' >> {shlex.quote(str(calls))}
}}
run_with_proof_signing_authority() {{ "$@"; }}
run_with_agent_proxy_binding() {{ "$@"; }}
run_as_m2m_identity() {{ shift 3; "$@"; }}
{_shell_function("complete_captured_runtime_retirement_journal")}
complete_captured_runtime_retirement_journal
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert observed[:4] == [
        "retire-finalize",
        "app-proof",
        "proxy-proof",
        "verifier-proof",
    ]
    assert "provision_agent_proxy_secret" in observed[4]
    assert observed[5] == "proxy-proof"
    assert "cutover_agent_runtime_supervisor clear-journal" in observed[6]


@pytest.mark.parametrize(
    ("absent_endpoint", "expected_gateway", "expected_supervisor"),
    [
        ("old-gateway", "0", "1"),
        ("old-supervisor", "1", "0"),
    ],
)
def test_captured_journal_classifies_partial_old_resource_retirement(
    tmp_path: Path,
    absent_endpoint: str,
    expected_gateway: str,
    expected_supervisor: str,
) -> None:
    harness = tmp_path / f"partial-retirement-{absent_endpoint}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
refresh_captured_cutover_journal() {{
  MIP_REPLACED_AGENT_GATEWAY_ENDPOINT=old-gateway
  MIP_REPLACED_AGENT_GATEWAY_ENDPOINT_ID=old-gateway-id
  MIP_REPLACED_AGENT_GATEWAY_CREATOR=runtime-client
  MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT=old-supervisor
  MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID=old-supervisor-id
  MIP_REPLACED_AGENT_SUPERVISOR_CREATOR=runtime-client
}}
pinned_serving_endpoint_status() {{
  [[ "$1" == {shlex.quote(absent_endpoint)} ]] && return 3
  return 0
}}
{_shell_function("load_captured_live_old_resources")}
load_captured_live_old_resources
printf '%s %s\\n' "$CAPTURED_OLD_GATEWAY_LIVE" "$CAPTURED_OLD_SUPERVISOR_LIVE"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{expected_gateway} {expected_supervisor}"


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    [("none", 0), ("direct", 0), ("mixed", 0), ("managed", 1)],
)
def test_journaled_old_supervisor_app_access_modes_are_fail_closed(
    tmp_path: Path,
    mode: str,
    expected_status: int,
) -> None:
    harness = tmp_path / f"old-supervisor-app-{mode}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
OLD_SUPERVISOR_APP_ACCESS_MODE=none
MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT=journaled-supervisor
MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID=journaled-supervisor-id
MIP_REPLACED_AGENT_SUPERVISOR_CREATOR=runtime-client
APP_SP_CLIENT_ID=app-client
RED=""
RST=""
pinned_serving_endpoint_status() {{
  [[ "$1" == journaled-supervisor && "$2" == journaled-supervisor-id ]]
}}
pinned_query_access_mode() {{
  [[ "$2" == journaled-supervisor ]] || return 1
  printf '%s\\n' {shlex.quote(mode)}
}}
{_shell_function("classify_journaled_old_supervisor_app_access")}
classify_journaled_old_supervisor_app_access
status=$?
printf 'status=%s mode=%s\\n' "$status" "$OLD_SUPERVISOR_APP_ACCESS_MODE"
exit "$status"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == expected_status
    assert result.stdout.strip() == f"status={expected_status} mode={mode}"


def test_only_authenticated_old_supervisor_app_pin_is_reviewed_during_activation() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    classifier = _shell_function("classify_journaled_old_supervisor_app_access")
    assert "MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT" in classifier
    assert "pinned_serving_endpoint_status" in classifier
    assert "pinned_query_access_mode" in classifier
    reconcile = _shell_function("reconcile_retry_supervisor_app_acl")
    assert "is_blue" in reconcile
    assert 'if mode == "managed":' in reconcile
    assert '"${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}"' in reconcile
    assert "display_name=supervisor_name" in reconcile
    activation = script[
        script.index("APP_GLOBAL_ACCESS_ARGS=(") : script.index(
            'step "audit App access across every visible serving resource during cutover"'
        )
    ]
    assert '"$OLD_SUPERVISOR_APP_ACCESS_MODE" == "direct"' in activation
    assert '"$OLD_SUPERVISOR_APP_ACCESS_MODE" == "mixed"' in activation
    assert '--serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"' in activation
    assert (
        '--legacy-pinned-serving-endpoint "$MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT"' in activation
    )


def test_configured_supervisor_name_reaches_historical_cleanup() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    cleanup = script[
        script.index("tools.databricks.reconcile_historical_agent_endpoints cleanup") :
    ]

    assert '--supervisor-name "${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}"' in cleanup


def test_fresh_deploy_creates_governed_uc_tables_before_table_grants() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    bundle = yaml.safe_load(BUNDLE_CONFIG.read_text(encoding="utf-8"))

    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    namespace_setup = script.index(
        'step "ensure managed UC pipeline namespace exists before bundle apply"'
    )
    namespace_bootstrap = script.index(
        "tools.databricks.ensure_pipeline_namespace", namespace_setup
    )
    post_bundle_quiesce = script.index(
        'step "quiesce app treatment writes immediately before treatment-table DDL"'
    )
    uc_init = script.index(
        "\ninitialize_uc_targets_and_reconcile_function_grants\n",
        post_bundle_quiesce,
    )
    constraint_convergence = script.index(
        "tools.databricks.ensure_campaign_treatment_table", uc_init
    )
    table_grants = script.index('step "apply UC grants to the app service principal')
    skip_silver_branch = script.index('if [[ "$SKIP_SILVER" -eq 1 ]]')

    assert (
        namespace_bootstrap
        < bundle_apply
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
    bootstrap_tasks = bundle["resources"]["jobs"]["mip_init_catalog_schemas"]["tasks"]
    task_by_key = {task["task_key"]: task for task in bootstrap_tasks}
    expected_paths = {
        "init_catalog_schemas": "sql/_rendered/ddl/001_catalogs_schemas.sql",
        "init_ref_tables": "sql/_rendered/ddl/004_ref_tables.sql",
        "init_governed_gold_tables": "sql/_rendered/ddl/003_gold_tables.sql",
        "publish_fn_build_cohort": "sql/_rendered/uc_functions/fn_build_cohort.sql",
        "publish_fn_segment_counts": "sql/_rendered/uc_functions/fn_segment_counts.sql",
        "publish_fn_lead_queue_url": "sql/_rendered/uc_functions/fn_lead_queue_url.sql",
    }
    assert set(task_by_key) == set(expected_paths)
    for task_key, path in expected_paths.items():
        assert task_by_key[task_key]["sql_task"]["file"]["path"] == path
    assert "depends_on" not in task_by_key["init_catalog_schemas"]
    assert task_by_key["init_ref_tables"]["depends_on"] == [{"task_key": "init_catalog_schemas"}]
    assert task_by_key["init_governed_gold_tables"]["depends_on"] == [
        {"task_key": "init_ref_tables"}
    ]
    for task_key in (
        "publish_fn_build_cohort",
        "publish_fn_segment_counts",
        "publish_fn_lead_queue_url",
    ):
        assert task_by_key[task_key]["depends_on"] == [{"task_key": "init_governed_gold_tables"}]

    ddl_sources = {
        path: _sql_without_comments(
            (REPO / path.replace("sql/_rendered/", "sql/")).read_text(encoding="utf-8")
        )
        for path in expected_paths.values()
    }
    assert re.search(
        r"(?im)^\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+mip\.gold\b",
        ddl_sources[expected_paths["init_catalog_schemas"]],
    )
    assert re.search(
        r"(?im)^\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+mip\.audit\b",
        ddl_sources[expected_paths["init_catalog_schemas"]],
    )
    assert re.search(
        r"(?im)^\s*CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+mip\.ref\b",
        ddl_sources[expected_paths["init_ref_tables"]],
    )
    for table in ("borrower_lifecycle_state", "funnel_snapshot_daily"):
        assert re.search(
            rf"(?im)^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+" rf"mip\.gold\.{re.escape(table)}\b",
            ddl_sources[expected_paths["init_governed_gold_tables"]],
        )
    for function in ("fn_build_cohort", "fn_segment_counts", "fn_lead_queue_url"):
        assert re.search(
            rf"(?im)^\s*CREATE\s+OR\s+REPLACE\s+FUNCTION\s+" rf"mip\.gold\.{re.escape(function)}\b",
            ddl_sources[expected_paths[f"publish_{function}"]],
        )
    namespace_block = script[namespace_setup:bundle_apply]
    assert '--catalog "${MIP_DEFAULT_CATALOG:-mip}"' in namespace_block
    assert "--schema silver" in namespace_block
    assert "--warehouse-id" not in namespace_block
    assert "run_with_account_identity" in namespace_block
    assert namespace_block.count("--forbidden-owner-principal") >= 2
    for forbidden_client_id in (
        '"$DATABRICKS_CLIENT_ID"',
        '"$DATABRICKS_OPERATOR2_CLIENT_ID"',
        '"$DATABRICKS_ADMIN_CLIENT_ID"',
        '"$DATABRICKS_RELEASE_PROBE_CLIENT_ID"',
        '"$DATABRICKS_VERIFIER_CLIENT_ID"',
        '"$DATABRICKS_AGENT_RUNTIME_CLIENT_ID"',
    ):
        assert forbidden_client_id in namespace_block
    assert '"$_EXISTING_APP_SP_CLIENT_ID"' in namespace_block
    assert "mip_init_catalog_schemas" not in namespace_block


def test_admin_and_nightly_jobs_cannot_replace_reviewed_growth_agent_functions() -> None:
    bundle = yaml.safe_load(BUNDLE_CONFIG.read_text(encoding="utf-8"))
    jobs = bundle["resources"]["jobs"]
    reviewed_paths = {
        "sql/_rendered/uc_functions/fn_build_cohort.sql",
        "sql/_rendered/uc_functions/fn_segment_counts.sql",
        "sql/_rendered/uc_functions/fn_lead_queue_url.sql",
    }

    publishers: set[tuple[str, str, str]] = set()
    for job_name, job in jobs.items():
        for task in job.get("tasks", []):
            path = task.get("sql_task", {}).get("file", {}).get("path", "")
            if path in reviewed_paths:
                publishers.add((job_name, task["task_key"], path))

    assert publishers == {
        (
            "mip_init_catalog_schemas",
            f"publish_{path.rsplit('/', 1)[-1].removesuffix('.sql')}",
            path,
        )
        for path in reviewed_paths
    }
    for managed_job in MANAGED_JOBS.values():
        assert not any(
            task.get("sql_task", {}).get("file", {}).get("path") in reviewed_paths
            for task in jobs[managed_job.job_name].get("tasks", [])
        ), managed_job.job_name

    nightly_workflow = yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))
    nightly_jobs = _workflow_bundle_run_targets(nightly_workflow)
    publisher_jobs = {job_name for job_name, _task_key, _path in publishers}
    assert "mip_refresh_scores" in nightly_jobs
    assert nightly_jobs.isdisjoint(publisher_jobs)


def test_workflow_bundle_target_inventory_handles_shell_boundaries() -> None:
    continued = {
        "jobs": {
            "gate": {
                "steps": [{"run": "databricks bundle run \\\n  mip_init_catalog_schemas -t dev"}]
            }
        }
    }
    variable = {"jobs": {"gate": {"steps": [{"run": 'databricks bundle run "$JOB" -t dev'}]}}}
    chained = {
        "jobs": {
            "gate": {
                "steps": [{"run": "true&&databricks bundle run mip_init_catalog_schemas -t dev"}]
            }
        }
    }
    subshell = {
        "jobs": {
            "gate": {
                "steps": [
                    {"run": "(databricks bundle run mip_init_catalog_schemas -t dev)"},
                    {"run": "result=$(databricks bundle run " "mip_init_catalog_schemas -t dev)"},
                ]
            }
        }
    }

    assert _workflow_bundle_run_targets(continued) == {"mip_init_catalog_schemas"}
    assert _workflow_bundle_run_targets(chained) == {"mip_init_catalog_schemas"}
    assert _workflow_bundle_run_targets(subshell) == {"mip_init_catalog_schemas"}
    with pytest.raises(AssertionError, match="literal governed job names"):
        _workflow_bundle_run_targets(variable)


def test_lakebase_sync_access_is_target_bound_and_converged_around_provisioning() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    migration = script.index(
        'run_job_with_retry databricks bundle run mip_lakebase_migrate -t "$TARGET"'
    )
    quiesce = script.index('step "quiesce legacy and target Lakebase synced-catalog access"')
    base_grants = script.index('step "apply UC grants to the app service principal')
    sync_provision = script.index('step "prove agentic Lakebase Sync under deployer authority"')
    runtime_convergence = script.index(
        'step "converge exact app read-only access to proven Lakebase synced tables"'
    )
    app_snapshot_with_agentic_proof = script.index(
        'deploy_app_snapshot "activate App snapshot on the runtime-owned Gateway before retirement"'
    )

    assert migration < quiesce < base_grants < sync_provision < runtime_convergence
    assert runtime_convergence < app_snapshot_with_agentic_proof
    base_grant_block = script[base_grants:sync_provision]
    assert "converge_app_lakebase_sync_access" not in base_grant_block
    executable_grants = [
        line.strip().replace("\\`", "`")
        for line in base_grant_block.splitlines()
        if line.lstrip().startswith("GRANT ")
    ]
    assert executable_grants == [
        "GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO `${APP_SP_CLIENT_ID}`",
        "GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.gold TO `${APP_SP_CLIENT_ID}`",
        "GRANT MODIFY ON TABLE ${_GRANTS_CATALOG}.gold.borrower_lifecycle_state TO `${APP_SP_CLIENT_ID}`",
        "GRANT MODIFY ON TABLE ${_GRANTS_CATALOG}.gold.funnel_snapshot_daily TO `${APP_SP_CLIENT_ID}`",
        "GRANT USE SCHEMA, SELECT ON SCHEMA ${_GRANTS_CATALOG}.ref TO `${APP_SP_CLIENT_ID}`",
        "GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.audit TO `${APP_SP_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO `${APP_SP_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO `${APP_SP_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO `${APP_SP_CLIENT_ID}`",
        "GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.gold TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT USE CATALOG ON CATALOG ${_GRANTS_CATALOG} TO `${DATABRICKS_AGENT_PROXY_CLIENT_ID}`",
        "GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.gold TO `${DATABRICKS_AGENT_PROXY_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_build_cohort TO `${DATABRICKS_AGENT_PROXY_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_segment_counts TO `${DATABRICKS_AGENT_PROXY_CLIENT_ID}`",
        "GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.fn_lead_queue_url TO `${DATABRICKS_AGENT_PROXY_CLIENT_ID}`",
        "GRANT USE SCHEMA ON SCHEMA ${_GRANTS_CATALOG}.audit TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT CREATE MODEL ON SCHEMA ${_GRANTS_CATALOG}.audit TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
        "GRANT CREATE TABLE ON SCHEMA ${_GRANTS_CATALOG}.audit TO `${DATABRICKS_AGENT_RUNTIME_CLIENT_ID}`",
    ]
    assert not any(
        token in base_grant_block
        for token in (
            "DEPLOYMENT_SYNC_CATALOG",
            "MIP_LAKEBASE_SYNC_CATALOG",
            "mip_app_state.public",
            "mip_app_state.mip_app",
            "mip_app_state.mip_sync",
        )
    )
    quiesce_block = script[quiesce:base_grants]
    quiesce_tokens = _continued_command_tokens(
        quiesce_block, "tools.databricks.converge_app_lakebase_sync_access"
    )
    runtime_block = script[runtime_convergence:app_snapshot_with_agentic_proof]
    runtime_tokens = _continued_command_tokens(
        runtime_block, "tools.databricks.converge_app_lakebase_sync_access"
    )
    common = [
        "--warehouse-id",
        "$_GRANTS_WAREHOUSE_ID",
        "--app-application-id",
        "$APP_SP_CLIENT_ID",
        "--app-scim-id",
        "$APP_SP_SCIM_ID",
        "--sync-catalog",
        "$DEPLOYMENT_SYNC_CATALOG",
        "--sync-schema",
        "$DEPLOYMENT_SYNC_SCHEMA",
        "--sync-tables",
        "$DEPLOYMENT_SYNC_TABLES",
    ]
    assert quiesce_tokens == [
        "run",
        "$PYTHON",
        "-m",
        "tools.databricks.converge_app_lakebase_sync_access",
        "--mode",
        "quiesce",
        *common,
    ]
    assert runtime_tokens == [
        "run",
        "$PYTHON",
        "-m",
        "tools.databricks.converge_app_lakebase_sync_access",
        "--mode",
        "runtime",
        *common,
    ]


def test_reviewed_function_execute_grants_are_reconciled_after_gold_refresh() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    helper = _shell_function("reconcile_reviewed_function_execute_grants")
    publisher = _shell_function("run_job_and_reconcile_reviewed_function_grants")
    bootstrap = _shell_function("initialize_uc_targets_and_reconcile_function_grants")
    refresh_helper = _shell_function("refresh_gold_and_reconcile_function_grants")
    runtime_provision = (
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    initial_proof = script.index("REVIEWED_FUNCTION_GRANTS_PROVEN=0")
    lakebase_migration = script.index("databricks bundle run mip_lakebase_migrate")
    function_postflight = script.index(
        "tools.databricks.verify_reviewed_function_execute_grants",
        lakebase_migration,
    )
    first_proven = script.index("REVIEWED_FUNCTION_GRANTS_PROVEN=1", function_postflight)

    assert initial_proof < lakebase_migration < function_postflight < first_proven
    assert "REVIEWED_FUNCTION_GRANTS_PROVEN=1" not in script[initial_proof:function_postflight]
    assert script.count("\ninitialize_uc_targets_and_reconcile_function_grants\n") == 1
    assert script.count("\nrefresh_gold_and_reconcile_function_grants\n") == 1
    invocation = script.index("\nrefresh_gold_and_reconcile_function_grants\n")
    assert invocation < script.index(runtime_provision)
    assert '"$APP_SP_CLIENT_ID" \\\n    "$DATABRICKS_AGENT_RUNTIME_CLIENT_ID" \\\n' in helper
    assert '"$DATABRICKS_AGENT_PROXY_CLIENT_ID"; do' in helper
    assert "fn_build_cohort fn_segment_counts fn_lead_queue_url" in helper
    assert (
        '"GRANT EXECUTE ON FUNCTION ${_GRANTS_CATALOG}.gold.${_function_name} '
        'TO \\`${_principal}\\`"'
    ) in helper
    assert "      if ! apply_uc_grant \\\n" in helper
    assert (
        '  if ! run "$PYTHON" -m tools.databricks.verify_reviewed_function_execute_grants ' "\\\n"
    ) in helper
    assert '--agent-proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"' in helper
    assert (
        '  run_job_with_retry "$@" || _job_rc=$?\n'
        '  step "reconcile and prove reviewed function EXECUTE grants after governed job"\n'
        "  reconcile_reviewed_function_execute_grants || _reconcile_rc=$?"
    ) in publisher
    assert publisher.index("REVIEWED_FUNCTION_GRANTS_PROVEN=0") < publisher.index(
        'run_job_with_retry "$@"'
    )
    assert (
        "run_job_and_reconcile_reviewed_function_grants \\\n"
        '    "initialize every pre-refresh UC grant target (idempotent)" \\\n'
        '    databricks bundle run mip_init_catalog_schemas -t "$TARGET"'
    ) in bootstrap
    assert (
        "run_job_and_reconcile_reviewed_function_grants \\\n"
        '    "refresh gold — borrower_360, lead_scores, *_population, dossier, + mip.semantics.*" \\\n'
        '    databricks bundle run mip_refresh_scores -t "$TARGET"'
    ) in refresh_helper


def test_function_reconciliation_attempts_all_grants_after_one_fails(tmp_path: Path) -> None:
    calls = tmp_path / "function-grants.log"
    harness = tmp_path / "function-grants.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
APP_SP_CLIENT_ID=app-client
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
_GRANTS_CATALOG=mip
PYTHON=python3
RED=""
RST=""
REVIEWED_FUNCTION_GRANTS_PROVEN=0
apply_uc_grant() {{
  printf 'grant:%s\n' "$1" >> {shlex.quote(str(calls))}
  [[ "$1" != *'fn_segment_counts TO `app-client`'* ]]
}}
run() {{ printf 'postflight:%s\n' "$*" >> {shlex.quote(str(calls))}; return 0; }}
{_shell_function("reconcile_reviewed_function_execute_grants")}
set +e
reconcile_reviewed_function_execute_grants
rc=$?
set -e
printf 'rc:%s proven:%s\n' "$rc" "$REVIEWED_FUNCTION_GRANTS_PROVEN"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "rc:4 proven:0\n"
    entries = calls.read_text(encoding="utf-8").splitlines()
    assert len([entry for entry in entries if entry.startswith("grant:")]) == 9
    assert len([entry for entry in entries if entry.startswith("postflight:")]) == 1


def test_function_reconciliation_requires_effective_postflight(tmp_path: Path) -> None:
    calls = tmp_path / "function-postflight.log"
    harness = tmp_path / "function-postflight.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
APP_SP_CLIENT_ID=app-client
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
_GRANTS_CATALOG=mip
PYTHON=python3
RED=""
RST=""
REVIEWED_FUNCTION_GRANTS_PROVEN=0
apply_uc_grant() {{ printf 'grant\n' >> {shlex.quote(str(calls))}; return 0; }}
run() {{ printf 'postflight\n' >> {shlex.quote(str(calls))}; return 23; }}
{_shell_function("reconcile_reviewed_function_execute_grants")}
set +e
reconcile_reviewed_function_execute_grants
rc=$?
set -e
printf 'rc:%s proven:%s\n' "$rc" "$REVIEWED_FUNCTION_GRANTS_PROVEN"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "rc:4 proven:0\n"
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "grant",
        "grant",
        "grant",
        "grant",
        "grant",
        "grant",
        "grant",
        "grant",
        "grant",
        "postflight",
    ]


def test_failed_gold_refresh_repairs_grants_before_preserving_nonzero_exit(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "failed-refresh.log"
    harness = tmp_path / "failed-refresh.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
TARGET=dev
RED=""
YLW=""
DIM=""
RST=""
PYTHON=python3
step() {{ printf 'step:%s\n' "$*" >> {shlex.quote(str(calls))}; }}
run_job_with_retry() {{ printf 'refresh\n' >> {shlex.quote(str(calls))}; return 17; }}
reconcile_reviewed_function_execute_grants() {{
  printf 'reconcile\n' >> {shlex.quote(str(calls))}
  REVIEWED_FUNCTION_GRANTS_PROVEN=1
  return 0
}}
stop_app_after_failed_deploy() {{ printf 'compensate\n' >> {shlex.quote(str(calls))}; }}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
{_shell_function("run_job_and_reconcile_reviewed_function_grants")}
{_shell_function("refresh_gold_and_reconcile_function_grants")}
{_deploy_exit_trap_block()}
refresh_gold_and_reconcile_function_grants
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 17, result.stdout + result.stderr
    entries = calls.read_text(encoding="utf-8").splitlines()
    assert entries.index("refresh") < entries.index("reconcile") < entries.index("compensate")


def test_failed_reconciliation_invalidates_prior_proof_before_real_compensation(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "failed-reconciliation.log"
    harness = tmp_path / "failed-reconciliation.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
APP_FAIL_CLOSED_ARMED=1
APP_FAIL_CLOSED_NAME=mip-app
APP_UPGRADE_STATE=green_activating_quiesced
TREATMENT_RUNTIME_QUIESCED=1
APP_SIGNED_BLUE_AVAILABLE=1
APP_ACCESS_QUARANTINED=0
REVIEWED_FUNCTION_GRANTS_PROVEN=1
TARGET=dev
RED=""
YLW=""
DIM=""
RST=""
PYTHON=python3
step() {{ :; }}
run_job_with_retry() {{ printf 'refresh\n' >> {shlex.quote(str(calls))}; return 0; }}
reconcile_reviewed_function_execute_grants() {{
  printf 'reconcile-failed\n' >> {shlex.quote(str(calls))}
  return 4
}}
stop_and_quiesce_unproven_app() {{ printf 'stop-quiesce\n' >> {shlex.quote(str(calls))}; }}
restore_signed_blue_while_quiesced() {{ printf 'rollback-blue\n' >> {shlex.quote(str(calls))}; }}
converge_app_treatment_access() {{ printf 'treatment:%s\n' "$1" >> {shlex.quote(str(calls))}; }}
converge_green_only_app_access() {{ return 0; }}
{_shell_function("run_job_and_reconcile_reviewed_function_grants")}
{_shell_function("refresh_gold_and_reconcile_function_grants")}
{_shell_function("stop_app_after_failed_deploy")}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
{_deploy_exit_trap_block()}
refresh_gold_and_reconcile_function_grants
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 4, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "refresh",
        "reconcile-failed",
        "stop-quiesce",
    ]


def test_failed_bootstrap_repairs_before_blue_quiesced_compensation(tmp_path: Path) -> None:
    calls = tmp_path / "failed-bootstrap.log"
    harness = tmp_path / "failed-bootstrap.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
APP_DEPLOY_PAYLOAD=""
AGENTIC_ENV_FILE=""
AGENT_EVAL_ENV_FILE=""
APP_FAIL_CLOSED_ARMED=1
APP_FAIL_CLOSED_NAME=mip-app
APP_UPGRADE_STATE=blue_quiesced
TREATMENT_RUNTIME_QUIESCED=1
APP_SIGNED_BLUE_AVAILABLE=1
APP_ACCESS_QUARANTINED=0
REVIEWED_FUNCTION_GRANTS_PROVEN=1
TARGET=dev
RED=""
YLW=""
DIM=""
RST=""
PYTHON=python3
step() {{ :; }}
run_job_with_retry() {{ printf 'bootstrap-failed\n' >> {shlex.quote(str(calls))}; return 19; }}
reconcile_reviewed_function_execute_grants() {{
  printf 'reconcile-failed\n' >> {shlex.quote(str(calls))}
  return 4
}}
stop_and_quiesce_unproven_app() {{ printf 'stop-quiesce\n' >> {shlex.quote(str(calls))}; }}
restore_signed_blue_while_quiesced() {{ printf 'rollback-blue\n' >> {shlex.quote(str(calls))}; }}
converge_app_treatment_access() {{ return 0; }}
converge_green_only_app_access() {{ return 0; }}
{_shell_function("run_job_and_reconcile_reviewed_function_grants")}
{_shell_function("initialize_uc_targets_and_reconcile_function_grants")}
{_shell_function("stop_app_after_failed_deploy")}
quiesce_app_treatment_after_failed_stop() {{ return 0; }}
{_deploy_exit_trap_block()}
initialize_uc_targets_and_reconcile_function_grants
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 19, result.stdout + result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "bootstrap-failed",
        "reconcile-failed",
        "stop-quiesce",
    ]


def test_deployer_sync_provision_command_is_exact_and_cannot_skip_sync() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index('step "prove agentic Lakebase Sync under deployer authority"')
    end = script.index(
        'step "converge exact app read-only access to proven Lakebase synced tables"', start
    )
    tokens = _continued_command_tokens(
        script[start:end], "tools.databricks.provision_agentic_resources"
    )

    assert tokens == [
        "run",
        "$PYTHON",
        "-m",
        "tools.databricks.provision_agentic_resources",
        "--app-name",
        "$_GRANTS_APP_NAME",
        "--deployment-lease-id",
        "$MIP_APP_DEPLOYMENT_LEASE_ID",
        "--deployment-source-git-sha",
        "$SOURCE_GIT_SHA",
        "--catalog",
        "${MIP_DEFAULT_CATALOG:-mip}",
        "--genie-space-id",
        "${GENIE_SPACE_ID:-$(< genie/space_id.txt)}",
        "--lakebase-catalog",
        "$DEPLOYMENT_SYNC_CATALOG",
        "--lakebase-schema",
        "$DEPLOYMENT_SYNC_SCHEMA",
        "--lakebase-sync-tables",
        "$DEPLOYMENT_SYNC_TABLES",
        "--database-instance",
        "$MIP_LAKEBASE_INSTANCE",
        "--logical-database",
        "$LAKEBASE_DATABASE",
        "--capture-reviewed-function-owner",
        "--skip-supervisor",
        "--skip-gateway",
        "--out-env",
        "$AGENTIC_ENV_FILE",
    ]
    assert "--skip-sync" not in tokens


def test_agentic_rotation_and_retirement_pass_exact_managed_group_identities() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    supervisor_start = script.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    supervisor_end = script.index(
        'step "grant and globally audit the dedicated Supervisor proxy caller"',
        supervisor_start,
    )
    supervisor = script[supervisor_start:supervisor_end]
    assert '--approved-query-application-id "$APP_SP_CLIENT_ID"' in supervisor

    gateway_start = script.index(
        'step "provision the governed outer Gateway under agent-runtime authority"'
    )
    gateway_end = script.index(
        'step "re-audit the Supervisor proxy caller after Gateway provisioning"',
        gateway_start,
    )
    gateway = script[gateway_start:gateway_end]
    assert '--approved-query-application-id "$APP_SP_CLIENT_ID"' in gateway
    assert '--approved-query-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in gateway

    retire_start = script.index("AGENT_RUNTIME_RETIRE_ARGS=(")
    retire_end = script.index("\n  )", retire_start)
    retire = script[retire_start:retire_end]
    assert '--verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in retire
    assert '--verifier-scim-id "$MIP_VERIFIER_SCIM_ID"' in retire
    assert '--proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"' in retire


def test_pipeline_namespace_bootstrap_is_leased_and_precedes_bundle_apply() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    lease = script.index("tools.databricks.app_deployment_lease acquire")
    compensation = script.index("APP_FAIL_CLOSED_ARMED=1")
    plan = script.index('tools.databricks.bundle_env plan -t "$TARGET"')
    namespace = script.index("tools.databricks.ensure_pipeline_namespace")
    bundle_apply = script.index('tools.databricks.bundle_env deploy -t "$TARGET"')
    full_ddl = script.index('databricks bundle run mip_init_catalog_schemas -t "$TARGET"')

    assert lease < compensation < plan < namespace < bundle_apply < full_ddl
    assert script.count("verify_exact_deploy_source", plan, bundle_apply) == 2
    assert "^[a-z_][a-z0-9_]{0,254}$" in script


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
        ["git", "clone", "--quiet", "--shared", str(REPO), str(checkout)],
        check=True,
    )
    deploy_copy = checkout / "scripts" / "deploy.sh"
    _write_deploy_fixture(deploy_copy, DEPLOY_SCRIPT.read_text(encoding="utf-8"))
    payload_copy = checkout / "tools" / "databricks" / "app_deploy_payload.py"
    payload_copy.write_text(
        (REPO / "tools" / "databricks" / "app_deploy_payload.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    lender_identity_copy = checkout / "backend" / "schemas" / "lender_identity.py"
    lender_identity_copy.write_text(
        (REPO / "backend" / "schemas" / "lender_identity.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    instance_contract_copy = checkout / "tools" / "databricks" / "lakebase_instance_contract.py"
    instance_contract_copy.write_text(
        (REPO / "tools" / "databricks" / "lakebase_instance_contract.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "git",
            "add",
            "scripts/deploy.sh",
            "scripts/lib",
            "backend/schemas/lender_identity.py",
            "tools/databricks/app_deploy_payload.py",
            "tools/databricks/lakebase_instance_contract.py",
        ],
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
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "dry-run-runtime-client-id",
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
    grant_start = result.stdout.index(
        "refresh gold — borrower_360, lead_scores, *_population, dossier, + mip.semantics.*"
    )
    grant_end = result.stdout.index(
        "sync lifecycle state from Lakebase + record daily funnel snapshot",
        grant_start,
    )
    grant_block = result.stdout[grant_start:grant_end]
    for principal in ("dry-run-app-client-id", "dry-run-runtime-client-id"):
        for function_name in (
            "fn_build_cohort",
            "fn_segment_counts",
            "fn_lead_queue_url",
        ):
            statement = (
                "would grant: GRANT EXECUTE ON FUNCTION mip.gold."
                f"{function_name} TO `{principal}`"
            )
            assert grant_block.count(statement) == 1
    assert "would verify: campaign treatment table append-only" in result.stdout
    assert "would inspect/create: scope mip and write-once pii-salt-v1" in result.stdout
    assert "deploy Databricks App snapshot with Agent Evaluation proof" in result.stdout


def test_deploy_dev_wires_separate_required_gateway_signing_keys() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    secret_binding = (
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY: " "${{ secrets.MIP_AI_GATEWAY_PROOF_SIGNING_KEY }}"
    )
    assert workflow.count(secret_binding) == 3
    assert (
        workflow.count(
            "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS: "
            "${{ vars.MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS }}"
        )
        == 2
    )
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


def test_app_snapshot_payload_forwards_exact_lakebase_deployment_control() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index("emit_app_deploy_payload() {")
    end = script.index("\n}\n", start)
    function = script[start:end]

    assert function.count('--lakebase-instance "$MIP_LAKEBASE_INSTANCE"') == 1
    assert function.index('--lakebase-instance "$MIP_LAKEBASE_INSTANCE"') < function.index(
        '> "$destination"'
    )


def test_otlp_is_an_exact_overlay_on_the_governed_target_and_rollback() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    bundle = BUNDLE_CONFIG.read_text(encoding="utf-8")

    otlp_preflight = script.index(
        'MIP_OTEL_ENDPOINT="$(deployment_control_value MIP_OTEL_ENDPOINT)"'
    )
    lease = script.index("tools.databricks.app_deployment_lease acquire")
    resource_build = script.index("tools.databricks.app_resource_bindings build")
    snapshot = script.index("emit_app_deploy_payload() {")
    capture = script.index("--app-resource-payload", snapshot)

    assert otlp_preflight < lease < resource_build < snapshot < capture
    assert "MIP_OTEL_HEADERS must never be provided as plaintext" in script
    assert "--otel-header-secret-scope" in script[otlp_preflight : resource_build + 500]
    assert "--otel-header-secret-key" in script[otlp_preflight : resource_build + 500]
    assert "--otel-endpoint" in script[otlp_preflight:lease]
    assert "--otel-header-resource otel_headers" in script[otlp_preflight:lease]
    assert '"${OTEL_DEPLOY_PAYLOAD_ARGS[@]}"' in script[snapshot:capture]
    assert "prod_otlp:" not in bundle


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
    assert '--app-resource-payload "${APP_RESOURCE_BINDING_PAYLOAD:' in script


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
            from tools.databricks import app_deployment_lease_cli as lease_cli

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
            lease_cli.time.sleep = lambda _seconds: None
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
        key: value for key, value in os.environ.items() if key != "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"
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
        # CI executes this import-heavy child beside the full xdist worker
        # pool. Bound hangs, but do not mistake scheduler starvation for a
        # heartbeat failure on smaller shared runners.
        timeout=45,
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
        "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET",
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
        "DATABRICKS_RELEASE_PROBE_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_AGENT_PROXY_CLIENT_ID",
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
        "DATABRICKS_RELEASE_PROBE_CLIENT_ID": "release-probe",
        "DATABRICKS_VERIFIER_CLIENT_ID": "verifier",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID": "runtime",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE": (
            '{"client_id":"agent-proxy","client_secret":"proxy-secret",'
            '"credential_id":"agent-proxy-credential","version":1}'
        ),
        "DATABRICKS_ACCOUNT_HOST": "https://accounts.example",
        "DATABRICKS_ACCOUNT_ID": "account-id",
        "DATABRICKS_ACCOUNT_CLIENT_ID": "account-client",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET": "account-secret",
    }
    result = subprocess.run(
        ["bash", "-c", _workflow_run_block(DEPLOY_DEV, "Configure Databricks dev credentials")],
        cwd=REPO,
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
        "DATABRICKS_RELEASE_PROBE_CLIENT_ID",
        "DATABRICKS_VERIFIER_CLIENT_ID",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DATABRICKS_AGENT_PROXY_CLIENT_ID",
        "DATABRICKS_ACCOUNT_CLIENT_ID",
    ):
        expected = (
            "agent-proxy"
            if client_id == "DATABRICKS_AGENT_PROXY_CLIENT_ID"
            else required[client_id]
        )
        assert child[client_id] == expected
    for secret in (
        "DATABRICKS_TOKEN",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        "MIP_COTALITY_ID_MASK_SECRET",
        "MIP_GENIE_ACTION_SECRET_CURRENT",
        "MIP_GENIE_ACTION_SECRET_PREVIOUS",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY",
        "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY",
        "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE",
    ):
        assert secret not in child


def test_deploy_dev_wires_optional_approved_uc_owner_contract() -> None:
    workflow = DEPLOY_DEV.read_text(encoding="utf-8")

    assert "rebase_unverified_app:" in workflow
    assert "remediate_foreign_catalog_bindings:" in workflow
    assert (
        "MIP_REBASE_UNVERIFIED_APP: ${{ inputs.rebase_unverified_app && '1' || '0' }}" in workflow
    )
    assert (
        "MIP_REMEDIATE_FOREIGN_CATALOG_BINDINGS: "
        "${{ inputs.remediate_foreign_catalog_bindings && '1' || '0' }}" in workflow
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
    binding_policy = (
        "MIP_UC_FOREIGN_CATALOG_BINDING_POLICY: "
        "${{ vars.MIP_UC_FOREIGN_CATALOG_BINDING_POLICY }}"
    )
    assert workflow.count(binding_policy) == 2
    assert (
        "MIP_UC_FOREIGN_CATALOG_BINDING_POLICY=" "${MIP_UC_FOREIGN_CATALOG_BINDING_POLICY}"
    ) in workflow
    assert "DATABRICKS_ACCOUNT_CLIENT_ID: ${{ secrets.DATABRICKS_ACCOUNT_CLIENT_ID }}" in workflow
    assert (
        "DATABRICKS_ACCOUNT_CLIENT_SECRET: " "${{ secrets.DATABRICKS_ACCOUNT_CLIENT_SECRET }}"
    ) in workflow
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "dotenv_value MIP_UC_APPROVED_OWNER_PRINCIPALS" in script
    assert "deployment_control_value MIP_UC_FOREIGN_CATALOG_BINDING_POLICY" in script
    assert 'resolve_m2m_credential "$_ACCOUNT_AUTH_NAME"' in script
    assert "account-SCIM OAuth client must be distinct from" in script
    assert "account_client_id not in values" in script
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
    rebase = script.index('if [[ "${MIP_REBASE_UNVERIFIED_APP:-0}" == "1" && \\')
    rebase_first_install = script.index('APP_UPGRADE_STATE="first_install"', rebase)
    first_snapshot_guard = script.index(
        'if [[ "$APP_UPGRADE_STATE" == "first_install" ]]; then', rebase_first_install
    )
    assert rebase < rebase_first_install < first_snapshot_guard < first_snapshot
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


def test_active_app_binding_hash_includes_complete_proxy_credential_binding() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index('AGENT_RUNTIME_BINDING_SHA256="$($PYTHON -')
    end = script.index("\nPYEOF", start)
    block = script[start:end]

    for shell_value in (
        '"$MIP_AGENT_PROXY_CLIENT_ID"',
        '"$MIP_AGENT_PROXY_CREDENTIAL_ID"',
        '"$MIP_AGENT_PROXY_SECRET_REFERENCE"',
    ):
        assert shell_value in block
    for argument in (
        "proxy_caller_application_id=sys.argv[8]",
        "proxy_caller_credential_id=sys.argv[9]",
        "proxy_caller_secret_reference=sys.argv[10]",
    ):
        assert argument in block


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


@pytest.mark.parametrize("original_rc", (0, 7))
def test_deploy_exit_trap_releases_exact_lease_on_success_and_failure(
    tmp_path: Path,
    original_rc: int,
) -> None:
    result, release_log = _run_deploy_lease_cleanup_harness(
        tmp_path,
        original_rc=original_rc,
    )

    assert result.returncode == original_rc, result.stderr
    assert release_log.read_text(encoding="utf-8") == (
        "python3 -m tools.databricks.app_deployment_lease release "
        "--app-name mip-app --lease-id lease-id\n"
    )


def test_oauth_credential_quarantine_retains_borrowed_deployment_lease(
    tmp_path: Path,
) -> None:
    result, release_log = _run_deploy_lease_cleanup_harness(
        tmp_path,
        original_rc=7,
        credential_quarantined=True,
    )

    assert result.returncode == 90
    assert not release_log.exists()
    assert "OAuth credential cleanup is unproven" in result.stderr
    assert "retaining the signed deployment lease" in result.stderr


def test_deploy_exports_exact_source_and_quarantine_marker_under_signed_lease() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    source_assignment = script.index('MIP_DEPLOYMENT_SOURCE_GIT_SHA="$SOURCE_GIT_SHA"')
    source_export = script.index("export MIP_DEPLOYMENT_SOURCE_GIT_SHA")
    lease_acquire = script.index("tools.databricks.app_deployment_lease acquire")
    lease_assignment = script.index('APP_DEPLOYMENT_LEASE_ID="${MIP_APP_DEPLOYMENT_LEASE_ID:')
    marker_assignment = script.index(
        'OAUTH_CREDENTIAL_QUARANTINE_FILE="$(',
        lease_assignment,
    )
    marker_export = script.index(
        "export MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE=" '"$OAUTH_CREDENTIAL_QUARANTINE_FILE"',
        marker_assignment,
    )
    trap = _shell_function("restore_rendered_sql_fail_closed")

    assert source_assignment < source_export < lease_acquire
    assert lease_acquire < lease_assignment < marker_assignment < marker_export
    assert trap.index(' -s "$OAUTH_CREDENTIAL_QUARANTINE_FILE"') < trap.index(
        "tools.databricks.app_deployment_lease release"
    )


def test_oauth_credential_recovery_is_explicit_complete_and_lease_bound() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for name in (
        "MIP_OAUTH_CREDENTIAL_RECOVERY_INTENT_PATH",
        "MIP_OAUTH_CREDENTIAL_RECOVERY_PRINCIPAL_ID",
        "MIP_OAUTH_CREDENTIAL_RECOVERY_AUTHORITY_IDENTITY",
        "MIP_OAUTH_CREDENTIAL_RECOVERY_PROVIDER_API",
    ):
        assert name in script
    assert (
        'if [[ "$_OAUTH_RECOVERY_VALUE_COUNT" -ne 0 && \\\n'
        '      "$_OAUTH_RECOVERY_VALUE_COUNT" -ne 4 ]]'
    ) in script
    start = script.index(
        'step "recover the explicitly confirmed interrupted OAuth credential intent"'
    )
    end = script.index(
        'step "prove or create the deterministic owned App rollback secret scope"',
        start,
    )
    recovery = script[start:end]
    assert "run_with_account_identity run_with_proof_signing_authority" in recovery
    assert "tools.databricks.oauth_credential_recovery_cli recover" in recovery
    assert recovery.index("--intent-path") < recovery.index("--confirm-principal-id")
    assert recovery.index("--confirm-principal-id") < recovery.index("--confirm-authority-identity")
    assert recovery.index("--confirm-authority-identity") < recovery.index("--confirm-provider-api")


def test_oauth_orphan_lease_recovery_is_complete_exclusive_and_lease_bound() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    for name in (
        "MIP_OAUTH_CREDENTIAL_ORPHAN_LEASE_ID",
        "MIP_OAUTH_CREDENTIAL_ORPHAN_RECOVERY_ROOT_LEASE_ID",
    ):
        assert name in script
    assert (
        'if [[ "$_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT" -ne 0 && \\\n'
        '      "$_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT" -ne 2 ]]'
    ) in script
    assert (
        'if [[ "$_OAUTH_RECOVERY_VALUE_COUNT" -ne 0 && \\\n'
        '      "$_OAUTH_ORPHAN_RECOVERY_VALUE_COUNT" -ne 0 ]]'
    ) in script
    lease_acquire = script.index("tools.databricks.app_deployment_lease acquire")
    lease_heartbeat = script.index(
        "tools.databricks.app_deployment_lease heartbeat",
        lease_acquire,
    )
    start = script.index('step "recover the explicitly confirmed orphan OAuth credential lease"')
    end = script.index(
        'step "recover the explicitly confirmed interrupted OAuth credential intent"',
        start,
    )
    recovery = script[start:end]
    assert lease_acquire < lease_heartbeat < start
    assert "run_with_proof_signing_authority" in recovery
    assert (
        "tools.databricks.oauth_credential_recovery_cli " "\\\n      recover-orphan-lease"
    ) in recovery
    assert recovery.index("--confirm-lease-id") < recovery.index("--confirm-recovery-root-lease-id")


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


def test_app_failure_compensation_stops_when_function_grants_are_unproven(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="blue_active",
        rollback_result=0,
        stop_result=0,
        function_grants_proven=False,
    )

    assert result.returncode == 0, result.stderr
    assert "stop_app_fail_closed" in calls
    assert "app_deployment_rollback restore" not in calls


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


def test_quarantined_blue_restore_uses_probe_then_restores_runtime_acl(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="green_activating_quiesced",
        rollback_result=0,
        stop_result=1,
        access_quarantined=True,
    )

    assert result.returncode == 0, result.stderr
    probe_mint = (
        "mint MIP_BEARER_TOKEN DATABRICKS_RELEASE_PROBE_CLIENT_ID "
        "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET"
    )
    assert probe_mint in calls
    assert calls.index(probe_mint) < calls.index("app_deployment_rollback restore")
    assert calls.index("app_deployment_rollback restore") < calls.index(
        "converge_app_release_access --mode runtime"
    )
    assert calls.index("converge_app_release_access --mode runtime") < calls.index(
        "converge_campaign_treatment_access"
    )


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


def test_first_install_compensation_accepts_authoritative_app_absence(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=1,
        stop_result=0,
        stop_outcome="absent",
        app_principal="",
    )

    assert result.returncode == 0, result.stderr
    assert "stop_app_fail_closed" in calls
    assert "converge_campaign_treatment_access" not in calls


def test_unclaimed_first_install_trap_refuses_stop_and_treatment_mutations(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=1,
        stop_result=0,
        first_install_created=True,
        journal_status="unclaimed",
    )

    assert result.returncode == 1
    assert "app_first_install_journal status" in calls
    assert "stop_app_fail_closed" not in calls
    assert "converge_campaign_treatment_access" not in calls
    assert "not authorized for journal state unclaimed" in result.stderr


def test_claimed_first_install_trap_authenticates_before_stop(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=1,
        stop_result=0,
        first_install_created=True,
        journal_status="recover",
    )

    assert result.returncode == 0, result.stderr
    assert calls.index("app_first_install_journal status") < calls.index("stop_app_fail_closed")
    assert "--expected-app-id app-object-id" in calls
    assert "--expected-client-id app-client-id" in calls
    assert "--expected-scim-id app-scim-id" in calls


@pytest.mark.parametrize(
    "record",
    (
        "MIP_APP_STOP_OUTCOME=absent\nATTACKER_BYTES\n",
        "MIP_APP_STOP_OUTCOME=absent\nATTACKER_BYTES",
    ),
)
def test_first_install_compensation_rejects_extra_outcome_lines(
    tmp_path: Path,
    record: str,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=1,
        stop_result=0,
        app_principal="",
        stop_outcome_record=record,
    )

    assert result.returncode == 1
    assert "no authenticated outcome" in result.stderr
    assert "converge_campaign_treatment_access" not in calls


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


def test_app_failure_compensation_requarantines_probe_before_treatment_rollback(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="green_treatment_pending_capture",
        rollback_result=0,
        stop_result=0,
        access_quarantined=True,
    )

    assert result.returncode == 0, result.stderr
    assert calls.index("stop_app_fail_closed") < calls.index("converge_app_release_access")
    assert calls.index("converge_app_release_access") < calls.index(
        "converge_campaign_treatment_access"
    )
    assert "--mode quarantine" in calls


def test_app_failure_compensation_fails_if_probe_requarantine_cannot_be_proven(
    tmp_path: Path,
) -> None:
    result, calls = _run_app_failure_compensation_harness(
        tmp_path,
        state="first_install",
        rollback_result=1,
        stop_result=0,
        access_quarantined=True,
        release_acl_result=1,
    )

    assert result.returncode == 1
    assert "failed to re-quarantine temporary App release access" in result.stderr
    assert "converge_campaign_treatment_access" in calls


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


def test_deploy_script_requires_cotality_mask_secret_for_prod_target(tmp_path: Path) -> None:
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
    _write_deploy_fixture(deploy_copy, text)
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
        ["bash", str(deploy_copy), "-t", "prod", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'prod' (APP_ENV=prod)" in result.stderr
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
    _write_deploy_fixture(deploy_copy, text)
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
    _write_deploy_fixture(deploy_copy, text)
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
    _write_deploy_fixture(deploy_copy, text)
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
        ["bash", str(deploy_copy), "-t", "prod", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'prod' (APP_ENV=prod)" in result.stderr
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
    _write_deploy_fixture(deploy_copy, text)
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
        ["bash", str(deploy_copy), "-t", "prod", "--dry-run", "--no-confirm"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "MIP_COTALITY_ID_MASK_SECRET is required for target 'prod' (APP_ENV=prod)" in result.stderr
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
    _write_deploy_fixture(deploy_copy, text)
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
    _write_deploy_fixture(deploy_copy, DEPLOY_SCRIPT.read_text(encoding="utf-8"))
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


def test_deploy_timeout_covers_two_serial_auth_expiry_fences_with_margin() -> None:
    workflow = yaml.safe_load(DEPLOY_DEV.read_text(encoding="utf-8"))
    timeout_seconds = int(workflow["jobs"]["deploy"]["timeout-minutes"]) * 60
    serialized_admission_seconds = (
        2 * (admission._MAX_BOOTSTRAP_AUTH_TTL + orchestration._EXPIRY_SKEW).total_seconds()
    )

    assert timeout_seconds >= serialized_admission_seconds + 60 * 60


def test_agent_proxy_acl_lifecycle_is_bound_and_compensated_before_lease_release() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    first_proxy_step = script.index(
        'step "grant and globally audit the dedicated Supervisor proxy caller"'
    )
    first_mutation = script.index("run converge_agent_proxy_boundary", first_proxy_step)
    proxy_compensation_armed = script.index("AGENT_PROXY_ACCESS_MUTATED=1", first_proxy_step)
    assert proxy_compensation_armed < first_mutation
    lease_acquired = script.index('APP_DEPLOYMENT_LEASE_ID="${MIP_APP_DEPLOYMENT_LEASE_ID:')
    app_inventory = script.index('_EXISTING_APPS_JSON="$(databricks apps list -o json)"')
    signed_blue_proof = script.index(
        'step "prove or reconcile the signed last-good App before non-App mutations"'
    )
    assert (
        lease_acquired
        < app_inventory
        < signed_blue_proof
        < proxy_compensation_armed
        < first_mutation
    )
    assert "AGENT_PROXY_ACCESS_MUTATED=1" not in script[lease_acquired:proxy_compensation_armed]
    exact_identity_export = script.index('export MIP_DEPLOYMENT_APP_OBJECT_ID="$APP_OBJECT_ID"')
    unsigned_rebase_stop = script.index(
        'step "stop the exact unsigned rebase App before legacy proxy ACL migration"'
    )
    assert exact_identity_export < unsigned_rebase_stop < proxy_compensation_armed
    stop_block = script[unsigned_rebase_stop:proxy_compensation_armed]
    assert "tools.databricks.stop_app_fail_closed" in stop_block
    assert '"${APP_EXPECTED_IDENTITY_ARGS[@]}"' in stop_block
    helper = _shell_function("converge_agent_proxy_boundary")
    for required in (
        "--supervisor-endpoint",
        "--supervisor-endpoint-id",
        "DATABRICKS_AGENT_RUNTIME_CLIENT_ID",
        "DEPLOY_INVENTORY_PRINCIPAL",
    ):
        assert required in helper

    reaudit = script[
        script.index(
            'step "re-audit the Supervisor proxy caller after Gateway provisioning"'
        ) : script.index('step "prove dual-authority agent-proxy Unity Catalog boundary"')
    ]
    assert "run converge_agent_proxy_boundary" in reaudit
    assert "\n  audit \\" in reaudit

    restore = _shell_function("restore_signed_blue_while_quiesced")
    rollback = restore.index("tools.databricks.app_deployment_rollback restore")
    assert restore.index("converge_signed_blue_agent_proxy_boundary") < rollback
    assert restore.index("prove_exact_agent_proxy_boundary") < rollback
    proof = _shell_function("prove_exact_agent_proxy_boundary")
    assert "--target-query-only" not in proof
    assert "--allow-attested-app-401" in proof
    assert "run_with_account_identity" in proof
    assert "run_with_agent_proxy_credentials" in proof

    compensation = _shell_function("compensate_agent_proxy_access")
    assert "deny_all_agent_proxy_access" in compensation
    assert "converge_signed_blue_agent_proxy_boundary" in compensation
    assert "prove_exact_agent_proxy_boundary" in compensation
    assert "MIP_AGENT_SUPERVISOR_ENDPOINT" in compensation
    signed_blue = _shell_function("converge_signed_blue_agent_proxy_boundary")
    assert "--legacy-pinned-supervisor-endpoint" in signed_blue
    assert "MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" in signed_blue
    preserve = script[
        script.index("AGENT_PROXY_PRESERVE_ARGS=()") : script.index(
            'step "grant and globally audit the dedicated Supervisor proxy caller"'
        )
    ]
    assert "--legacy-pinned-supervisor-endpoint" in preserve
    assert "MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT" in preserve
    first_access = script[
        script.index(
            'step "grant and globally audit the dedicated Supervisor proxy caller"'
        ) : script.index(
            'step "provision the credential-versioned Supervisor proxy secret reference"'
        )
    ]
    assert '"${AGENT_PROXY_ACCESS_PRESERVE_ARGS[@]}"' in first_access
    credential_boundary_start = script.index(
        'step "prove agent-proxy target query and negative authorization boundary before cutover"'
    )
    credential_boundary = script[
        credential_boundary_start : script.index(
            "if ! revoke_agent_runtime_bootstrap_grants",
            credential_boundary_start,
        )
    ]
    assert '"${AGENT_PROXY_PRESERVE_ARGS[@]}"' in credential_boundary
    assert "AGENT_PROXY_ACCESS_PRESERVE_ARGS" not in credential_boundary
    deny_all = _shell_function("deny_all_agent_proxy_access")
    assert "tools.databricks.agent_proxy_access" in deny_all
    assert "tools.databricks.verify_agent_proxy_identity_boundary" in deny_all
    assert "--customer-resource-denial" in deny_all
    assert "--wait-customer-resource-denial" in deny_all
    assert "--account-id" in deny_all
    assert "run_with_agent_proxy_credentials" in deny_all
    assert "run_with_proof_signing_authority" in deny_all
    assert "run_with_proof_signing_authority" in _shell_function("converge_agent_proxy_boundary")

    trap = _shell_function("restore_rendered_sql_fail_closed")
    assert trap.index("compensate_agent_proxy_access") < trap.index(
        "tools.databricks.app_deployment_lease release"
    )


def test_pre_inventory_failure_cannot_deny_proxy_while_live_app_remains(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "pre-inventory-compensation.log"
    harness = tmp_path / "pre-inventory-compensation.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
APP_DEPLOYMENT_LEASE_ID=""
_GRANTS_APP_NAME=mip-app
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
AGENT_PROXY_ACCESS_MUTATED=0
APP_UPGRADE_STATE=first_install
stop_app_after_failed_deploy() {{
  printf 'live-app-remains\\n' >> {shlex.quote(str(calls))}
  return 0
}}
cleanup_failed_first_install_app() {{ return 0; }}
compensate_verifier_gateway_access() {{ return 0; }}
revoke_agent_runtime_bootstrap_grants() {{ return 0; }}
deny_all_agent_proxy_access() {{
  printf 'deny-all\\n' >> {shlex.quote(str(calls))}
  return 0
}}
{_shell_function("compensate_agent_proxy_access")}
{_deploy_exit_trap_block()}
false
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 1, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["live-app-remains"]


def test_signed_blue_gateway_is_preserved_and_audited_only_during_cutover() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    verifier_cutover = script[
        script.index(
            'step "converge dedicated verifier access to the green Gateway before cutover"'
        ) : script.index(
            'step "audit App access across every visible serving resource during cutover"'
        )
    ]
    assert '--preserve-gateway-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"' in (verifier_cutover)
    assert '--preserve-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"' in (verifier_cutover)
    assert "VERIFIER_GLOBAL_ACCESS_ARGS" in verifier_cutover
    assert '--serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"' in verifier_cutover
    assert '--serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"' in verifier_cutover
    assert (
        verifier_cutover.count(
            '--legacy-pinned-serving-endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"'
        )
        == 2
    )

    final_verifier = script[
        script.index(
            'step "re-audit final verifier global access after blue retirement"'
        ) : script.index('step "re-audit final App global serving access after blue retirement"')
    ]
    final_app = script[
        script.index(
            'step "re-audit final App global serving access after blue retirement"'
        ) : script.index(
            "# Persist only after retirement/finalization.",
        )
    ]
    for final_audit in (final_verifier, final_app):
        assert '--serving-endpoint "$MIP_AI_GATEWAY_ENDPOINT"' in final_audit
        assert "MIP_APP_ROLLBACK_GATEWAY_ENDPOINT" not in final_audit
        assert "--legacy-pinned-serving-endpoint" not in final_audit


def test_verifier_gateway_cutover_is_identity_pinned_and_compensated() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    cutover = script[
        script.index(
            'step "capture the verifier immutable identity before retirement admission"'
        ) : script.index(
            'step "audit agent-runtime access across every visible Genie and serving resource"'
        )
    ]
    capture_function = _shell_function("capture_verifier_identity")
    assert "converge_verifier_gateway_access capture" in capture_function
    capture = cutover.index("run capture_verifier_identity")
    prepare = cutover.index("cutover_agent_runtime_supervisor prepare")
    armed = cutover.index("VERIFIER_GATEWAY_CUTOVER_MUTATED=1")
    grant = cutover.index("tools.databricks.provision_m2m_oauth")
    assert capture < prepare < armed < grant
    assert '--verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in cutover
    assert '--verifier-scim-id "$MIP_VERIFIER_SCIM_ID"' in cutover
    assert "--expected-inventory-principal" in cutover
    assert "MIP_VERIFIER_SCIM_ID" in cutover

    compensation = _shell_function("compensate_verifier_gateway_access")
    assert "converge_verifier_gateway_access revoke-managed" in compensation
    assert '--expected-scim-id "$MIP_VERIFIER_SCIM_ID"' in compensation
    assert "--forbid-customer-serving" in compensation
    assert "--customer-resource-denial" in compensation
    assert "prove_exact_verifier_boundary" in compensation
    assert "MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX" in compensation
    assert compensation.index("revoke-managed") < compensation.index(
        "VERIFIER_GATEWAY_CUTOVER_MUTATED=0"
    )
    capture = script.index('capture_last_good_app "${AGENT_RUNTIME_BINDING_SHA256:-}"')
    final_audit = script.index(
        'step "re-audit final verifier global access after blue retirement"', capture
    )
    assert "VERIFIER_GATEWAY_CUTOVER_MUTATED=0" not in script[capture:final_audit]

    trap = _shell_function("restore_rendered_sql_fail_closed")
    assert trap.index("compensate_verifier_gateway_access") < trap.index(
        "tools.databricks.app_deployment_lease release"
    )


def test_failed_verifier_gateway_compensation_retains_signed_lease(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "failed-verifier-compensation.log"
    fake_python = tmp_path / "failed-verifier-compensation-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "failed-verifier-compensation.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
APP_DEPLOYMENT_LEASE_ID=lease-id
_GRANTS_APP_NAME=mip-app
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
VERIFIER_GATEWAY_CUTOVER_MUTATED=1
MIP_VERIFIER_SCIM_ID=verifier-scim-id
MIP_AI_GATEWAY_ENDPOINT=green-gateway
MIP_APP_ROLLBACK_GATEWAY_ENDPOINT=blue-gateway
MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX=mip.audit.blue_gateway
APP_SIGNED_BLUE_AVAILABLE=1
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=admin@example.com
PYTHON={shlex.quote(str(fake_python))}
RED=""
RST=""
stop_app_after_failed_deploy() {{ return 0; }}
cleanup_failed_first_install_app() {{ return 0; }}
compensate_agent_proxy_access() {{ return 0; }}
prove_exact_verifier_boundary() {{
  printf 'credential-proof %s\\n' "$1" >> {shlex.quote(str(calls))}
  return 1
}}
revoke_agent_runtime_bootstrap_grants() {{ return 0; }}
run_with_proof_signing_authority() {{ "$@"; }}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("compensate_verifier_gateway_access")}
{_deploy_exit_trap_block()}
false
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 90
    assert "retaining the signed deployment lease" in result.stderr
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert any("revoke-managed" in call for call in observed)
    assert any("--legacy-pinned-serving-endpoint" in call for call in observed)
    assert observed[-1] == "credential-proof blue-gateway"
    assert all("app_deployment_lease release" not in call for call in observed)


@pytest.mark.parametrize(("credential_result", "expected_flag"), [(0, "1"), (1, "1")])
def test_captured_green_verifier_compensation_preserves_and_reproves_green(
    tmp_path: Path,
    credential_result: int,
    expected_flag: str,
) -> None:
    calls = tmp_path / f"captured-green-verifier-{credential_result}.log"
    fake_python = tmp_path / f"captured-green-verifier-{credential_result}-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / f"captured-green-verifier-{credential_result}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_UPGRADE_STATE=green_captured_cleanup_pending
VERIFIER_GATEWAY_CUTOVER_MUTATED=1
MIP_VERIFIER_SCIM_ID=verifier-scim-id
MIP_AI_GATEWAY_ENDPOINT=green-gateway
MIP_AI_GATEWAY_INFERENCE_TABLE=mip.audit.green_gateway_payload
MIP_APP_ROLLBACK_GATEWAY_ENDPOINT=green-gateway
MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX=mip.audit.green_gateway
APP_SIGNED_BLUE_AVAILABLE=1
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=admin@example.com
PYTHON={shlex.quote(str(fake_python))}
RED=""
RST=""
prove_exact_verifier_boundary() {{
  printf 'credential-proof %s %s\\n' "$1" "$2" >> {shlex.quote(str(calls))}
  return {credential_result}
}}
load_captured_live_old_resources() {{
  CAPTURED_OLD_GATEWAY_LIVE=0
  CAPTURED_OLD_SUPERVISOR_LIVE=0
}}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("compensate_verifier_gateway_access")}
compensate_verifier_gateway_access
status=$?
printf 'status=%s flag=%s\\n' "$status" "$VERIFIER_GATEWAY_CUTOVER_MUTATED"
exit "$status"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == credential_result
    assert result.stdout.strip() == f"status={credential_result} flag={expected_flag}"
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert len(observed) == 2
    assert "audit_global_m2m_access" in observed[0]
    assert "--serving-endpoint green-gateway" in observed[0]
    assert "--legacy-pinned-serving-endpoint" not in observed[0]
    assert "revoke-managed" not in observed[0]
    assert observed[1] == ("credential-proof green-gateway mip.audit.green_gateway")


def test_captured_green_verifier_compensation_rejects_stale_signed_binding(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "captured-green-stale-binding.log"
    harness = tmp_path / "captured-green-stale-binding.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_UPGRADE_STATE=green_captured_cleanup_pending
VERIFIER_GATEWAY_CUTOVER_MUTATED=1
MIP_VERIFIER_SCIM_ID=verifier-scim-id
MIP_AI_GATEWAY_ENDPOINT=green-gateway
MIP_AI_GATEWAY_INFERENCE_TABLE=mip.audit.green_gateway_payload
MIP_APP_ROLLBACK_GATEWAY_ENDPOINT=green-gateway
MIP_APP_ROLLBACK_GATEWAY_INFERENCE_TABLE_PREFIX=mip.audit.stale_blue_gateway
APP_SIGNED_BLUE_AVAILABLE=1
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=admin@example.com
PYTHON=true
RED=""
RST=""
prove_exact_verifier_boundary() {{
  printf 'unexpected-proof\\n' >> {shlex.quote(str(calls))}
}}
load_captured_live_old_resources() {{
  CAPTURED_OLD_GATEWAY_LIVE=0
  CAPTURED_OLD_SUPERVISOR_LIVE=0
}}
{_shell_function("compensate_verifier_gateway_access")}
compensate_verifier_gateway_access
status=$?
printf 'status=%s flag=%s\\n' "$status" "$VERIFIER_GATEWAY_CUTOVER_MUTATED"
exit "$status"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 1
    assert result.stdout.strip() == "status=1 flag=1"
    assert "lacks its exact signed Gateway binding" in result.stderr
    assert not calls.exists()


@pytest.mark.parametrize(("credential_result", "expected_flag"), [(0, "0"), (1, "1")])
def test_first_install_verifier_compensation_clears_only_after_credential_denial(
    tmp_path: Path,
    credential_result: int,
    expected_flag: str,
) -> None:
    calls = tmp_path / f"first-install-verifier-{credential_result}.log"
    fake_python = tmp_path / f"first-install-verifier-{credential_result}-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / f"first-install-verifier-{credential_result}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
VERIFIER_GATEWAY_CUTOVER_MUTATED=1
MIP_VERIFIER_SCIM_ID=verifier-scim-id
MIP_AI_GATEWAY_ENDPOINT=green-gateway
APP_SIGNED_BLUE_AVAILABLE=0
DATABRICKS_VERIFIER_CLIENT_ID=verifier-client
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=admin@example.com
PYTHON={shlex.quote(str(fake_python))}
RED=""
RST=""
run_with_verifier_credentials() {{
  printf 'credential %s\\n' "$*" >> {shlex.quote(str(calls))}
  return {credential_result}
}}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("compensate_verifier_gateway_access")}
compensate_verifier_gateway_access
status=$?
printf 'status=%s flag=%s\\n' "$status" "$VERIFIER_GATEWAY_CUTOVER_MUTATED"
exit "$status"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == credential_result
    assert result.stdout.strip() == (f"status={credential_result} flag={expected_flag}")
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert "revoke-managed" in observed[0]
    assert "--forbid-customer-serving" in observed[1]
    assert "--customer-resource-denial" in observed[2]


@pytest.mark.parametrize(
    ("state", "signed_blue", "expected"),
    [
        ("first_install", 0, "deny"),
        ("unverified_existing", 0, "deny"),
        ("blue_quiesced", 1, "blue"),
        ("green_activating_quiesced", 1, "blue"),
        ("green_verified", 1, "green"),
        ("green_captured_cleanup_pending", 1, "green"),
    ],
)
def test_early_proxy_compensation_uses_durable_release_state(
    tmp_path: Path,
    state: str,
    signed_blue: int,
    expected: str,
) -> None:
    calls = tmp_path / f"proxy-compensation-{state}.log"
    harness = tmp_path / f"proxy-compensation-{state}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
AGENT_PROXY_ACCESS_MUTATED=1
APP_UPGRADE_STATE={shlex.quote(state)}
APP_SIGNED_BLUE_AVAILABLE={signed_blue}
MIP_AGENT_SUPERVISOR_ID=green-id
MIP_AGENT_SUPERVISOR_ENDPOINT=green-endpoint
MIP_AGENT_SUPERVISOR_ENDPOINT_ID=green-endpoint-id
MIP_APP_ROLLBACK_PROXY_MODE=exact-proxy
MIP_APP_ROLLBACK_SUPERVISOR_ID=blue-id
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT=blue-endpoint
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID=blue-endpoint-id
refresh_signed_blue_binding() {{ printf 'refresh\\n' >> {shlex.quote(str(calls))}; }}
converge_signed_blue_agent_proxy_boundary() {{ printf 'blue\\n' >> {shlex.quote(str(calls))}; }}
deny_all_agent_proxy_access() {{ printf 'deny\\n' >> {shlex.quote(str(calls))}; }}
converge_agent_proxy_boundary() {{ printf 'green\\n' >> {shlex.quote(str(calls))}; }}
prove_exact_agent_proxy_boundary() {{ printf 'proof %s\\n' "$1" >> {shlex.quote(str(calls))}; }}
load_captured_live_old_resources() {{
  CAPTURED_OLD_GATEWAY_LIVE=0
  CAPTURED_OLD_SUPERVISOR_LIVE=0
}}
{_shell_function("compensate_agent_proxy_access")}
compensate_agent_proxy_access
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    lines = calls.read_text(encoding="utf-8").splitlines()
    if expected == "deny":
        assert lines == ["deny"]
    elif expected == "blue":
        assert lines == ["refresh", "blue", "proof blue-id"]
    else:
        assert lines == ["green", "proof green-id"]


@pytest.mark.parametrize(
    ("old_mode", "expected_boundary", "expected_preserve"),
    [
        ("none", "converge", False),
        ("managed", "converge", False),
        ("direct", "audit", True),
        ("mixed", "audit", True),
    ],
)
def test_captured_proxy_partial_retirement_never_regrants_old(
    tmp_path: Path,
    old_mode: str,
    expected_boundary: str,
    expected_preserve: bool,
) -> None:
    calls = tmp_path / f"captured-proxy-{old_mode}.log"
    harness = tmp_path / f"captured-proxy-{old_mode}.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
AGENT_PROXY_ACCESS_MUTATED=1
APP_UPGRADE_STATE=green_captured_cleanup_pending
CAPTURED_PROXY_BOUNDARY_PROVEN=0
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
MIP_AGENT_SUPERVISOR_ID=green-id
MIP_AGENT_SUPERVISOR_ENDPOINT=green-endpoint
MIP_AGENT_SUPERVISOR_ENDPOINT_ID=green-endpoint-id
load_captured_live_old_resources() {{
  CAPTURED_OLD_GATEWAY_LIVE=0
  CAPTURED_OLD_SUPERVISOR_LIVE=1
  MIP_REPLACED_AGENT_SUPERVISOR_ID=old-id
  MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT=old-endpoint
  MIP_REPLACED_AGENT_SUPERVISOR_ENDPOINT_ID=old-endpoint-id
}}
pinned_query_access_mode() {{ printf '%s\\n' {shlex.quote(old_mode)}; }}
converge_agent_proxy_boundary() {{
  printf 'boundary %s\\n' "$*" >> {shlex.quote(str(calls))}
}}
prove_exact_agent_proxy_boundary() {{
  printf 'proof %s\\n' "$*" >> {shlex.quote(str(calls))}
}}
{_shell_function("compensate_agent_proxy_access")}
compensate_agent_proxy_access
printf 'proven=%s mutated=%s\\n' \
  "$CAPTURED_PROXY_BOUNDARY_PROVEN" "$AGENT_PROXY_ACCESS_MUTATED"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "proven=1 mutated=1"
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert f"boundary {expected_boundary} green-id green-endpoint green-endpoint-id" in observed[0]
    if expected_preserve:
        assert "--preserve-supervisor-id old-id" in observed[0]
        assert "--preserve-supervisor-id old-id" in observed[1]
        assert "--legacy-pinned-supervisor-endpoint old-endpoint" in observed[0]
        assert "--legacy-pinned-supervisor-endpoint" not in observed[1]
    else:
        assert "old-id" not in observed[0]
        assert "old-id" not in observed[1]


@pytest.mark.parametrize(("admin_result", "credential_result"), [(1, 0), (0, 1)])
def test_deny_all_runs_admin_and_proxy_credential_proofs_even_after_failure(
    tmp_path: Path,
    admin_result: int,
    credential_result: int,
) -> None:
    calls = tmp_path / "dual-authority-denial.log"
    fake_python = tmp_path / "dual-authority-denial-python.sh"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        f'if [[ "$*" == *agent_proxy_access* ]]; then exit {admin_result}; fi\n'
        f'if [[ "$*" == *verify_agent_proxy_identity_boundary* ]]; then exit {credential_result}; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "dual-authority-denial.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
PYTHON={shlex.quote(str(fake_python))}
DATABRICKS_AGENT_PROXY_CLIENT_ID=proxy-client
DATABRICKS_ACCOUNT_ID=account-id
DEPLOY_INVENTORY_PRINCIPAL=admin@example.com
run_with_account_identity() {{ "$@"; }}
run_with_agent_proxy_credentials() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("deny_all_agent_proxy_access")}
deny_all_agent_proxy_access
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 1
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "agent_proxy_access" in lines[0]
    assert "--customer-resource-denial" in lines[1]


def test_dry_run_exit_trap_never_mutates_agent_proxy_acl(tmp_path: Path) -> None:
    calls = tmp_path / "dry-run-proxy.log"
    harness = tmp_path / "dry-run-proxy-trap.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=1
AGENT_PROXY_ACCESS_MUTATED=1
APP_UPGRADE_STATE=first_install
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
APP_DEPLOYMENT_LEASE_ID=lease-id
_GRANTS_APP_NAME=mip-app
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
stop_app_after_failed_deploy() {{ return 0; }}
cleanup_failed_first_install_app() {{ return 0; }}
revoke_agent_runtime_bootstrap_grants() {{ return 0; }}
deny_all_agent_proxy_access() {{ printf 'deny\\n' >> {shlex.quote(str(calls))}; }}
converge_signed_blue_agent_proxy_boundary() {{ printf 'blue\\n' >> {shlex.quote(str(calls))}; }}
converge_agent_proxy_boundary() {{ printf 'green\\n' >> {shlex.quote(str(calls))}; }}
{_shell_function("compensate_agent_proxy_access")}
{_deploy_exit_trap_block()}
false
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 1
    assert not calls.exists()


def test_failed_proxy_compensation_retains_signed_deployment_lease(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "failed-compensation.log"
    fake_python = tmp_path / "failed-compensation-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "failed-compensation-trap.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_DEPLOYMENT_LEASE_HEARTBEAT_PID=""
APP_DEPLOYMENT_LEASE_ID=lease-id
_GRANTS_APP_NAME=mip-app
RESTORE_RENDERED_SQL_FAIL_CLOSED=0
AGENT_PROXY_ACCESS_MUTATED=1
APP_UPGRADE_STATE=green_verified
APP_SIGNED_BLUE_AVAILABLE=1
MIP_AGENT_SUPERVISOR_ID=green-id
MIP_AGENT_SUPERVISOR_ENDPOINT=green-endpoint
MIP_AGENT_SUPERVISOR_ENDPOINT_ID=green-endpoint-id
PYTHON={shlex.quote(str(fake_python))}
RED=""
RST=""
stop_app_after_failed_deploy() {{ return 0; }}
cleanup_failed_first_install_app() {{ return 0; }}
converge_agent_proxy_boundary() {{ printf 'admin-converge\\n' >> {shlex.quote(str(calls))}; }}
prove_exact_agent_proxy_boundary() {{
  printf 'credential-proof\\n' >> {shlex.quote(str(calls))}
  return 1
}}
deny_all_agent_proxy_access() {{ return 0; }}
refresh_signed_blue_binding() {{ return 0; }}
converge_signed_blue_agent_proxy_boundary() {{ return 0; }}
revoke_agent_runtime_bootstrap_grants() {{ return 0; }}
run_with_proof_signing_authority() {{ "$@"; }}
{_shell_function("compensate_agent_proxy_access")}
{_deploy_exit_trap_block()}
false
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 90
    assert "retaining the signed deployment lease" in result.stderr
    observed = calls.read_text(encoding="utf-8").splitlines()
    assert observed == ["admin-converge", "credential-proof"]


def test_legacy_v5_signed_blue_denies_new_proxy_instead_of_requiring_proxy_binding(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "legacy-blue.log"
    harness = tmp_path / "legacy-blue.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
MIP_APP_ROLLBACK_PROXY_MODE=legacy-proxyless
MIP_APP_ROLLBACK_RECORD_VERSION=5
MIP_APP_ROLLBACK_DEPLOYMENT_ID=legacy-deployment
RED=""
RST=""
deny_all_agent_proxy_access() {{ printf 'deny-all\\n' >> {shlex.quote(str(calls))}; }}
converge_agent_proxy_boundary() {{ printf 'exact-proxy\\n' >> {shlex.quote(str(calls))}; }}
{_shell_function("converge_signed_blue_agent_proxy_boundary")}
converge_signed_blue_agent_proxy_boundary
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == ["deny-all"]


def test_signed_blue_restore_refreshes_durable_binding_before_acl_and_restore(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "refresh-binding.log"
    fake_python = tmp_path / "refresh-binding-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "refresh-binding.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
APP_SIGNED_BLUE_AVAILABLE=1
LAKEBASE_RUNTIME_ACCESS_PROVEN=1
APP_FAIL_CLOSED_NAME=mip-app
APP_ROLLBACK_SECRET_SCOPE=mip
MIP_APP_URL=https://mip.example
MIP_BEARER_TOKEN=token
_GRANTS_WAREHOUSE_ID=warehouse
_GRANTS_CATALOG=mip
MIP_AI_GATEWAY_ENDPOINT=green-gateway
MIP_APP_ROLLBACK_PROXY_MODE=exact-proxy
MIP_APP_ROLLBACK_DEPLOYMENT_ID=blue-deployment
PYTHON={shlex.quote(str(fake_python))}
refresh_signed_blue_binding() {{
  MIP_APP_ROLLBACK_PROXY_MODE=exact-proxy
  MIP_APP_ROLLBACK_DEPLOYMENT_ID=green-deployment
  MIP_APP_ROLLBACK_SUPERVISOR_ID=blue-id
  MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT=blue-endpoint
  MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID=blue-endpoint-id
}}
converge_signed_blue_agent_proxy_boundary() {{
  printf 'converge %s\\n' "$MIP_APP_ROLLBACK_DEPLOYMENT_ID" >> {shlex.quote(str(calls))}
}}
prove_exact_agent_proxy_boundary() {{
  printf 'prove %s %s\\n' "$MIP_APP_ROLLBACK_DEPLOYMENT_ID" "$1" >> {shlex.quote(str(calls))}
}}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
converge_runtime_app_release_access() {{ return 0; }}
converge_app_treatment_access() {{ return 0; }}
{_shell_function("restore_signed_blue_while_quiesced")}
restore_signed_blue_while_quiesced
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    observed = calls.read_text(encoding="utf-8")
    assert observed.index("converge green-deployment") < observed.index(
        "app_deployment_rollback restore"
    )
    assert "prove green-deployment" in observed
    assert "--expected-rollback-deployment-id green-deployment" in observed


def test_preactivation_app_acl_journal_compensates_gateway_and_supervisor(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "preactivation-app-acl.log"
    fake_python = tmp_path / "preactivation-app-acl-python.sh"
    fake_python.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    harness = tmp_path / "preactivation-app-acl.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -u
DRY_RUN=0
APP_UPGRADE_STATE=blue_active
APP_SIGNED_BLUE_AVAILABLE=1
LAKEBASE_RUNTIME_ACCESS_PROVEN=1
PREACTIVATION_APP_ACL_MUTATED=0
PREACTIVATION_APP_REVOKE_ENDPOINTS=()
APP_FAIL_CLOSED_NAME=mip-app
APP_ROLLBACK_SECRET_SCOPE=mip
MIP_APP_URL=https://mip.example
MIP_BEARER_TOKEN=token
_GRANTS_WAREHOUSE_ID=warehouse
_GRANTS_CATALOG=mip
MIP_APP_ROLLBACK_GATEWAY_ENDPOINT=blue-gateway
MIP_APP_ROLLBACK_SUPERVISOR_ID=blue-supervisor-id
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT=blue-supervisor
MIP_APP_ROLLBACK_SUPERVISOR_ENDPOINT_ID=blue-supervisor-endpoint-id
MIP_APP_ROLLBACK_DEPLOYMENT_ID=blue-deployment
MIP_APP_ROLLBACK_PROXY_MODE=exact-proxy
MIP_AI_GATEWAY_ENDPOINT=green-gateway
MIP_AGENT_SUPERVISOR_ENDPOINT=green-supervisor
PYTHON={shlex.quote(str(fake_python))}
refresh_signed_blue_binding() {{ :; }}
converge_signed_blue_agent_proxy_boundary() {{ :; }}
prove_exact_agent_proxy_boundary() {{ :; }}
run_with_account_identity() {{ "$@"; }}
run_with_proof_signing_authority() {{ "$@"; }}
converge_runtime_app_release_access() {{ :; }}
converge_app_treatment_access() {{ :; }}
{_shell_function("journal_preactivation_app_acl_endpoint")}
{_shell_function("restore_signed_blue_while_quiesced")}
journal_preactivation_app_acl_endpoint "$MIP_AI_GATEWAY_ENDPOINT"
journal_preactivation_app_acl_endpoint "$MIP_AGENT_SUPERVISOR_ENDPOINT"
journal_preactivation_app_acl_endpoint "$MIP_AI_GATEWAY_ENDPOINT"
journal_preactivation_app_acl_endpoint "$MIP_APP_ROLLBACK_GATEWAY_ENDPOINT"
restore_signed_blue_while_quiesced
printf 'flag=%s count=%s\\n' "$PREACTIVATION_APP_ACL_MUTATED" \
  "${{#PREACTIVATION_APP_REVOKE_ENDPOINTS[@]}}"
""",
        encoding="utf-8",
    )

    result = subprocess.run(["bash", str(harness)], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "flag=0 count=0"
    rollback = calls.read_text(encoding="utf-8")
    assert rollback.count("--revoke-endpoint green-gateway") == 1
    assert rollback.count("--revoke-endpoint green-supervisor") == 1
    assert "--revoke-endpoint blue-gateway" not in rollback
    assert "--revoke-endpoint blue-supervisor" not in rollback


def test_historical_runtime_cleanup_precedes_green_provisioning_and_preserves_signed_blue() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    stale_resume = script.index(
        'step "resume exact stale runtime retirement under the signed-blue boundary"'
    )
    cleanup = script.index("-m tools.databricks.reconcile_historical_agent_endpoints cleanup")
    supervisor = script.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    gateway = script.index(
        'step "provision the governed outer Gateway under agent-runtime authority"'
    )

    assert stale_resume < cleanup < supervisor < gateway
    stale_resume_block = script[stale_resume:cleanup]
    assert "-m tools.databricks.cutover_agent_runtime_supervisor" in stale_resume_block
    assert "resume-stale-journal" in stale_resume_block
    assert "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON" in stale_resume_block
    assert "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON" in stale_resume_block
    assert '--app-application-id "$APP_SP_CLIENT_ID"' in stale_resume_block
    supervisor_block = script[supervisor:gateway]
    assert (
        "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON=" '"${MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON:-}"'
    ) in supervisor_block
    assert '--verifier-application-id "$DATABRICKS_VERIFIER_CLIENT_ID"' in (stale_resume_block)
    assert '--verifier-scim-id "$MIP_VERIFIER_SCIM_ID"' in stale_resume_block
    assert '--proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"' in (stale_resume_block)
    cleanup_block = script[cleanup:supervisor]
    assert '--preserve-gateway-json "$MIP_APP_ROLLBACK_GATEWAY_PIN_JSON"' in script
    assert '--preserve-supervisor-json "$MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON"' in script
    assert '--app-scim-id "$APP_SP_SCIM_ID"' in cleanup_block
    assert '--verifier-scim-id "$MIP_VERIFIER_SCIM_ID"' in cleanup_block
    assert '--proxy-application-id "$DATABRICKS_AGENT_PROXY_CLIENT_ID"' in cleanup_block
    assert '--rollback-scope "$APP_ROLLBACK_SECRET_SCOPE"' in cleanup_block
    assert "capture_verifier_identity" in script[:cleanup]
    assert "-m tools.databricks.cutover_agent_runtime_supervisor export-journal" in script[:cleanup]
    historical_journal = script[script.rindex("run_as_m2m_identity", 0, cleanup) : cleanup]
    assert "DATABRICKS_AGENT_RUNTIME_CLIENT_ID" in historical_journal
    assert "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET" in historical_journal
    assert "merge_historical_cutover_journal_preservation" in historical_journal
    assert "STALE_CUTOVER_JOURNAL_PENDING=1" in _shell_function(
        "merge_historical_cutover_journal_preservation"
    )
    assert "--preserve-retirement-gateway-json" in historical_journal
    assert '"$MIP_REPLACED_AGENT_GATEWAY_PIN_JSON"' in historical_journal
    assert "--preserve-retirement-supervisor-json" in historical_journal
    assert '"$MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON"' in historical_journal
    assert "--preserve-gateway-name" not in cleanup_block
    assert "--preserve-supervisor-id" not in cleanup_block
    stale_clear = script.index(
        'step "prove historical retirement and clear only the stale signed cutover journal"',
        cleanup,
    )
    supervisor = script.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    assert cleanup < stale_clear < supervisor
    stale_clear_block = script[stale_clear:supervisor]
    assert "MIP_CUTOVER_SIGNED_BLUE_GATEWAY_PIN_JSON" in stale_clear_block
    assert "MIP_CUTOVER_SIGNED_BLUE_SUPERVISOR_PIN_JSON" in stale_clear_block
    assert "DATABRICKS_AGENT_RUNTIME_CLIENT_ID" in stale_clear_block
    assert "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET" in stale_clear_block
    assert "-m tools.databricks.cutover_agent_runtime_supervisor clear-journal" in stale_clear_block


def test_current_cutover_journal_retry_deduplicates_signed_blue_and_rejects_collisions(
    tmp_path: Path,
) -> None:
    blue_gateway = {
        "name": "blue-gateway",
        "endpoint_id": "blue-gateway-id",
        "creator": "runtime-client",
    }
    blue_supervisor = {
        "supervisor_id": "blue-supervisor-id",
        "endpoint": "blue-supervisor",
        "endpoint_id": "blue-supervisor-endpoint-id",
        "creator": "runtime-client",
    }
    current_gateway = dict(blue_gateway)
    current_supervisor = dict(blue_supervisor)
    gateway_json = json.dumps(blue_gateway, separators=(",", ":"))
    supervisor_json = json.dumps(blue_supervisor, separators=(",", ":"))
    harness = tmp_path / "current-cutover-journal.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -eu
PYTHON={shlex.quote(sys.executable)}
RED=""
RST=""
STALE_CUTOVER_JOURNAL_PENDING=0
MIP_APP_ROLLBACK_GATEWAY_PIN_JSON={shlex.quote(gateway_json)}
MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON={shlex.quote(supervisor_json)}
MIP_REPLACED_AGENT_GATEWAY_PIN_JSON={shlex.quote(json.dumps(current_gateway))}
MIP_REPLACED_AGENT_SUPERVISOR_PIN_JSON={shlex.quote(json.dumps(current_supervisor))}
HISTORICAL_ENDPOINT_PRESERVE_ARGS=(
  --preserve-gateway-json "$MIP_APP_ROLLBACK_GATEWAY_PIN_JSON"
  --preserve-supervisor-json "$MIP_APP_ROLLBACK_SUPERVISOR_PIN_JSON"
)
{_shell_function("plan_historical_cutover_journal_preservation")}
{_shell_function("merge_historical_cutover_journal_preservation")}
merge_historical_cutover_journal_preservation
printf '%s\\n' "${{HISTORICAL_ENDPOINT_PRESERVE_ARGS[@]}}"
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(harness)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "--preserve-gateway-json",
        gateway_json,
        "--preserve-supervisor-json",
        supervisor_json,
    ]

    historical_supervisor = {
        "supervisor_id": "historical-supervisor-id",
        "endpoint": "historical-supervisor",
        "endpoint_id": "historical-supervisor-endpoint-id",
        "creator": "runtime-client",
    }
    historical_supervisor_json = json.dumps(historical_supervisor)
    mixed_harness = tmp_path / "mixed-current-cutover-journal.sh"
    mixed_harness.write_text(
        harness.read_text(encoding="utf-8").replace(
            shlex.quote(json.dumps(current_supervisor)),
            shlex.quote(historical_supervisor_json),
        ),
        encoding="utf-8",
    )
    mixed = subprocess.run(
        ["bash", str(mixed_harness)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert mixed.returncode == 0, mixed.stderr
    assert mixed.stdout.splitlines() == [
        "--preserve-gateway-json",
        gateway_json,
        "--preserve-supervisor-json",
        supervisor_json,
        "--preserve-retirement-supervisor-json",
        historical_supervisor_json,
    ]

    historical_gateway = {
        "name": "historical-gateway",
        "endpoint_id": "historical-gateway-id",
        "creator": "runtime-client",
    }
    historical_gateway_json = json.dumps(historical_gateway)
    gateway_harness = tmp_path / "gateway-current-cutover-journal.sh"
    gateway_harness.write_text(
        harness.read_text(encoding="utf-8").replace(
            shlex.quote(json.dumps(current_gateway)),
            shlex.quote(historical_gateway_json),
        ),
        encoding="utf-8",
    )
    gateway_only = subprocess.run(
        ["bash", str(gateway_harness)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert gateway_only.returncode == 0, gateway_only.stderr
    assert gateway_only.stdout.splitlines() == [
        "--preserve-gateway-json",
        gateway_json,
        "--preserve-supervisor-json",
        supervisor_json,
        "--preserve-retirement-gateway-json",
        historical_gateway_json,
    ]

    colliding_gateway = {**current_gateway, "endpoint_id": "reused-name-new-id"}
    collision_harness = tmp_path / "colliding-cutover-journal.sh"
    collision_harness.write_text(
        harness.read_text(encoding="utf-8").replace(
            shlex.quote(json.dumps(current_gateway)),
            shlex.quote(json.dumps(colliding_gateway)),
        ),
        encoding="utf-8",
    )
    collision = subprocess.run(
        ["bash", str(collision_harness)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert collision.returncode != 0
    assert "collides with the signed-blue Gateway name or immutable endpoint ID" in (
        collision.stderr
    )


def test_completed_redeploy_supervisor_command_pins_proxy_identity_explicitly() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index(
        'step "provision the managed Supervisor under the dedicated agent-runtime identity"'
    )
    end = script.index(
        'step "grant and globally audit the dedicated Supervisor proxy caller"', start
    )
    tokens = _continued_command_tokens(
        script[start:end], "tools.databricks.provision_agentic_resources"
    )

    proxy_flag = tokens.index("--proxy-caller-application-id")
    assert tokens[proxy_flag + 1] == "$DATABRICKS_AGENT_PROXY_CLIENT_ID"
    assert "--skip-gateway" in tokens


def test_dev_workflow_credential_repair_is_explicit_bounded_and_non_deploying() -> None:
    workflow = yaml.load(DEPLOY_DEV.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    workflow_inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    repair_input = workflow_inputs["repair_normal_credential"]
    assert repair_input["type"] == "boolean"
    assert repair_input["default"] == "false"
    break_glass_input = workflow_inputs["acknowledge_single_maintainer_break_glass"]
    assert break_glass_input["type"] == "boolean"
    assert break_glass_input["default"] == "false"

    steps = workflow["jobs"]["deploy"]["steps"]
    refusal_job = workflow["jobs"]["refuse-unacknowledged"]
    deploy_job = workflow["jobs"]["deploy"]
    assert refusal_job["if"] == ("${{ !inputs.acknowledge_single_maintainer_break_glass }}")
    refusal_steps = refusal_job["steps"]
    assert len(refusal_steps) == 1
    assert refusal_steps[0]["name"] == ("Refuse an unacknowledged single-maintainer deployment")
    assert "exit 1" in refusal_steps[0]["run"]
    assert deploy_job["if"] == ("${{ inputs.acknowledge_single_maintainer_break_glass }}")
    repair_steps = [
        step for step in steps if step.get("name") == "Repair normal operator OAuth credential"
    ]
    deploy_steps = [step for step in steps if step.get("name") == "Deploy dev Databricks App"]
    assert len(repair_steps) == 1
    assert len(deploy_steps) == 1
    repair = repair_steps[0]
    assert repair["if"] == "${{ inputs.repair_normal_credential }}"
    assert repair["env"] == {
        "DATABRICKS_AUTH_TYPE": "pat",
        "DATABRICKS_HOST": "${{ secrets.DATABRICKS_HOST }}",
        "DATABRICKS_TOKEN": "${{ secrets.DATABRICKS_TOKEN }}",
        "DATABRICKS_CLIENT_ID": "${{ secrets.DATABRICKS_CLIENT_ID }}",
        "GH_TOKEN": "${{ secrets.MIP_GITHUB_CREDENTIAL_SINK_TOKEN }}",
        "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": ("${{ secrets.MIP_AI_GATEWAY_PROOF_SIGNING_KEY }}"),
        "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY": (
            "${{ secrets.MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY }}"
        ),
        "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS": (
            "${{ vars.MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS }}"
        ),
        "MIP_APP_NAME": "${{ vars.MIP_APP_NAME || 'mip-app' }}",
        "MIP_DEPLOYMENT_SOURCE_GIT_SHA": "${{ github.sha }}",
        "MIP_M2M_GITHUB_REPOSITORY": "${{ github.repository }}",
    }
    assert 'MIP_AI_GATEWAY_PROOF_VERIFY_KEY="$(' in repair["run"]
    assert "derive_gateway_proof_verify_key" in repair["run"]
    assert 'os.environ["MIP_AI_GATEWAY_PROOF_SIGNING_KEY"]' in repair["run"]
    assert "export MIP_AI_GATEWAY_PROOF_VERIFY_KEY" in repair["run"]
    assert "MIP_AI_GATEWAY_PROOF_SIGNING_KEY is invalid" in repair["run"]
    repair_preflight = repair["run"].partition("python -m tools.databricks.provision_m2m_oauth")[0]

    def encoded_seed(byte: int) -> str:
        return base64.urlsafe_b64encode(bytes([byte]) * 32).rstrip(b"=").decode()

    def run_preflight(
        *,
        signing_key: str,
        previous_verify_key: str = "",
        historical_verify_keys: str = "",
        post_preflight: str,
    ) -> subprocess.CompletedProcess[str]:
        preflight_env = {
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
            "DATABRICKS_HOST": "https://example.invalid",
            "DATABRICKS_TOKEN": "test-token",
            "DATABRICKS_CLIENT_ID": "test-client-id",
            "GH_TOKEN": "test-github-token",
            "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": signing_key,
            "MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY": previous_verify_key,
            "MIP_AI_GATEWAY_PROOF_HISTORICAL_VERIFY_KEYS": historical_verify_keys,
            "MIP_APP_NAME": "test-app",
            "MIP_DEPLOYMENT_SOURCE_GIT_SHA": "a" * 40,
            "MIP_M2M_GITHUB_REPOSITORY": "owner/repo",
        }
        return subprocess.run(
            ["bash", "-c", repair_preflight + post_preflight],
            cwd=REPO,
            env=preflight_env,
            text=True,
            capture_output=True,
            check=False,
        )

    historical_verify_key = derive_gateway_proof_verify_key(encoded_seed(1))
    previous_verify_key = derive_gateway_proof_verify_key(encoded_seed(2))
    current_signing_key = encoded_seed(3)
    current_verify_key = derive_gateway_proof_verify_key(current_signing_key)
    ordered_probe = run_preflight(
        signing_key=current_signing_key,
        previous_verify_key=previous_verify_key,
        historical_verify_keys=historical_verify_key,
        post_preflight=(
            "\npython - <<'PY'\n"
            "import json\n"
            "from tools.databricks.app_deployment_lease_support import key_registry\n"
            "print(json.dumps(key_registry()))\n"
            "PY\n"
        ),
    )
    assert ordered_probe.returncode == 0, ordered_probe.stdout + ordered_probe.stderr
    assert json.loads(ordered_probe.stdout) == [
        historical_verify_key,
        previous_verify_key,
        current_verify_key,
    ]

    invalid_signing_key = "A" * 42
    invalid_probe = run_preflight(
        signing_key=invalid_signing_key,
        post_preflight="\nprintf 'PROVISION_SENTINEL\\n'\n",
    )
    assert invalid_probe.returncode != 0
    assert "::error::MIP_AI_GATEWAY_PROOF_SIGNING_KEY is invalid" in invalid_probe.stdout
    assert "PROVISION_SENTINEL" not in invalid_probe.stdout
    assert invalid_signing_key not in invalid_probe.stdout + invalid_probe.stderr
    assert deploy_steps[0]["if"] == "${{ !inputs.repair_normal_credential }}"
    assert _continued_command_tokens(repair["run"], "tools.databricks.provision_m2m_oauth") == [
        "python",
        "-m",
        "tools.databricks.provision_m2m_oauth",
        "--identity-role",
        "normal",
        "--expected-application-id",
        "$DATABRICKS_CLIENT_ID",
        "--app-name",
        "$MIP_APP_NAME",
        "--no-grant-can-use",
        "--gh-repo",
        "$GITHUB_REPOSITORY",
        "--rotate",
        "--set-gh-secrets",
    ]
