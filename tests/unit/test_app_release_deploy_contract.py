"""deploy.sh wiring of the governed per-release App source.

Step 0 runs ``app_runtime_closure check --ref "$SOURCE_GIT_SHA"`` (git only,
no dist, no workspace call), so a commit step 1 could not stage, which would
stop and quiesce the App there, is refused before the lease. The block
mirrors the requirements-digest check beside it: a dry run reports and
continues, a real run refuses anything but ``current`` with exit 2. Blocks
are read from ``deploy_entrypoint_text()`` (the sliced deploy.sh layout)
and driven in a small harness.
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
CHECK_START = "# >>> app runtime closure: check >>>"
CHECK_END = "# <<< app runtime closure: check <<<"
DIGEST_END = "# <<< requirements digest: check <<<"
PROMPT = 'read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans'


def _block(start: str = CHECK_START, end: str = CHECK_END) -> str:
    text = deploy_entrypoint_text()
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) : text.index(end) + len(end)]


def _code(block: str) -> str:
    return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))


def test_the_closure_check_runs_after_the_digest_check_and_before_any_workspace_call() -> None:
    text = deploy_entrypoint_text()
    check = text.index(CHECK_START)

    assert text.index(DIGEST_END) < check
    assert check < text.index("workspace_admin_inventory_principal(WorkspaceClient())")
    assert check < text.index(PROMPT) < text.index("\nrun ")
    assert check < text.index('step "acquire signed workspace lease for the exact App deployment"')
    assert text.index("verify_exact_deploy_source\n") < check  # SOURCE_GIT_SHA is bound


def test_the_block_checks_the_deploy_commit_and_refuses_only_real_runs() -> None:
    code = _code(_block())

    assert '-m tools.databricks.app_runtime_closure check --ref "$SOURCE_GIT_SHA"' in code
    assert "app_runtime_closure stage" not in code and "databricks " not in code
    assert code.count("-m tools.") == 1
    assert 'if [[ "$DRY_RUN" -eq 0 && "$APP_RUNTIME_CLOSURE_STATE" != "current" ]]; then' in code
    assert code.count("exit 2") == 1


def _harness(repo: Path, *, dry_run: int) -> subprocess.CompletedProcess[str]:
    script = repo / "preflight.sh"
    script.write_text(
        "set -Eeuo pipefail\ntrap 'exit 99' ERR\n"
        f'RED=""\nRST=""\nDRY_RUN={dry_run}\nTARGET=dev\nPYTHON={shlex.quote(sys.executable)}\n'
        f'SOURCE_GIT_SHA={"0" * 40}\n{_block()}\necho reached-after-block\n',
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    return subprocess.run(
        ["bash", str(script)], cwd=repo, env=env, text=True, capture_output=True, check=False
    )


def _stub(repo: Path, exit_code: int) -> None:
    package = repo / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (repo / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    (package / "app_runtime_closure.py").write_text(
        "import sys\n"
        "assert sys.argv[1:] == ['check', '--ref', '0' * 40], sys.argv\n"
        f"print('  stub verdict')\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )


def test_a_stageable_head_passes_a_real_run_in_the_real_tree(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    script = tmp_path / "preflight.sh"
    script.write_text(
        "set -Eeuo pipefail\ntrap 'exit 99' ERR\n"
        f'RED=""\nRST=""\nDRY_RUN=0\nTARGET=dev\nPYTHON={shlex.quote(sys.executable)}\n'
        f"SOURCE_GIT_SHA={head}\n{_block()}\necho reached-after-block\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script)], cwd=ROOT, text=True, capture_output=True, check=False,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "app runtime closure: current" in result.stdout
    assert "reached-after-block" in result.stdout


def test_a_current_stub_passes_a_real_run(tmp_path: Path) -> None:
    _stub(tmp_path, 0)

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 0, result.stderr
    assert "app runtime closure: current" in result.stdout
    assert "reached-after-block" in result.stdout


@pytest.mark.parametrize(
    ("tool_exit", "state", "message"),
    [
        (2, "refused", "cannot be staged"),
        (1, "error", "the App runtime closure check failed"),
        (None, "skipped", "app_runtime_closure.py is not in this tree"),
    ],
)
def test_a_real_run_refuses_anything_but_current_with_exit_2(
    tmp_path: Path, tool_exit: int | None, state: str, message: str
) -> None:
    if tool_exit is not None:
        _stub(tmp_path, tool_exit)

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 2
    assert f"app runtime closure: {state}" in result.stdout
    assert message in result.stderr
    assert "reached-after-block" not in result.stdout


@pytest.mark.parametrize(("tool_exit", "state"), [(2, "refused"), (1, "error"), (None, "skipped")])
def test_a_dry_run_reports_and_continues(tmp_path: Path, tool_exit: int | None, state: str) -> None:
    if tool_exit is not None:
        _stub(tmp_path, tool_exit)

    result = _harness(tmp_path, dry_run=1)

    assert result.returncode == 0, result.stderr
    assert f"app runtime closure: {state}" in result.stdout
    assert "reached-after-block" in result.stdout
