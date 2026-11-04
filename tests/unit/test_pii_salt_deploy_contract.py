"""deploy.sh checks the write-once pii-salt before the lease and writes it only through the tool.

Step 4d once listed the scope with ``databricks secrets list-secrets mip`` and
read any failure as "key absent", so a failed listing put a fresh salt over
the live one. The step now calls ``tools.databricks.pii_salt_scope ensure``,
and step 0 runs the same inventory read-only (``check``) before the signed
lease and the App stop. The blocks are read from ``deploy_entrypoint_text()``
(the sliced deploy.sh layout) and driven with a stub module.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools.databricks import pii_salt_scope

ROOT = Path(__file__).resolve().parents[2]
CHECK_START = "  # >>> pii-salt pre-stop check >>>"
CHECK_END = "  # <<< pii-salt pre-stop check <<<"
CONTROL_START = "# >>> pii-salt scope control >>>"
CONTROL_END = "# <<< pii-salt scope control <<<"
ENSURE_STEP = 'step "provision pii-salt secret scope (create-if-missing, never rotate)"'
NEXT_STEP = 'step "provision dedicated signed App rollback-contract secret scope"'
SCOPE_ARG = '--scope "$MIP_PII_SALT_SCOPE"'
POLICY_ARG = '--require-existing "$PII_SALT_REQUIRE_EXISTING"'


def _between(start: str, end: str, *, inclusive_end: bool = True) -> str:
    text = deploy_entrypoint_text()
    assert text.count(start) == 1 and text.count(end) == 1, (start, end)
    stop = text.index(end) + (len(end) if inclusive_end else 0)
    return text[text.index(start) : stop]


def _code(block: str) -> list[str]:
    return [line for line in block.splitlines() if not line.lstrip().startswith("#")]


def _shell_function(name: str) -> str:
    text = deploy_entrypoint_text()
    start = text.index(f"\n{name}() {{\n") + 1
    return text[start : text.index("\n}\n", start) + len("\n}\n")]


# --- Ordering and shape --------------------------------------------------------


def test_the_check_runs_before_the_lease_and_every_app_stop() -> None:
    text = deploy_entrypoint_text()
    steps = text.index('step "preflight — check .env.local, databricks CLI, venv"')
    control = text.index(CONTROL_START, steps)
    probe = text.index("tools.databricks.deploy_prestop_probes", steps)
    check = text.index(CHECK_START, steps)
    lease_root = text.index("tools.databricks.app_deployment_lease recovery-root", steps)
    lease = text.index('step "acquire signed workspace lease for the exact App deployment"', steps)
    first_arm = text.index("APP_FAIL_CLOSED_ARMED=1", steps)
    ensure = text.index(ENSURE_STEP, steps)

    assert control < probe < check < lease_root < lease < ensure
    assert check < first_arm
    # Since M0-PR8 every stop is a named function of the window library
    # (scripts/lib/deploy_quiesce_window_lifecycle.sh) the step files call.
    for app_stop in (
        "run stop_unsigned_rebase_app",
        "--mode quarantine",
        'prove_or_restore_signed_blue "$APP_ROLLBACK_BINDING_ENV"',
    ):
        assert check < text.index(app_stop, steps), app_stop


def test_the_tool_is_called_exactly_twice_with_one_scope_and_one_policy() -> None:
    text = deploy_entrypoint_text()
    check = "\n".join(_code(_between(CHECK_START, CHECK_END)))
    ensure = "\n".join(_code(_between(ENSURE_STEP, NEXT_STEP, inclusive_end=False)))

    assert text.count("-m tools.databricks.pii_salt_scope") == 2
    assert '"$PYTHON" -m tools.databricks.pii_salt_scope check' in check
    assert 'run "$PYTHON" -m tools.databricks.pii_salt_scope ensure' in ensure
    for block in (check, ensure):
        assert SCOPE_ARG in block and POLICY_ARG in block
    assert text.count("\nPII_SALT_REQUIRE_EXISTING=0\n") == 1
    assert len(re.findall(r"^\s*PII_SALT_REQUIRE_EXISTING=", text, flags=re.MULTILINE)) == 1


def test_the_check_is_read_only_and_never_run_wrapped() -> None:
    code = _code(_between(CHECK_START, CHECK_END))

    assert not any(line.lstrip().startswith("run ") for line in code)
    assert not any("run_with_" in line or "run_as_m2m_identity" in line for line in code)
    assert sum("exit 2" in line for line in code) == 1
    assert " ensure" not in "\n".join(code)


def test_step_4d_writes_only_through_the_tool() -> None:
    ensure = "\n".join(_code(_between(ENSURE_STEP, NEXT_STEP, inclusive_end=False)))

    for retired in ("databricks secrets", "databricks api", "secrets/put", "mktemp", "run_redacted"):
        assert retired not in ensure, retired


def test_the_scope_control_resolves_like_every_deployment_control() -> None:
    control = _between(CONTROL_START, CONTROL_END)

    assert 'MIP_PII_SALT_SCOPE="$(deployment_control_value MIP_PII_SALT_SCOPE mip)"' in control
    assert deploy_entrypoint_text().count("deployment_control_value MIP_PII_SALT_SCOPE") == 1


def test_the_only_accepted_scope_is_the_one_the_readers_use() -> None:
    # Until the silver SQL and the Lakeflow pipeline take the control, the
    # deploy accepts only the literal they read; this pins all three together.
    pipeline = (ROOT / "pipelines" / "lakeflow" / "mip_feature_pipeline.py").read_text(
        encoding="utf-8"
    )
    literal = f"secret('{pii_salt_scope.DEFAULT_SCOPE}', '{pii_salt_scope.SALT_KEY}')"

    assert f'_PII_SALT_SCOPE = "{pii_salt_scope.DEFAULT_SCOPE}"' in pipeline
    assert f'_PII_SALT_KEY = "{pii_salt_scope.SALT_KEY}"' in pipeline
    for sql in ("silver_property_master.sql", "silver_property_owners.sql"):
        text = (ROOT / "sql" / "transformations" / sql).read_text(encoding="utf-8")
        assert literal in text, sql
        assert len(re.findall(r"secret\(\s*'([^']+)'", text)) == text.count(literal), sql
    assert f'"$MIP_PII_SALT_SCOPE" != "{pii_salt_scope.DEFAULT_SCOPE}"' in _between(
        CONTROL_START, CONTROL_END
    )


# --- Driven blocks -------------------------------------------------------------


def _stub_tool(repo: Path, *, exit_code: int) -> Path:
    calls = repo / "calls.log"
    module = repo / "tools" / "databricks" / "pii_salt_scope.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "print('[pii-salt] stub')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return calls


def _drive(repo: Path, body: str, *, dry_run: int, prelude: str = "") -> subprocess.CompletedProcess[str]:
    script = repo / "block.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'RED=""\nRST=""\nBOLD=""\nDIM=""\nSTEP=0\nDRY_RUN={dry_run}\n'
        f"PYTHON={shlex.quote(sys.executable)}\n"
        "MIP_PII_SALT_SCOPE=mip\nPII_SALT_REQUIRE_EXISTING=0\n"
        f"{prelude}{body}\necho reached-the-next-step\n",
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    return subprocess.run(
        ["bash", str(script)], cwd=repo, env=env, text=True, capture_output=True, check=False
    )


def test_a_clean_check_lets_the_deploy_reach_the_lease(tmp_path: Path) -> None:
    calls = _stub_tool(tmp_path, exit_code=0)

    result = _drive(tmp_path, _between(CHECK_START, CHECK_END), dry_run=0)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8") == "check --scope mip --require-existing 0\n"
    assert "reached-the-next-step" in result.stdout


@pytest.mark.parametrize("exit_code", (1, 2))
def test_a_refused_check_stops_before_the_lease_with_exit_2(tmp_path: Path, exit_code: int) -> None:
    _stub_tool(tmp_path, exit_code=exit_code)

    result = _drive(tmp_path, _between(CHECK_START, CHECK_END), dry_run=0)

    assert result.returncode == 2
    assert "the pii-salt check was refused before the lease and the App stop" in result.stderr
    assert "reached-the-next-step" not in result.stdout


def _ensure_step() -> str:
    return _between(ENSURE_STEP, NEXT_STEP, inclusive_end=False)


def _helpers() -> str:
    return _shell_function("step") + _shell_function("run")


def test_step_4d_runs_ensure_with_the_resolved_scope(tmp_path: Path) -> None:
    calls = _stub_tool(tmp_path, exit_code=0)

    result = _drive(tmp_path, _ensure_step(), dry_run=0, prelude=_helpers())

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8") == "ensure --scope mip --require-existing 0\n"
    assert "$ " + sys.executable + " -m tools.databricks.pii_salt_scope ensure" in result.stdout


@pytest.mark.parametrize("exit_code", (1, 2))
def test_a_refused_or_unproven_write_stops_the_deploy(tmp_path: Path, exit_code: int) -> None:
    _stub_tool(tmp_path, exit_code=exit_code)

    result = _drive(tmp_path, _ensure_step(), dry_run=0, prelude=_helpers())

    assert result.returncode == exit_code
    assert "reached-the-next-step" not in result.stdout


@pytest.mark.parametrize("block", ("check", "ensure"))
def test_a_dry_run_makes_no_call(tmp_path: Path, block: str) -> None:
    calls = _stub_tool(tmp_path, exit_code=2)
    body = _ensure_step() if block == "ensure" else _between(CHECK_START, CHECK_END)
    if block == "check":
        # The real check sits in the non-dry-run branch; the dry-run branch
        # only names it. Drive the branch the dry run takes.
        automation = (ROOT / "scripts" / "lib" / "deploy_step_automation_identities.sh").read_text(
            encoding="utf-8"
        )
        dry_branch = automation[
            automation.index('export MIP_APP_DEPLOYMENT_LEASE_ID="dry-run-deployment-lease"') :
        ]
        assert "pii_salt_scope" not in dry_branch
        body = next(line for line in dry_branch.splitlines() if "pii-salt-v1" in line)

    result = _drive(tmp_path, body, dry_run=1, prelude=_helpers())

    assert result.returncode == 0, result.stderr
    assert not calls.exists()
    expected = {
        "check": "  would check read-only: scope mip and write-once pii-salt-v1",
        "ensure": "  would inspect/create: scope mip and write-once pii-salt-v1",
    }[block]
    assert expected in result.stdout.splitlines()


@pytest.mark.parametrize("dry_run", (0, 1))
@pytest.mark.parametrize("source", ("shell", ".env.local"))
def test_another_scope_is_refused_before_any_workspace_call(
    tmp_path: Path, dry_run: int, source: str
) -> None:
    calls = _stub_tool(tmp_path, exit_code=0)
    prelude = _shell_function("dotenv_value") + _shell_function("deployment_control_value")
    if source == "shell":
        prelude += "export MIP_PII_SALT_SCOPE=acme-mip\n"
    else:
        prelude += "unset MIP_PII_SALT_SCOPE\n"
        (tmp_path / ".env.local").write_text("MIP_PII_SALT_SCOPE=acme-mip\n", encoding="utf-8")

    result = _drive(
        tmp_path, _between(CONTROL_START, CONTROL_END), dry_run=dry_run, prelude=prelude
    )

    assert result.returncode == 2
    assert "MIP_PII_SALT_SCOPE must be mip" in result.stderr
    assert "reached-the-next-step" not in result.stdout
    assert not calls.exists()


def test_an_unset_scope_resolves_to_mip(tmp_path: Path) -> None:
    prelude = (
        _shell_function("dotenv_value")
        + _shell_function("deployment_control_value")
        + "unset MIP_PII_SALT_SCOPE PII_SALT_REQUIRE_EXISTING\n"
    )
    body = _between(CONTROL_START, CONTROL_END) + (
        '\necho "scope=$MIP_PII_SALT_SCOPE policy=$PII_SALT_REQUIRE_EXISTING"'
    )

    result = _drive(tmp_path, body, dry_run=0, prelude=prelude)

    assert result.returncode == 0, result.stderr
    assert "scope=mip policy=0" in result.stdout.splitlines()


# --- The runbook says what the tool prints ---------------------------------------


def test_the_runbook_names_the_one_line_that_prints_a_host() -> None:
    # The workspace-binding refusal names both hosts it compared; the runbook
    # once said neither mode ever prints a host. tests/unit/test_pii_salt_scope.py
    # pins the output this sentence describes.
    doc = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    start = doc.index("### PII salt (write-once)")
    section = " ".join(doc[start : doc.index("\n### ", start + 1)].split())

    assert "never the value or the host" not in section
    assert (
        "Neither mode prints the value. Both print scope and key names, and show an SDK "
        "error only by its class. The one exception is the workspace-binding refusal, which "
        "runs before any Secrets API call: it names the host the SDK resolved and the "
        "install's configured host"
    ) in section
