"""The deploy reads the client profile's Supervisor name once, and every site uses it.

The preflight resolves ``MIP_AGENT_SUPERVISOR_NAME`` like the Genie space name
(``deployment_control_value``: a non-empty shell value, else ``.env.local``,
else the canonical ``Mortgage Growth Agent``) and exports it. Before, every
site read ``${MIP_AGENT_SUPERVISOR_NAME:-Mortgage Growth Agent}``, which no
profile ever set, so an agentic-tier client naming another Supervisor had the
canonical name probed, cleaned up, created, provisioned and cut over while its
App payload carried its own.

* Client #1 (the sandbox's profile, canonical name) plans the two lines that
  name the Supervisor exactly as origin/main 711af8be did; its full agentic
  and core dry-run transcripts are byte-identical once the commit SHA and the
  mktemp suffixes are normalised (see the PR).
* A profile naming another Supervisor plans that name at both lines and the
  canonical name nowhere.
* No deploy-shell site keeps a default of its own: each Supervisor-name flag
  passes the resolved value, the cutover CLI's prepare / retire / finalize /
  pin-journal get it explicitly (their parser defaults to the canonical name),
  and ``run_as_m2m_identity`` carries it into the clean environment, where
  ``provision_agentic_resources`` reads it (the wrapper un-exports everything
  else).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

from tests.fixtures.deploy_sandbox import ANSI, dry_run, sandbox
from tests.fixtures.deploy_script import DEPLOY_SCRIPT, deploy_contract_text

REPO = Path(__file__).resolve().parents[2]
CANONICAL = "Mortgage Growth Agent"
OTHER = "Harbor Growth Supervisor"
PREFLIGHT = REPO / "scripts" / "lib" / "deploy_step_preflight_gates.sh"
IDENTITY_LIB = REPO / "scripts" / "lib" / "deploy_identity_lifecycle.sh"
_PROFILE_LINE = f"MIP_AGENT_SUPERVISOR_NAME='{CANONICAL}'\n"


def _site_lines(name: str) -> tuple[str, str]:
    """The dry run's two Supervisor-naming lines, stable parts only (the cleanup line also carries the commit and a mktemp path)."""

    return (
        f"  would probe read-only: the creator of Supervisor '{name}' (MIP_RETIRE_HUMAN_SUPERVISOR), "
        "Genie group grants, the signed rollback record in mip-app-rollback, governed owners and "
        "owner-equivalent grantees, App serving access, and one Genie turn",
        "$ .venv/bin/python -m tools.databricks.reconcile_historical_agent_endpoints cleanup "
        "--runtime-application-id 00000000-0000-4000-8000-000000000009 --catalog mip --genie-space-id  "
        f"--supervisor-name {name} --app-name mip-app --rollback-scope mip-app-rollback ",
    )


def _plan(tmp_path: Path, supervisor_name: str | None = None) -> tuple[str, str]:
    checkout, env, _log = sandbox(tmp_path)
    if supervisor_name is not None:
        dotenv = checkout / ".env.local"
        text = dotenv.read_text(encoding="utf-8")
        assert (
            text.count(_PROFILE_LINE) == 1
        ), "the sandbox profile names the canonical Supervisor once"
        dotenv.write_text(
            text.replace(_PROFILE_LINE, f"MIP_AGENT_SUPERVISOR_NAME='{supervisor_name}'\n"),
            encoding="utf-8",
        )
    result = dry_run(checkout, env)
    out, err = ANSI.sub("", result.stdout), ANSI.sub("", result.stderr)
    assert result.returncode == 0, out[-3000:] + err[-3000:]
    return out, err


def _naming(out: str, name: str) -> list[str]:
    return [line for line in out.splitlines() if name in line]


def test_client_one_plans_the_canonical_name_at_both_sites_as_before(tmp_path: Path) -> None:
    out, _err = _plan(tmp_path)
    lines = _naming(out, CANONICAL)

    assert len(lines) == 2, lines
    for line, expected in zip(lines, _site_lines(CANONICAL), strict=True):
        assert line.startswith(expected), (expected, line)


def test_a_profile_naming_another_supervisor_plans_it_at_every_site(tmp_path: Path) -> None:
    out, err = _plan(tmp_path, OTHER)
    lines = _naming(out, OTHER)

    assert len(lines) == 2, lines
    for line, expected in zip(lines, _site_lines(OTHER), strict=True):
        assert line.startswith(expected), (expected, line)
    assert CANONICAL not in out and CANONICAL not in err


# --- the deploy shell ---------------------------------------------------------------------


def _deploy_shell_files() -> list[Path]:
    return [DEPLOY_SCRIPT, *sorted((REPO / "scripts" / "lib").glob("*.sh"))]


