"""deploy.sh step 0 refuses a real deploy whose requirements.txt digest is stale.

The only other enforcement of ``tools/requirements_digest.py --check`` is a
pytest test, and the repo has no required checks, so a lock bump merged
without a restamp would reach a governed deploy with an unchanged
requirements.txt, and with one the Apps build skipped the reinstall and kept
the old packages on 2026-10-28 (it compares only requirements.txt;
docs/deployment.md). The block mirrors the
interpreter pin check beside it: a dry run reports and continues, a real run
refuses with exit 2 before any workspace call. The block is read from ``deploy_entrypoint_text()`` (the sliced deploy.sh
layout) and driven in a small harness.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text

ROOT = Path(__file__).resolve().parents[2]
START = "# >>> requirements digest: check >>>"
END = "# <<< requirements digest: check <<<"
PINS_END = "# <<< deploy interpreter pins: check <<<"
PROMPT = 'read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans'


def _block() -> str:
    text = deploy_entrypoint_text()
    assert text.count(START) == 1 and text.count(END) == 1
    return text[text.index(START) : text.index(END) + len(END)]


def test_the_digest_check_runs_after_the_pin_check_and_before_any_workspace_call() -> None:
    text = deploy_entrypoint_text()
    check = text.index(START)

    assert text.index(PINS_END) < check
    assert check < text.index("workspace_admin_inventory_principal(WorkspaceClient())")
    assert check < text.index(PROMPT) < text.index("\nrun ")
    assert check < text.index('step "acquire signed workspace lease for the exact App deployment"')


def test_the_block_never_writes_the_digest_and_refuses_only_real_runs() -> None:
    code = "\n".join(line for line in _block().splitlines() if not line.lstrip().startswith("#"))

    assert "--check" in code and "--write" not in code
    assert 'if [[ "$DRY_RUN" -eq 0 && "$REQUIREMENTS_DIGEST_STATE" != "current" ]]; then' in code
    assert code.count("exit 2") == 1


def _harness(repo: Path, *, dry_run: int) -> subprocess.CompletedProcess[str]:
    script = repo / "preflight.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'RED=""\nRST=""\nDRY_RUN={dry_run}\nTARGET=dev\nPYTHON={shlex.quote(sys.executable)}\n'
        f"{_block()}\necho reached-after-block\n",
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(repo)}
    return subprocess.run(
        ["bash", str(script)], cwd=repo, env=env, text=True, capture_output=True, check=False
    )


def _stub(repo: Path, exit_code: int) -> None:
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    (repo / "tools" / "requirements_digest.py").write_text(
        f"import sys\nprint('  stub verdict')\nsys.exit({exit_code})\n", encoding="utf-8"
    )


def _real_tree(repo: Path) -> None:
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "tools" / "requirements_digest.py", repo / "tools")
    for name in ("requirements.txt", "uv.lock", "requirements.in"):
        shutil.copy2(ROOT / name, repo / name)


def test_a_current_digest_passes_a_real_run(tmp_path: Path) -> None:
    _real_tree(tmp_path)

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 0, result.stderr
    assert "requirements digest: current" in result.stdout
    assert "reached-after-block" in result.stdout


def test_a_lock_bumped_without_a_restamp_refuses_a_real_run_with_the_remedy(
    tmp_path: Path,
) -> None:
    _real_tree(tmp_path)
    lock = tmp_path / "uv.lock"
    lock.write_text(lock.read_text(encoding="utf-8") + "# bumped\n", encoding="utf-8")

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 2
    assert "requirements digest: stale" in result.stdout
    assert "fix: python tools/requirements_digest.py --write" in result.stderr
    assert "can skip the dependency reinstall" in result.stderr
    assert "reached-after-block" not in result.stdout


@pytest.mark.parametrize(
    ("tool_exit", "state", "message"),
    [
        (1, "stale", "deps-digest line is stale"),
        (2, "error", "the requirements digest check failed"),
        (None, "skipped", "tools/requirements_digest.py is not in this tree"),
    ],
)
def test_a_real_run_refuses_anything_but_current_with_exit_2(
    tmp_path: Path, tool_exit: int | None, state: str, message: str
) -> None:
    if tool_exit is not None:
        _stub(tmp_path, tool_exit)

    result = _harness(tmp_path, dry_run=0)

    assert result.returncode == 2
    assert f"requirements digest: {state}" in result.stdout
    assert message in result.stderr
    assert "reached-after-block" not in result.stdout


@pytest.mark.parametrize(("tool_exit", "state"), [(1, "stale"), (2, "error"), (None, "skipped")])
def test_a_dry_run_reports_and_continues(
    tmp_path: Path, tool_exit: int | None, state: str
) -> None:
    if tool_exit is not None:
        _stub(tmp_path, tool_exit)

    result = _harness(tmp_path, dry_run=1)

    assert result.returncode == 0, result.stderr
    assert f"requirements digest: {state}" in result.stdout
    assert "reached-after-block" in result.stdout
