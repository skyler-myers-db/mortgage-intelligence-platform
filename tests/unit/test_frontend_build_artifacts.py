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


_MODULE_SCRIPT = re.compile(r'<script type="module" crossorigin src="/(assets/[^"]+\.js)"></script>')


def _module_scripts(html: str) -> list[str]:
    return _MODULE_SCRIPT.findall(html)


def _built_manifest() -> dict[str, dict[str, object]]:
    manifest_file = FRONTEND / "build-meta" / "build-manifest.json"
    if not manifest_file.is_file():
        _skip_unless_required("frontend/build-meta is not built in this checkout")
    return json.loads(manifest_file.read_text(encoding="utf-8"))


def test_built_html_loads_the_boot_module_just_before_the_entry() -> None:
    """Audit ``bundle-02``: the boot module starts the four non-audited boot
    reads while the entry downloads. No inline script, no fetch preload."""
    _built_entry_chunk()
    manifest = _built_manifest()
    boot = manifest["src/boot/primeBoot.ts"]["file"]
    entry = manifest["index.html"]["file"]
    html = (DIST / "index.html").read_text(encoding="utf-8")

    assert _module_scripts(html) == [boot, entry]
    assert html.index(f'src="/{boot}"') < html.index(f'src="/{entry}"')
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html) is None, "no inline script (CSP script-src 'self')"
    assert 'as="fetch"' not in html


def test_built_html_paints_the_text_free_shell_skeleton() -> None:
    """Audit ``bundle-10``: #root carries the aria-hidden shell skeleton, with
    no text and no inline style, in both shells."""
    _built_entry_chunk()
    for shell in ("index.html", "index.home.html"):
        html = (DIST / shell).read_text(encoding="utf-8")
        match = re.search(r'<div id="root">(<div class="app-shell" aria-hidden="true">.*?)</div>\s*</body>', html, re.S)
        assert match, f"{shell}: #root holds the shell skeleton"
        skeleton = match.group(1)
        assert re.sub(r"<[^>]+>", "", skeleton) == "", "no text nodes"
        assert "style=" not in skeleton and " id=" not in skeleton and "role=" not in skeleton
        for name in ("rail", "topbar", "main", "route-nav", "proto-hero", "skeleton skeleton--title"):
            assert f'class="{name}"' in skeleton


_MODULEPRELOAD = re.compile(r'<link rel="modulepreload" crossorigin href="/(assets/[^"]+\.js)">')


def _static_closure(manifest: dict[str, dict[str, object]], start: str) -> set[str]:
    seen: set[str] = set()
    queue = [start]
    while queue:
        key = queue.pop()
        if key in seen:
            continue
        seen.add(key)
        queue.extend(str(imported) for imported in manifest[key].get("imports", []))  # type: ignore[union-attr]
    return seen


def _home_closure_files(manifest: dict[str, dict[str, object]]) -> set[str]:
    """Home's JS beyond the initial closure, from the manifest (not the plugin's own maths)."""
    keys = _static_closure(manifest, "src/routes/home.tsx") - _static_closure(manifest, "index.html")
    return {str(manifest[key]["file"]) for key in keys if str(manifest[key]["file"]).endswith(".js")}


def test_root_serves_the_home_variant_and_deep_links_the_plain_shell() -> None:
    """Audit ``bundle-02``: exactly ``/`` preloads Home's closure beside the
    entry; a deep link never pays for Home's chunks. Both shells carry the
    boot module just before the entry and are never cached."""
    _built_entry_chunk()
    manifest = _built_manifest()
    boot = manifest["src/boot/primeBoot.ts"]["file"]
    entry = manifest["index.html"]["file"]
    home_files = _home_closure_files(manifest)
    assert home_files, "the Home route has chunks beyond the initial closure"
    assert (DIST / "index.home.html").is_file()
    from backend.main import app

    client = TestClient(app)
    root = client.get("/")
    root_with_query = client.get("/?view=map")
    deep = client.get("/lead-queue")
    by_name = client.get("/index.home.html")
    plain = (DIST / "index.html").read_text(encoding="utf-8")

    for response in (root, root_with_query, deep, by_name):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
        assert _module_scripts(response.text) == [boot, entry]
    assert root.text == root_with_query.text
    assert set(_MODULEPRELOAD.findall(root.text)) - set(_MODULEPRELOAD.findall(plain)) == home_files
    assert deep.text == plain
    assert by_name.text == plain, "a shell requested by name is served as the plain shell"
    assert not home_files & set(_MODULEPRELOAD.findall(deep.text))
    assert not re.search(r'href="/assets/home-[^"]+\.js"', deep.text)
    assert 'rel="preload" as="style"' not in root.text


def test_the_genie_answer_chunk_is_named_for_its_family() -> None:
    """w3 review #88: the shared chunk holding GenieAnswer.tsx is
    ``genie-answer-*.js`` (a naming-only chunkFileNames rule), not the name of
    an arbitrary member such as ``useGenieTurnCollapse``."""
    _built_entry_chunk()
    modules_file = FRONTEND / "build-meta" / "build-modules.json"
    if not modules_file.is_file():
        _skip_unless_required("frontend/build-meta is not built in this checkout")
    chunks: dict[str, list[str]] = json.loads(modules_file.read_text(encoding="utf-8"))["chunks"]

    holders = [name for name, modules in chunks.items() if "src/components/mortgage/GenieAnswer.tsx" in modules]
    assert len(holders) == 1
    assert re.fullmatch(r"assets/genie-answer-[\w-]+\.js", holders[0]), holders[0]
    assert not [name for name in chunks if name.startswith("assets/useGenieTurnCollapse-")]


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
