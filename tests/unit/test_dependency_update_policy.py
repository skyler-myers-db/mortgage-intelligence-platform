"""Dependency updates are a weekly SIGNAL, never a bot (D-platform-process-a, W5e).

Ruled 2026-09-30 (docs/modernization-todo.md 'Dependency update automation'):
no Renovate or Dependabot (bot branches break the main-only contract and
join every all-refs gitleaks scan; a bot PR still needs budget, VRT and
checklist re-baselines). .github/workflows/deps-report.yml reports instead:
a read-only ``report`` job and an issues-only ``publish`` job that runs no
repository or package code and edits one issue in place. Its markdown
validator is executed here with node against the same cases as the Python
renderer's, so the two checks cannot drift.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.deps_report import render

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deps-report.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
EXPECTED_REPORT = ROOT / "tests" / "fixtures" / "deps_report" / "expected_report.md"
RUN = "https://github.com/o/r/actions/runs/42"


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: str) -> list[dict[str, Any]]:
    return _workflow()["jobs"][job]["steps"]


def _uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", ""))


def test_no_update_bot_is_configured() -> None:
    for name in (".github/dependabot.yml", ".github/dependabot.yaml", "renovate.json", ".github/renovate.json"):
        assert not (ROOT / name).exists(), name
    assert not list(ROOT.glob(".renovaterc*")) and not list((ROOT / ".github").glob(".renovaterc*"))


def test_the_workflow_triggers_weekly_and_on_dispatch_with_no_default_permissions() -> None:
    workflow = _workflow()
    triggers = workflow.get("on", workflow.get(True))  # PyYAML reads a bare `on:` key as True

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "17 13 * * 1"}]
    assert workflow["permissions"] == {}
    assert workflow["concurrency"] == {"group": "deps-report", "cancel-in-progress": False}


def test_the_report_job_is_read_only_and_runs_the_tool_with_the_ephemeral_token() -> None:
    job = _workflow()["jobs"]["report"]
    steps = job["steps"]

    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 15
    assert _uses(steps[0]) == "actions/checkout@v6" and steps[0]["with"]["fetch-depth"] == 0
    assert any(_uses(s) == "actions/setup-node@v6" and s["with"]["node-version"] == "22" for s in steps)
    assert any(_uses(s) == "actions/setup-python@v6" and s["with"]["python-version"] == "3.11" for s in steps)
    pip = next(s for s in steps if "pip install" in str(s.get("run", "")))
    assert "'pip-audit==2.9.0'" in pip["run"] and "'pip-audit==2.9.0'" in CI.read_text(encoding="utf-8")
    build = next(s for s in steps if "python -m tools.deps_report" in str(s.get("run", "")))
    assert build["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert "--json dist/deps-report.json --markdown dist/deps-report.md" in build["run"]
    assert "set +e" in build["run"] and 'echo "exit_code=${status}" >> "$GITHUB_OUTPUT"' in build["run"]
    upload = next(s for s in steps if _uses(s).startswith("actions/upload-artifact@"))
    assert _uses(upload) == "actions/upload-artifact@v7" and upload["if"] == "always()"
    assert upload["with"]["name"] == "deps-report" and upload["with"]["retention-days"] == 30
    assert steps[-1]["run"] == 'exit "${REPORT_EXIT:-2}"'
    assert steps[-1]["env"] == {"REPORT_EXIT": "${{ steps.build.outputs.exit_code }}"}


def test_the_publish_job_holds_only_issues_write_and_runs_no_repository_code() -> None:
    job = _workflow()["jobs"]["publish"]
    steps = job["steps"]

    assert job["needs"] == "report" and job["if"] == "always()"
    assert job["permissions"] == {"issues": "write"}
    assert [_uses(s) for s in steps] == ["actions/download-artifact@v8", "actions/github-script@v9"]
    assert all("run" not in s for s in steps), "no shell step: no checkout, setup, pip or npm"
    script = steps[1]["with"]["script"]
    assert "Report withheld: output validation failed; see ${runUrl}" in script
    assert "issues.update" in script and "issues.create" in script
    assert "createComment" not in script, "never comments"
    assert "'Weekly dependency and release-signal report'" in script and "'dependency-report'" in script


def test_the_workflow_text_rules() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "github.token" in text
    for banned in ("secrets.", "npm ci", "npm install", "git push", "gh pr create", "create-pull-request"):
        assert banned not in text, banned


def _js_validator() -> str:
    script = _steps("publish")[1]["with"]["script"]
    start, end = script.index("// >>> validate"), script.index("// <<< validate")
    return script[start:end]


CASES = [
    "hi @someone",
    "[x](http://evil)",
    "see www.evil.example",
    "<b>x</b>",
    "x" * (render.MAX_CHARS + 1),
    f"[this run]({RUN}) and `@tanstack/react-query`",
    "[`GHSA-2234-5678-9cfg`](https://github.com/advisories/GHSA-2234-5678-9cfg)",
    "[`GHSA-2234-5678-9cfg`](https://github.com/advisories/GHSA-2234-5678-9cfg/x)",
    "`[x](http://evil)` stays inside a code span",
]


def test_the_publish_validator_agrees_with_the_renderer_on_every_case() -> None:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("MIP_REQUIRE_FIXTURE_CONTRACT") == "1":
            pytest.fail("node is required to run the publish validator (MIP_REQUIRE_FIXTURE_CONTRACT=1)")
        pytest.skip("node is not installed")
    cases = [*CASES, EXPECTED_REPORT.read_text(encoding="utf-8")]
    program = _js_validator() + (
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(input.cases.map((md) => validate(md, input.run))));"
    )
    result = subprocess.run(
        [node, "-e", program], input=json.dumps({"cases": cases, "run": RUN}), capture_output=True, text=True, check=True, timeout=60
    )
    js = json.loads(result.stdout)
    python = [render.validate_markdown(case, RUN) for case in cases]

    assert js == python
    assert python[-1] == [], "the pinned dry-run report is publishable"
    assert [bool(problems) for problems in python[:-1]] == [True, True, True, True, True, False, False, True, False]
