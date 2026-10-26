"""The bundle treemap stays a dev-only PR artifact (audit quality-08, W5e).

rollup-plugin-visualizer is an exact devDependency loaded ONLY by
frontend/vite.analyze.config.ts, which refuses to load without ANALYZE=1 and
builds under the git-ignored frontend/bundle-analyze/ (never frontend/dist).
The production vite.config.ts never imports it, and CI's informational
bundle-delta job uploads the treemap as a PR artifact (docs/testing.md).
The config is outside tsc's ``src`` include and eslint's ``*.config.*``
ignore, so these static checks are its contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
ANALYZE_CONFIG = FRONTEND / "vite.analyze.config.ts"
PRODUCTION_CONFIG = FRONTEND / "vite.config.ts"
PIN = "7.1.1"


def _bundle_delta_steps() -> list[dict[str, Any]]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))["jobs"]["bundle-delta"]["steps"]


def test_the_visualizer_is_an_exact_dev_only_pin_with_a_lock_entry_and_a_licence_row() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))["packages"]

    assert package["devDependencies"]["rollup-plugin-visualizer"] == PIN
    assert "rollup-plugin-visualizer" not in package["dependencies"]
    entry = lock["node_modules/rollup-plugin-visualizer"]
    assert entry["version"] == PIN and entry["dev"] is True and entry["license"] == "MIT"
    assert package["scripts"]["build:analyze"] == "ANALYZE=1 vite build --config vite.analyze.config.ts"
    licences = (ROOT / "docs" / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
    assert re.search(r"^\| `rollup-plugin-visualizer` \(7\.1\.1\).*\| `MIT`", licences, re.MULTILINE)


def test_the_analyze_config_refuses_without_analyze_and_writes_under_bundle_analyze() -> None:
    text = ANALYZE_CONFIG.read_text(encoding="utf-8")

    assert 'import { visualizer } from "rollup-plugin-visualizer";' in text
    assert 'import productionConfig from "./vite.config.ts";' in text and "mergeConfig(productionConfig," in text
    assert re.search(r'if \(process\.env\.ANALYZE !== "1"\) \{\s*throw new Error\(', text)
    assert 'outDir: "bundle-analyze/dist"' in text and "emptyOutDir: true" in text
    assert 'filename: "bundle-analyze/treemap.html"' in text and 'template: "treemap"' in text
    assert "gzipSize: true" in text and "brotliSize: true" in text and "sourcemap: false" in text
    assert '"dist"' not in text.replace('"bundle-analyze/dist"', ""), "never the production dist"


def test_the_production_config_never_loads_the_visualizer() -> None:
    assert "rollup-plugin-visualizer" not in PRODUCTION_CONFIG.read_text(encoding="utf-8")
    assert "visualizer(" not in PRODUCTION_CONFIG.read_text(encoding="utf-8")


def test_the_bundle_delta_job_uploads_the_head_treemap() -> None:
    steps = _bundle_delta_steps()
    names = [step.get("name") for step in steps]
    summary = names.index("Head vs base (step summary)")

    build = steps[summary + 1]
    upload = steps[summary + 2]
    assert build["name"] == "Bundle treemap (head)" and build["run"] == "npm --prefix frontend run build:analyze"
    assert upload["uses"] == "actions/upload-artifact@v7"
    assert upload["with"] == {
        "name": "bundle-treemap-${{ github.sha }}",
        "path": "frontend/bundle-analyze/treemap.html",
        "if-no-files-found": "error",
        "retention-days": 14,
    }
    for step in (build, upload):
        assert "continue-on-error" not in step and "if" not in step and "env" not in step


def test_the_treemap_build_is_ignored_by_git() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "frontend/bundle-analyze/" in lines
