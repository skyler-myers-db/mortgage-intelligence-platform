"""Build metadata never reaches the served or deployed dist.

2026-09-21 UI/UX audit, findings ``bundle-08`` and ``stack-01`` (build slice).

``vite build`` emits a chunk-graph manifest (for the budget gate) and hidden
source maps (for decoding production stack traces). Neither may be served:
``backend/main.py``'s SPA fallback serves any real file under
``frontend/dist`` and the ``/assets`` route serves any file under
``dist/assets``, and ``databricks.yml`` uploads ``frontend/dist/**``.
``tools/postbuild_artifacts.mjs`` moves them to ``frontend/build-meta/`` and
``frontend/sourcemaps/`` and fails the build if anything is left behind.

The static checks pin that wiring. The served-layer checks go through the
real ``backend.main.app`` and skip when ``frontend/dist`` is not built, which
is the case in the backend CI job (it runs before any frontend build); there
the postbuild step's own dist scan, which runs inside every
``npm run build``, is the fail-closed guard. CI's e2e-fixture job runs this
file again right after its build with ``MIP_REQUIRE_FRONTEND_DIST=1``, where a
missing dist or build-meta FAILS instead of skipping, so the served-layer
checks can never go silently vacuous.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
RELOCATED_DIRS = ("frontend/build-meta/", "frontend/sourcemaps/")
REQUIRE_DIST_FLAG = "MIP_REQUIRE_FRONTEND_DIST"
RELOCATED_SAMPLES = (
    "frontend/build-meta/build-manifest.json",
    "frontend/sourcemaps/assets/index-AbC123.js.map",
)


def test_relocated_build_metadata_dirs_are_git_ignored() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    for directory in RELOCATED_DIRS:
        assert directory in ignored, f"{directory} must be git-ignored"


def test_no_bundle_sync_include_pattern_uploads_relocated_build_metadata() -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    includes = bundle["sync"]["include"]

    assert "frontend/dist/**" in includes
    for sample in RELOCATED_SAMPLES:
        matching = [pattern for pattern in includes if fnmatch.fnmatch(sample, pattern)]
        assert matching == [], f"{sample} would be uploaded by sync.include {matching}"


def test_build_script_relocates_metadata_before_precompressing() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    steps = [step.strip() for step in package["scripts"]["build"].split("&&")]

    assert steps == [
        "tsc -b",
        "vite build",
        "node ../tools/postbuild_artifacts.mjs",
        "node ../tools/precompress_assets.mjs",
    ]


def test_vite_emits_hidden_source_maps_and_the_named_manifest() -> None:
    config = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")

    assert re.search(r"""^\s*sourcemap:\s*(["'])hidden\1,""", config, re.MULTILINE)
    assert re.search(r"""^\s*manifest:\s*(["'])build-manifest\.json\1,""", config, re.MULTILINE)


def _skip_unless_required(reason: str) -> None:
    """Skip where no build ran; FAIL where the caller promised one."""
    if os.environ.get(REQUIRE_DIST_FLAG) == "1":
        pytest.fail(f"{reason}, but {REQUIRE_DIST_FLAG}=1 requires the built frontend")
    pytest.skip(reason)


def _built_entry_chunk() -> Path:
    entries = sorted((DIST / "assets").glob("index-*.js")) if (DIST / "assets").is_dir() else []
    if not (DIST / "index.html").is_file() or not entries:
        _skip_unless_required("frontend/dist is not built in this checkout")
    return entries[0]


def test_served_build_manifest_path_is_the_spa_shell_not_json() -> None:
    _built_entry_chunk()
    from backend.main import app

    response = TestClient(app).get("/build-manifest.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.lstrip().lower().startswith("<!doctype html")
    assert '"isEntry"' not in response.text


def test_entry_chunk_source_map_is_not_served() -> None:
    entry = _built_entry_chunk()
    from backend.main import app

    client = TestClient(app)
    assert client.get(f"/assets/{entry.name}").status_code == 200
    assert client.get(f"/assets/{entry.name}.map").status_code == 404


def test_chunk_modules_key_node_modules_ids_by_package_path() -> None:
    """``build-modules.json`` reads the same whatever ``frontend/node_modules`` is.

    The build resolves symlinks before vite.config.ts makes module ids
    frontend-root relative, so a worktree whose ``node_modules`` is a symlink
    to another tree would record ``../../<other-tree>/frontend/node_modules/...``
    (audit ``bundle-03``, review of the vendor lazy-only guard). The plugin
    keys every such id from its last ``node_modules/`` segment instead.
    """
    _built_entry_chunk()
    modules_file = FRONTEND / "build-meta" / "build-modules.json"
    if not modules_file.is_file():
        _skip_unless_required("frontend/build-meta is not built in this checkout")
    modules = json.loads(modules_file.read_text(encoding="utf-8"))
    ids = {module for chunk in modules["chunks"].values() for module in chunk}
    ids.update(modules["entryStaticModules"])

    vendored = sorted(module for module in ids if "node_modules/" in module)
    assert vendored, "the build renders node_modules modules"
    assert [module for module in vendored if not module.startswith("node_modules/")] == []
    assert sorted(module for module in ids if module.startswith(("../", "/"))) == []


def _missing_build_outcome() -> str:
    """How a missing build ends a test: 'skip' or 'fail' (never both)."""
    try:
        _skip_unless_required("frontend/dist is not built in this checkout")
    except pytest.fail.Exception:
        return "fail"
    except pytest.skip.Exception:
        return "skip"
    return "none"


def test_the_require_flag_turns_a_missing_build_into_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(REQUIRE_DIST_FLAG, raising=False)
    assert _missing_build_outcome() == "skip"

    monkeypatch.setenv(REQUIRE_DIST_FLAG, "1")
    assert _missing_build_outcome() == "fail"
