"""deploy.sh plans the mip-runtime provisioning read-only before step 0a stops the App.

provision_runtime_secrets refuses a changed (or unreadable) Cotality ID-mask
value, but it runs in the bundle-apply step, after signed_blue_proof has
stopped and quarantined the App; with no signed blue the App would be left
stopped. The preflight runs the same plan with ``--check`` while the App still
serves. The block is read from ``deploy_entrypoint_text()`` (the sliced
deploy.sh layout) and driven with a stub module.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text

ROOT = Path(__file__).resolve().parents[2]
START = "# >>> runtime secret pre-stop check >>>"
END = "# <<< runtime secret pre-stop check <<<"
BUNDLE_APPLY_PROVISION = 'run "$PYTHON" -m tools.databricks.provision_runtime_secrets \\\n  --scope "$RUNTIME_SECRET_SCOPE"'


def _block() -> str:
    text = deploy_entrypoint_text()
    assert text.count(START) == 1 and text.count(END) == 1
    return text[text.index(START) : text.index(END) + len(END)]


def _step_body_start(step_file: str) -> str:
    text = (ROOT / "scripts" / "lib" / step_file).read_text(encoding="utf-8")
    return text[text.index("\n\n") + 2 :][:200]


def test_the_check_runs_after_the_secrets_resolve_and_before_the_app_stop() -> None:
    text = deploy_entrypoint_text()
    check = text.index(START)

    for resolved in (
        'MIP_RUNTIME_SECRET_SCOPE="$(deployment_control_value MIP_RUNTIME_SECRET_SCOPE mip-runtime)"',
        'export MIP_GENIE_ACTION_SECRET_CURRENT="$_GENIE_ACTION_SECRET_RESOLVED"',
        'export MIP_COTALITY_ID_MASK_SECRET="$_ID_MASK_RESOLVED"',
    ):
        assert text.index(resolved) < check, resolved
    assert check < text.index('step "acquire signed workspace lease for the exact App deployment"')
    assert check < text.index(_step_body_start("deploy_step_signed_blue_proof.sh")), "before the App stop"
    assert check < text.index(BUNDLE_APPLY_PROVISION), "before the write it previews"


def test_the_check_previews_the_same_scope_the_write_uses() -> None:
    text = deploy_entrypoint_text()

    assert 'RUNTIME_SECRET_SCOPE="${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"' in text
    assert '--scope "${MIP_RUNTIME_SECRET_SCOPE:-mip-runtime}"' in _block()


def test_the_check_is_read_only_and_never_run_wrapped() -> None:
    code = [line for line in _block().splitlines() if not line.lstrip().startswith("#")]

    assert any("tools.databricks.provision_runtime_secrets --check" in line for line in code)
    assert not any(line.lstrip().startswith("run ") for line in code)
    assert sum("exit 2" in line for line in code) == 1


def _harness(
    repo: Path, *, dry_run: int, stub_exit: int, scope: str | None = None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    calls = repo / "calls.log"
    module = repo / "tools" / "databricks" / "provision_runtime_secrets.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "print('runtime secret plan for stub')\n"
        f"sys.exit({stub_exit})\n",
        encoding="utf-8",
    )
    scope_line = f"MIP_RUNTIME_SECRET_SCOPE={shlex.quote(scope)}\n" if scope else ""
    script = repo / "prestop.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'RED=""\nRST=""\nDRY_RUN={dry_run}\nPYTHON={shlex.quote(sys.executable)}\n{scope_line}'
        f"{_block()}\necho reached-the-app-stop\n",
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    result = subprocess.run(["bash", str(script)], cwd=repo, env=env, text=True, capture_output=True, check=False)
    return result, calls


def test_a_clean_plan_lets_the_deploy_continue(tmp_path: Path) -> None:
    result, calls = _harness(tmp_path, dry_run=0, stub_exit=0)

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8") == "--check --scope mip-runtime\n"
    assert "reached-the-app-stop" in result.stdout


@pytest.mark.parametrize("stub_exit", (1, 2))
def test_a_refused_plan_stops_before_the_app_stop_with_exit_2(tmp_path: Path, stub_exit: int) -> None:
    result, _calls = _harness(tmp_path, dry_run=0, stub_exit=stub_exit, scope="mip-runtime-staging")

    assert result.returncode == 2
    assert "runtime secret plan was refused before the App stop" in result.stderr
    assert "reached-the-app-stop" not in result.stdout


def test_an_install_scoped_runtime_scope_is_the_one_checked(tmp_path: Path) -> None:
    _result, calls = _harness(tmp_path, dry_run=0, stub_exit=0, scope="mip-runtime-staging")

    assert calls.read_text(encoding="utf-8") == "--check --scope mip-runtime-staging\n"


def test_a_dry_run_names_the_check_and_calls_no_workspace(tmp_path: Path) -> None:
    result, calls = _harness(tmp_path, dry_run=1, stub_exit=2)

    assert result.returncode == 0, result.stderr
    assert not calls.exists()
    assert (
        "would check (read-only): tools.databricks.provision_runtime_secrets --check --scope mip-runtime"
        in result.stdout
    )