def test_no_deploy_shell_site_keeps_a_supervisor_default_of_its_own() -> None:
    # A non-empty fallback; the wrapper's empty ``:-}`` only keeps ``set -u`` callers safe.
    defaults = re.compile(r"\$\{MIP_AGENT_SUPERVISOR_NAME:?[-=][^}]")
    hits = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _deploy_shell_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if defaults.search(line)
    ]
    assert hits == []

    # The canonical name lives in one place: the preflight's resolution.
    named = [
        (path.relative_to(REPO), line.strip())
        for path in _deploy_shell_files()
        for line in path.read_text(encoding="utf-8").splitlines()
        if CANONICAL in line and not line.lstrip().startswith("#")
    ]
    assert named == [
        (
            PREFLIGHT.relative_to(REPO),
            f"MIP_AGENT_SUPERVISOR_NAME=\"$(deployment_control_value MIP_AGENT_SUPERVISOR_NAME '{CANONICAL}')\"",
        )
    ]
    preflight = PREFLIGHT.read_text(encoding="utf-8")
    assert re.search(r"^export [A-Z_ ]*\bMIP_AGENT_SUPERVISOR_NAME\b", preflight, re.MULTILINE)


def test_every_supervisor_name_flag_passes_the_resolved_value() -> None:
    script = deploy_contract_text()
    flags = re.findall(r"--(?:supervisor|canonical)-name\s+(\S+)", script)

    assert len(flags) >= 14, flags
    assert set(flags) == {'"$MIP_AGENT_SUPERVISOR_NAME"'}


def _command_block(script: str, start: int) -> str:
    """One invocation: backslash-continued lines, or the lines of the ``( ... )`` array it opens."""

    lines = script[script.rfind("\n", 0, start) + 1 :].splitlines()
    if lines[0].rstrip().endswith("\\"):
        block = []
        for line in lines:
            block.append(line)
            if not line.rstrip().endswith("\\"):
                break
        return "\n".join(block)
    end = next(index for index, line in enumerate(lines) if line.strip() == ")")
    return "\n".join(lines[: end + 1])


def test_the_cutover_cli_gets_the_resolved_name_at_every_call() -> None:
    script = deploy_contract_text()
    resolved = '--canonical-name "$MIP_AGENT_SUPERVISOR_NAME"'
    green = _command_block(script, script.index("AGENT_RUNTIME_GREEN_ARGS=("))
    assert resolved in green

    calls = list(
        re.finditer(
            r"tools\.databricks\.cutover_agent_runtime_supervisor\s+(prepare|retire|finalize|pin-journal)\b",
            script,
        )
    )
    assert sorted(call.group(1) for call in calls) == [
        "finalize",
        "finalize",
        "pin-journal",
        "prepare",
        "retire",
        "retire",
    ]
    for call in calls:
        block = _command_block(script, call.start())
        assert resolved in block or '"${AGENT_RUNTIME_GREEN_ARGS[@]}"' in block, block


def _shell_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    return text[start : text.index("\n}\n", start) + len("\n}\n")]


def _clean_env_supervisor_name(tmp_path: Path, parent_value: str | None) -> str:
    """What provision_agentic_resources' parser resolves inside run_as_m2m_identity."""

    observed = tmp_path / "observed.txt"
    parent = (
        ""
        if parent_value is None
        else f"export MIP_AGENT_SUPERVISOR_NAME={shlex.quote(parent_value)}"
    )
    probe = (
        "from tools.databricks.agentic_provisioning_cli import build_parser; "
        "print(build_parser(default_sync_tables=()).parse_args([]).supervisor_name)"
    )
    harness = tmp_path / "clean-env-harness.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
DRY_RUN=0
DIM=""
RST=""
MIP_DATABRICKS_WORKSPACE_HOST=https://workspace.example
DATABRICKS_AGENT_RUNTIME_CLIENT_ID=runtime-client
DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET=runtime-secret
{parent}
{_shell_function(IDENTITY_LIB.read_text(encoding="utf-8"), "run_as_m2m_identity")}
run_as_m2m_identity agent-runtime DATABRICKS_AGENT_RUNTIME_CLIENT_ID DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET \\
  bash -c '"$1" -c "$2" > "$3"' _ {shlex.quote(sys.executable)} {shlex.quote(probe)} {shlex.quote(str(observed))}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(harness)], cwd=REPO, text=True, capture_output=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr
    return observed.read_text(encoding="utf-8").strip()


def test_the_agent_runtime_clean_environment_carries_the_resolved_name(tmp_path: Path) -> None:
    assert _clean_env_supervisor_name(tmp_path, OTHER) == OTHER
    # Control: the wrapper adds nothing of its own; unresolved, the parser keeps its default.
    assert _clean_env_supervisor_name(tmp_path, None) == CANONICAL
