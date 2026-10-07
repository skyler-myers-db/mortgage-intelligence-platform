"""The deploy step-0 frontend dependency freshness preflight (W5a carryover).

tools/frontend_deps_check.py compares frontend/node_modules with
frontend/package-lock.json; scripts/deploy.sh reports the verdict before the
confirmation prompt and runs `npm ci` only after it, never on a shared
(symlinked) install and never in a dry run. The shell blocks are driven here
with a recording `npm` on PATH.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools import frontend_deps_check as deps

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "frontend_deps_check.py"
CHECK_START = "# >>> frontend dependency freshness: check >>>"
CHECK_END = "# <<< frontend dependency freshness: check <<<"
REPAIR_START = "# >>> frontend dependency freshness: repair >>>"
REPAIR_END = "# <<< frontend dependency freshness: repair <<<"
PROMPT = 'read -r -p "About to DEPLOY to the ${TARGET} target. Continue? [y/N] " ans'


def _lock(entries: dict[str, dict[str, object]]) -> dict[str, object]:
    return {"lockfileVersion": 3, "packages": {"": {"name": "mip-frontend"}, **entries}}


def _install(frontend: Path, path: str, version: str) -> None:
    target = frontend / path
    target.mkdir(parents=True, exist_ok=True)
    (target / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")


def _frontend(tmp_path: Path, lock_entries: dict[str, dict[str, object]], installed: dict[str, str]) -> Path:
    frontend = tmp_path / "frontend"
    (frontend / "node_modules").mkdir(parents=True)
    (frontend / "package-lock.json").write_text(json.dumps(_lock(lock_entries)), encoding="utf-8")
    for path, version in installed.items():
        _install(frontend, path, version)
    return frontend


LOCK = {
    "node_modules/vite": {"version": "8.3.2", "dev": True},
    "node_modules/a/node_modules/b": {"version": "1.0.0"},
    "node_modules/@oxlint/binding-linux-x64-gnu": {"version": "1.85.0", "optional": True, "dev": True},
}


# ------------------------------------------------------------------ the tool


def test_fresh_when_every_required_entry_matches(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, LOCK, {"node_modules/vite": "8.3.2", "node_modules/a/node_modules/b": "1.0.0"})

    assert deps.check(frontend) == (deps.EXIT_FRESH, ["frontend/node_modules matches frontend/package-lock.json"])


def test_stale_and_missing_entries_are_named_without_versions(tmp_path: Path) -> None:
    frontend = _frontend(tmp_path, LOCK, {"node_modules/vite": "8.3.0"})

    code, lines = deps.check(frontend)

    assert code == deps.EXIT_STALE
    assert lines == ["missing: b", "stale: vite"]
    assert not any("8.3" in line for line in lines)


def test_an_absent_optional_entry_counts_only_when_installed(tmp_path: Path) -> None:
    installed = {"node_modules/vite": "8.3.2", "node_modules/a/node_modules/b": "1.0.0"}
    assert deps.check(_frontend(tmp_path / "absent", LOCK, installed))[0] == deps.EXIT_FRESH

    stale_optional = {**installed, "node_modules/@oxlint/binding-linux-x64-gnu": "1.84.0"}
    code, lines = deps.check(_frontend(tmp_path / "stale", LOCK, stale_optional))
    assert (code, lines) == (deps.EXIT_STALE, ["stale: @oxlint/binding-linux-x64-gnu"])


def test_a_missing_node_modules_is_stale(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text(json.dumps(_lock(LOCK)), encoding="utf-8")

    assert deps.check(frontend) == (deps.EXIT_STALE, ["frontend/node_modules is missing"])


def test_a_symlinked_node_modules_is_shared_and_never_inspected(tmp_path: Path) -> None:
    real = _frontend(tmp_path / "real", LOCK, {})
    frontend = tmp_path / "linked" / "frontend"
    frontend.mkdir(parents=True)
    shutil.copy2(real / "package-lock.json", frontend / "package-lock.json")
    (frontend / "node_modules").symlink_to(real / "node_modules")

    assert deps.check(frontend) == (deps.EXIT_SHARED, ["frontend/node_modules is a shared/symlinked install"])


def test_only_the_first_twenty_mismatches_are_listed(tmp_path: Path) -> None:
    many = {f"node_modules/p{i:02d}": {"version": "1.0.0"} for i in range(25)}
    code, lines = deps.check(_frontend(tmp_path, many, {}))

    assert code == deps.EXIT_STALE
    assert len(lines) == 21 and lines[-1] == "... and 5 more"


def test_the_cli_exit_codes_are_distinct(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frontend = _frontend(tmp_path, LOCK, {"node_modules/vite": "8.3.0"})

    assert deps.main(["--frontend-dir", str(frontend)]) == 10
    assert "stale: vite" in capsys.readouterr().out
    assert len({deps.EXIT_FRESH, deps.EXIT_STALE, deps.EXIT_SHARED, 1, 2}) == 5


# ------------------------------------------------------------- the deploy wiring


def _block(start: str, end: str) -> str:
    text = deploy_entrypoint_text()
    return text[text.index(start) : text.index(end) + len(end)]


def test_the_blocks_sit_before_the_build_and_around_the_prompt() -> None:
    text = deploy_entrypoint_text()
    check, prompt, repair = text.index(CHECK_START), text.index(PROMPT), text.index(REPAIR_START)

    assert check < prompt < repair < text.index('step "build frontend')
    assert text.count(CHECK_START) == 1 and text.count(REPAIR_START) == 1


def test_the_check_block_never_installs_and_the_repair_block_is_dry_run_guarded() -> None:
    check = _block(CHECK_START, CHECK_END)
    repair = _block(REPAIR_START, REPAIR_END)

    assert not re.search(r"^\s*(?:run\s+)?npm\b", check, re.M), "the check block never installs"
    assert '[[ -f tools/frontend_deps_check.py ]]' in check
    assert 'if [[ "$DRY_RUN" -eq 0 && "$FRONTEND_DEPS_STATE" == "stale" ]]; then' in repair
    assert repair.count("npm --prefix frontend ci") == 2  # the run line and the fix hint
    assert "shared" not in repair


def _harness(repo: Path, *, dry_run: int, npm_fixes: bool) -> subprocess.CompletedProcess[str]:
    bin_dir = repo / "bin"
    bin_dir.mkdir(exist_ok=True)
    npm_log = repo / "npm.log"
    fix = (
        'python3 - <<PY\nimport json, pathlib\nfor key, meta in json.load(open("frontend/package-lock.json"))["packages"].items():\n'
        '    if key.startswith("node_modules/") and not meta.get("optional"):\n'
        '        p = pathlib.Path("frontend") / key\n        p.mkdir(parents=True, exist_ok=True)\n'
        '        (p / "package.json").write_text(json.dumps({"version": meta["version"]}))\nPY\n'
        if npm_fixes
        else ""
    )
    npm = bin_dir / "npm"
    npm.write_text(f'#!/usr/bin/env bash\necho "$*" >> {shlex.quote(str(npm_log))}\n{fix}', encoding="utf-8")
    npm.chmod(0o755)
    script = repo / "preflight.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'RED=""\nRST=""\nDIM=""\nDRY_RUN={dry_run}\nNO_CONFIRM=1\nTARGET=dev\n'
        f"PYTHON={shlex.quote(sys.executable)}\n"
        'run() {\n  echo "$ $*"\n  if [[ "$DRY_RUN" -eq 1 ]]; then\n    return 0\n  fi\n  "$@"\n}\n'
        f"{_block(CHECK_START, CHECK_END)}\n{_block(REPAIR_START, REPAIR_END)}\n",
        encoding="utf-8",
    )
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(repo)}
    return subprocess.run(["bash", str(script)], cwd=repo, env=env, text=True, capture_output=True, check=False)


def _npm_calls(repo: Path) -> list[str]:
    log = repo / "npm.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _repo(tmp_path: Path, *, tool: bool, state: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    if tool:
        shutil.copy2(TOOL, repo / "tools" / "frontend_deps_check.py")
    installed = {"node_modules/vite": "8.3.2", "node_modules/a/node_modules/b": "1.0.0"}
    if state == "stale":
        installed = {"node_modules/vite": "8.3.0"}
    frontend = _frontend(repo, LOCK, installed)
    if state == "shared":
        shutil.rmtree(frontend / "node_modules")
        (frontend / "node_modules").symlink_to(tmp_path)
    return repo


def test_isolated_copy_shape_without_the_tool_skips_and_never_installs(tmp_path: Path) -> None:
    repo = tmp_path / "isolated"
    repo.mkdir()
    shutil.copy2(ROOT / "scripts" / "deploy.sh", repo / "deploy.sh")

    result = _harness(repo, dry_run=0, npm_fixes=True)

    assert result.returncode == 0, result.stderr
    assert "frontend deps: skipped" in result.stdout
    assert _npm_calls(repo) == []


def test_auth_harness_shape_without_the_tool_skips_and_never_installs(tmp_path: Path) -> None:
    repo = tmp_path / "auth"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "oauth_m2m_mint.py").write_text("print('mint')\n", encoding="utf-8")
    (repo / ".env.local").write_text("DATABRICKS_HOST=https://reviewed-workspace.example\n", encoding="utf-8")
    (repo / "databricks.yml").write_text("workspace:\n  host: https://reviewed-workspace.example\n", encoding="utf-8")

    result = _harness(repo, dry_run=0, npm_fixes=True)

    assert result.returncode == 0, result.stderr
    assert "frontend deps: skipped" in result.stdout
    assert _npm_calls(repo) == []


def test_a_fresh_install_is_left_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path, tool=True, state="fresh")
    result = _harness(repo, dry_run=0, npm_fixes=True)

    assert result.returncode == 0, result.stderr
    assert "frontend deps: fresh" in result.stdout and _npm_calls(repo) == []


def test_a_stale_install_is_repaired_with_npm_ci_after_the_prompt(tmp_path: Path) -> None:
    repo = _repo(tmp_path, tool=True, state="stale")
    result = _harness(repo, dry_run=0, npm_fixes=True)

    assert result.returncode == 0, result.stderr
    assert "frontend deps: stale" in result.stdout
    assert _npm_calls(repo) == ["--prefix frontend ci --no-audit --no-fund"]


def test_a_repair_that_does_not_take_fails_with_the_fix_line(tmp_path: Path) -> None:
    repo = _repo(tmp_path, tool=True, state="stale")
    result = _harness(repo, dry_run=0, npm_fixes=False)

    assert result.returncode == 2
    assert "still does not match" in result.stderr and "fix: npm --prefix frontend ci" in result.stderr


def test_a_shared_install_fails_closed_and_is_never_mutated(tmp_path: Path) -> None:
    repo = _repo(tmp_path, tool=True, state="shared")
    result = _harness(repo, dry_run=0, npm_fixes=True)

    assert result.returncode == 2
    assert "shared/symlinked install; run npm --prefix frontend ci in a private checkout" in result.stderr
    assert _npm_calls(repo) == []


def test_a_dry_run_reports_and_never_installs_or_exits(tmp_path: Path) -> None:
    for state in ("stale", "shared"):
        repo = _repo(tmp_path / state, tool=True, state=state)
        result = _harness(repo, dry_run=1, npm_fixes=False)

        assert result.returncode == 0, (state, result.stderr)
        assert f"frontend deps: {state}" in result.stdout
        assert _npm_calls(repo) == []


def test_an_unexpected_check_failure_is_a_hard_error_never_stale(tmp_path: Path) -> None:
    repo = _repo(tmp_path, tool=True, state="stale")
    (repo / "frontend" / "package-lock.json").write_text("{not json", encoding="utf-8")

    result = _harness(repo, dry_run=0, npm_fixes=True)

    assert result.returncode == 2
    assert "frontend deps: error" in result.stdout
    assert _npm_calls(repo) == []
