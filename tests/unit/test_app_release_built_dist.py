"""Stage the built frontend as deploy.sh step 1 does, so a dist-side refusal fails the PR.

deploy.sh step 1 stages the App runtime closure at the deploy commit plus the
dist it just built (``# >>> app release source: stage >>>``). A refusal there
stops the deploy after the lease, which stops and quiesces the App, so CI's
e2e-fixture-perf job runs this file right after its production build with
``MIP_REQUIRE_FRONTEND_DIST=1``: the stage, the release id read back from
the manifest and every local publish check (tools/databricks/
app_release_source.prepare_publish) must pass on that dist. The stage
arguments are read from the deploy block itself, so the two cannot drift.

Without a built ``frontend/dist`` (the backend job runs before any frontend
build) the test skips, and under ``MIP_REQUIRE_FRONTEND_DIST=1`` it fails
instead. It makes no Databricks call.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import block
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_source as release_source
from tools.databricks.app_runtime_closure import MAX_STAGED_FILES

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"
REQUIRE_DIST_FLAG = "MIP_REQUIRE_FRONTEND_DIST"
STAGE_START = "# >>> app release source: stage >>>"
STAGE_END = "# <<< app release source: stage <<<"
STAGE_CALL = 'run "$PYTHON" -m tools.databricks.app_runtime_closure stage'
# A service-principal deployer under a caller-supplied root leaf; the principal
# is a placeholder, never a live one.
PRINCIPAL, TARGET, LEAF = "ci-deployer@example.invalid", "dev", "client-a"


def _require_built_dist() -> None:
    if (DIST / "index.html").is_file():
        return
    reason = "frontend/dist is not built in this checkout"
    if os.environ.get(REQUIRE_DIST_FLAG) == "1":
        pytest.fail(f"{reason}, but {REQUIRE_DIST_FLAG}=1 requires the built frontend")
    pytest.skip(reason)


def _deploy_stage_argv(*, sha: str, out: Path, manifest: Path) -> list[str]:
    """The step-1 stage command of deploy.sh, with its three variables bound."""

    lines = block(STAGE_START, STAGE_END).splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(STAGE_CALL))
    command = []
    for line in lines[start:]:
        command.append(line.rstrip("\\").strip())
        if not line.endswith("\\"):
            break
    words = shlex.split(" ".join(command))
    assert words[:4] == ["run", "$PYTHON", "-m", "tools.databricks.app_runtime_closure"]
    bound = {"$SOURCE_GIT_SHA": sha, "$APP_RELEASE_STAGE_ROOT/source": str(out), "$APP_RELEASE_MANIFEST": str(manifest)}
    unbound = [word for word in words[4:] if "$" in word and word not in bound]
    assert not unbound, f"the stage block uses variables this test does not bind: {unbound}"
    return [sys.executable, "-m", "tools.databricks.app_runtime_closure", *(bound.get(word, word) for word in words[4:])]


def test_the_built_dist_stages_as_a_publishable_release(tmp_path: Path) -> None:
    _require_built_dist()
    sha = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    out, manifest = tmp_path / "app-release" / "source", tmp_path / "app-release" / "manifest.json"
    argv = _deploy_stage_argv(sha=sha, out=out, manifest=manifest)
    assert "--require-brotli-check" in argv, "deploy.sh refuses to ship gzip only"

    staged = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False, timeout=600)
    assert staged.returncode == 0, staged.stdout[-2000:] + staged.stderr[-2000:]
    assert "left out" not in staged.stdout, "every .br is checked and shipped"
    release_id = subprocess.run(
        [sys.executable, "-m", "tools.databricks.app_release_contract", "release-id",
         "--manifest", str(manifest), "--expected-commit", sha],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()

    read = contract.read_manifest(manifest, expected_commit=sha)
    assert re.fullmatch(rf"{sha[:12]}-[0-9a-f]{{12}}", release_id) and release_id == read.release_id
    assert len(read.entries) <= MAX_STAGED_FILES
    assert any(entry.path.startswith("frontend/dist/") and entry.path.endswith(".br") for entry in read.entries)
    plan = release_source.prepare_publish(
        release_id=release_id, stage_dir=out, manifest_path=manifest,
        files_root=contract.files_root(principal=PRINCIPAL, target=TARGET, root_leaf=LEAF),
        expected_commit=sha, principal=PRINCIPAL, target=TARGET, root_leaf=LEAF,
    )
    assert plan.release_path.endswith(f"/{LEAF}/app-src/{release_id}")


@pytest.mark.parametrize(("required", "outcome"), [(None, "skip"), ("1", "fail")])
def test_a_missing_dist_skips_unless_it_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required: str | None, outcome: str
) -> None:
    monkeypatch.setattr(sys.modules[__name__], "DIST", tmp_path / "dist")
    if required is None:
        monkeypatch.delenv(REQUIRE_DIST_FLAG, raising=False)
    else:
        monkeypatch.setenv(REQUIRE_DIST_FLAG, required)

    expected = pytest.skip.Exception if outcome == "skip" else pytest.fail.Exception
    with pytest.raises(expected):
        _require_built_dist()
