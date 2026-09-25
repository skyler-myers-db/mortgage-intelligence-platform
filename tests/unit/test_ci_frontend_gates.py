"""The wave-2 CI integration duties stay wired (2026-09-21 UI/UX audit).

Three frontend gates in ``.github/workflows/ci.yml`` used to be able to pass
vacuously while their subject was not yet on the tree; it is now, so each is
pinned fail-closed:

- the perf-budget step runs unconditionally (its ``hashFiles`` guard is gone:
  perf-budget.fixture.spec.ts is committed);
- the source-map upload errors when the build emitted no maps
  (tools/postbuild_artifacts.mjs moves them to frontend/sourcemaps/);
- the e2e-fixture job re-runs tests/unit/test_frontend_build_artifacts.py
  right after its build with ``MIP_REQUIRE_FRONTEND_DIST=1``, so the
  served-layer and build-meta checks fail rather than skip.

The backend job's Node + ``MIP_REQUIRE_FIXTURE_CONTRACT`` wiring is pinned by
tests/unit/test_e2e_fixture_contract.py beside the test it serves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PERF_SPEC = ROOT / "frontend" / "tests" / "e2e" / "fixture" / "perf-budget.fixture.spec.ts"
BUILD_ARTIFACTS_TEST = "tests/unit/test_frontend_build_artifacts.py"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _step_index(steps: list[dict[str, Any]], needle: str) -> int:
    for index, step in enumerate(steps):
        if needle in str(step.get("run", "")) or needle in str(step.get("uses", "")):
            return index
    raise AssertionError(f"no step runs or uses {needle!r}")


def test_the_perf_budget_step_is_unconditional() -> None:
    assert PERF_SPEC.is_file(), "the perf budget spec the step runs is committed"
    steps = _jobs()["e2e-fixture"]["steps"]
    perf = steps[_step_index(steps, "perf-budget --workers=1")]

    assert "if" not in perf, "the perf-budget step must not be guarded now that its spec exists"
    assert perf["env"]["MIP_PERF"] == "1"


def test_the_source_map_upload_fails_when_the_build_emitted_none() -> None:
    steps = _jobs()["frontend-tests"]["steps"]
    upload = next(step for step in steps if step.get("name", "").startswith("Upload source maps"))

    assert upload["with"]["path"] == "frontend/sourcemaps/**"
    assert upload["with"]["if-no-files-found"] == "error"


def test_the_fixture_job_checks_the_built_dist_with_the_require_flag() -> None:
    steps = _jobs()["e2e-fixture"]["steps"]
    build = _step_index(steps, "npm --prefix frontend run build")
    python = _step_index(steps, "actions/setup-python@")
    install = _step_index(steps, "pip install -r requirements.txt")
    check = _step_index(steps, BUILD_ARTIFACTS_TEST)

    assert build < python < install < check
    assert steps[check]["env"]["MIP_REQUIRE_FRONTEND_DIST"] == "1"
