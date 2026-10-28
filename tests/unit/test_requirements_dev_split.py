"""The App installs runtime packages only; the dev tools have their own lock.

requirements.txt, what the Databricks Apps build installs, is uv.lock plus
requirements.in. pytest, its plugins, ruff, mypy and hypothesis live in
requirements-dev.in, pinned by requirements-dev.lock, which is compiled with
uv.lock as a constraint. requirements-dev.txt installs both sets, for CI and
local development (docs/deployment.md, "App Python dependencies").
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from tools.databricks import app_runtime_closure as closure
from tools.deps_report.python import pins

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
REQUIREMENTS_IN = ROOT / "requirements.in"
UV_LOCK = ROOT / "uv.lock"
DEV_IN = ROOT / "requirements-dev.in"
DEV_LOCK = ROOT / "requirements-dev.lock"
DEV_TXT = ROOT / "requirements-dev.txt"
DEV_TOOLS = ("pytest", "pytest-cov", "pytest-xdist", "ruff", "mypy", "hypothesis")
DEV_COMPILE = (
    "uv pip compile requirements-dev.in -c uv.lock --python-version 3.11 "
    "--format requirements.txt --output-file requirements-dev.lock"
)
DEV_INSTALL = "pip install -r requirements-dev.txt"
_DEV_TOOL_RUN = re.compile(r"(?:^|[\s;&|(])(?:python -m )?(?:pytest|ruff|mypy)\s")


def _content(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_requirements_dev_txt_installs_the_app_set_plus_the_dev_set() -> None:
    assert _content(DEV_TXT) == ["-r requirements.txt", "-c requirements-dev.lock", "-r requirements-dev.in"]


def test_the_dev_tools_are_pinned_in_the_dev_files_and_nowhere_in_the_app_set() -> None:
    runtime_in, runtime_lock, dev_in, dev_lock = pins(REQUIREMENTS_IN), pins(UV_LOCK), pins(DEV_IN), pins(DEV_LOCK)

    for tool in DEV_TOOLS:
        assert dev_lock[tool] == dev_in[tool], tool
        assert tool not in runtime_in and tool not in runtime_lock, tool
    # Options belong on the compile command, so readers of requirements.in-style
    # files (tools/deps_report) read this one the same way.
    assert not [line for line in _content(DEV_IN) if line.startswith("-")]


def test_the_dev_lock_is_compiled_against_uv_lock_and_agrees_on_every_shared_pin() -> None:
    header = DEV_LOCK.read_text(encoding="utf-8").splitlines()[1]
    runtime_lock, dev_lock = pins(UV_LOCK), pins(DEV_LOCK)
    shared = runtime_lock.keys() & dev_lock.keys()

    assert header == f"#    {DEV_COMPILE}"
    assert "--python-version 3.11" in UV_LOCK.read_text(encoding="utf-8").splitlines()[1]
    # pip refuses two different constraints for one package, so a narrow
    # uv.lock bump of a shared package must recompile the dev lock too.
    assert {"packaging", "typing-extensions"} <= shared
    assert {name: (runtime_lock[name], dev_lock[name]) for name in shared if runtime_lock[name] != dev_lock[name]} == {}


def test_the_app_source_never_carries_the_dev_files() -> None:
    staged = [entry.path for entry in (*closure.UPLOAD, *closure.READS)]

    assert {"requirements.txt", "requirements.in", "uv.lock"} <= set(staged)
    assert not [path for path in staged if path.startswith("requirements-dev")]


def _jobs_running_dev_tools() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        jobs: dict[str, Any] = yaml.safe_load(workflow.read_text(encoding="utf-8")).get("jobs", {})
        for name, job in jobs.items():
            runs = [str(step.get("run", "")) for step in job.get("steps", [])]
            code = "\n".join(line.split("#", 1)[0] for run in runs for line in run.splitlines())
            if _DEV_TOOL_RUN.search(code):
                found[f"{workflow.name}:{name}"] = runs
    return found


def test_every_workflow_job_that_runs_pytest_ruff_or_mypy_installs_the_dev_set() -> None:
    jobs = _jobs_running_dev_tools()

    assert {"ci.yml:backend-tests", "nightly.yml:parity-live", "nightly.yml:genie-fuzz-deep"} <= jobs.keys()
    missing = [job for job, runs in jobs.items() if not any(DEV_INSTALL in run for run in runs)]
    assert missing == [], f"these jobs run dev tools without {DEV_INSTALL}: {missing}"


def test_ci_pip_audit_covers_the_app_set_and_the_dev_set() -> None:
    steps = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))["jobs"]["security-scan"]["steps"]
    audits = [str(step["run"]) for step in steps if str(step.get("run", "")).startswith("pip-audit")]

    # requirements-dev.txt includes requirements.txt, so one audit covers both.
    assert audits == ["pip-audit -r requirements-dev.txt --strict"]
