"""The bundle's MLflow experiment renders a valid workspace path in every target.

Root cause (post-W5 deploy-hardening, 2026-10-27): the Databricks CLI's presets
step rewrites every experiment name as ``path.Dir(name) + "/" + name_prefix +
path.Base(name)``, in every target and mode, with an empty prefix outside
``mode: development``. A workspace-root name has ``path.Dir == "/"``, so
``/mip-lead-scoring`` rendered ``//[dev someone] mip-lead-scoring`` for dev and
``//mip-lead-scoring`` for production and no-mode targets. Recorded with CLI
v1.10.0 by read-only ``bundle validate -p paychex`` runs (the repo bundle's dev
target, plus a scratch bundle with development, production and no-mode targets);
``_cli_presets_name`` mirrors that rewrite and is checked against those renders
(with a synthetic short name in the dev prefix).
"""

from __future__ import annotations

import posixpath
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "databricks.yml"
DEV_PREFIX = "[dev someone] "  # synthetic stand-in for the CLI's `[dev <short user name>] ` prefix


def _cli_presets_name(name: str, prefix: str) -> str:
    """The CLI v1.10.0 presets rewrite of an experiment name (Go ``path`` semantics)."""

    directory = posixpath.normpath(posixpath.dirname(name) or ".")
    base = posixpath.basename(name)
    return prefix + base if directory == "." else f"{directory}/{prefix}{base}"


def _bundle() -> dict:
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))


def _target_prefix(target: dict) -> str:
    presets = target.get("presets") or {}
    if "name_prefix" in presets:
        return str(presets["name_prefix"])
    return DEV_PREFIX if target.get("mode") == "development" else ""


@pytest.mark.parametrize(
    ("name", "prefix", "rendered"),
    (
        ("/mip-lead-scoring", DEV_PREFIX, "//[dev someone] mip-lead-scoring"),
        ("/mip-lead-scoring", "", "//mip-lead-scoring"),
        ("/Shared/mip/lead-scoring", DEV_PREFIX, "/Shared/mip/[dev someone] lead-scoring"),
        ("/Shared/mip-lead-scoring", DEV_PREFIX, "/Shared/[dev someone] mip-lead-scoring"),
        ("/Shared/mip-lead-scoring", "", "/Shared/mip-lead-scoring"),
    ),
)
def test_the_mirror_reproduces_the_recorded_cli_renders(name: str, prefix: str, rendered: str) -> None:
    assert _cli_presets_name(name, prefix) == rendered


def test_no_target_overrides_the_experiment_so_the_root_name_is_what_every_target_renders() -> None:
    bundle = _bundle()

    assert set(bundle["resources"]["experiments"]) == {"mip_lead_scoring"}
    for target_name, target in bundle["targets"].items():
        overrides = (target.get("resources") or {}).get("experiments")
        assert not overrides, f"target {target_name} overrides the experiment"


def test_the_declared_name_is_one_level_under_shared() -> None:
    declared = _bundle()["resources"]["experiments"]["mip_lead_scoring"]["name"]

    assert declared == "/Shared/mip-lead-scoring"


def test_every_target_renders_one_valid_path_directly_under_shared() -> None:
    bundle = _bundle()
    name = bundle["resources"]["experiments"]["mip_lead_scoring"]["name"]

    assert set(bundle["targets"]) == {"client", "ci"}
    for target_name, target in bundle["targets"].items():
        prefix = _target_prefix(target)
        rendered = _cli_presets_name(name, prefix)

        assert rendered.startswith("/") and "//" not in rendered, (target_name, rendered)
        assert posixpath.normpath(rendered) == rendered, (target_name, rendered)
        # /Shared exists in every workspace; a deeper parent breaks a fresh apply.
        assert posixpath.dirname(rendered) == "/Shared", (target_name, rendered)
        assert posixpath.basename(rendered) == f"{prefix}mip-lead-scoring", target_name


def test_no_target_prefixes_the_experiment() -> None:
    """The development-mode dev target, the only prefixed one, left with M1-PR2."""

    targets = _bundle()["targets"]

    assert _target_prefix(targets["client"]) == "" and _target_prefix(targets["ci"]) == ""
    assert targets["client"]["mode"] == "production" and "mode" not in targets["ci"]
