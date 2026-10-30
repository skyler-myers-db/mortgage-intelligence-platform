"""The profile's MIP_UC_CATALOG_STORAGE_ROOT becomes the catalog's MANAGED LOCATION (M1-PR3).

A first install creates the install catalog twice over: the pipeline
namespace bootstrap (tools/databricks/ensure_pipeline_namespace.py; its own
test covers the create call) creates it empty before the bundle apply, and
``sql/ddl/001_catalogs_schemas.sql`` re-asserts it with ``CREATE CATALOG IF
NOT EXISTS``. With the profile's storage root set, both create it at that
managed location; unset, the render is byte-identical to before. The deploy
hands both tools the variable's name, never its value.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tests.fixtures.deploy_script import deploy_entrypoint_text
from tools import render_sql
from tools.databricks import bundle_env

REPO = Path(__file__).resolve().parents[2]
DDL = REPO / "sql" / "ddl" / "001_catalogs_schemas.sql"
ROOT = "s3://bucket-alpha/mip"


def _catalog_ddl(**kwargs: Any) -> str:
    text = DDL.read_text(encoding="utf-8")
    return render_sql._render_text(text, "mip", demo_first_party_enabled=False, **kwargs)[0]


def test_an_unset_root_renders_the_catalog_ddl_byte_identically() -> None:
    plain = _catalog_ddl()

    assert _catalog_ddl(storage_root=None) == plain
    assert render_sql._storage_root(None) is None and render_sql._storage_root("   ") is None
    assert "MANAGED LOCATION" not in plain


def test_a_root_renders_the_managed_location_before_the_comment() -> None:
    rendered = _catalog_ddl(storage_root=ROOT)

    assert f"CREATE CATALOG IF NOT EXISTS mip\nMANAGED LOCATION '{ROOT}'\nCOMMENT " in rendered
    assert rendered.count("MANAGED LOCATION") == 1, "only the catalog statement"
    assert rendered.replace(f"\nMANAGED LOCATION '{ROOT}'", "") == _catalog_ddl()


@pytest.mark.parametrize(
    "root",
    ["s3://bucket'; DROP CATALOG mip; --", "s3://bucket alpha", "https://bucket/x", "s3://", 's3://a"b', "s3://a\\b"],
)
def test_a_root_that_is_not_a_plain_storage_root_is_refused(root: str) -> None:
    with pytest.raises(ValueError, match="storage root"):
        render_sql._storage_root(root)


def test_the_cli_reads_the_named_variable_and_never_prints_the_root(tmp_path: Path) -> None:
    dest = tmp_path / "rendered"
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("MIP_")}, "MIP_UC_CATALOG_STORAGE_ROOT": ROOT}

    completed = subprocess.run(
        [
            sys.executable,
            "tools/render_sql.py",
            "--catalog",
            "mip",
            "--dest",
            str(dest),
            "--storage-root-env",
            "MIP_UC_CATALOG_STORAGE_ROOT",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert ROOT not in completed.stdout + completed.stderr
    assert f"MANAGED LOCATION '{ROOT}'" in (dest / "ddl" / "001_catalogs_schemas.sql").read_text(encoding="utf-8")


def test_bundle_env_renders_the_profiles_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(paychex_shaped_profile(MIP_UC_CATALOG_STORAGE_ROOT=ROOT)), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path)
    for name in [name for name in os.environ if name.startswith(("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_"))]:
        monkeypatch.delenv(name)
    renders: list[dict[str, Any]] = []
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **kwargs: renders.append(kwargs) or (1, 0, 0))
    monkeypatch.setattr(
        bundle_env.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])

    assert bundle_env.main() == 0
    assert {render["storage_root"] for render in renders} == {ROOT}

    renders.clear()
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "ci"])
    assert bundle_env.main() == 0
    assert {render["storage_root"] for render in renders} == {None}, "the ci target renders none"


def test_the_deploy_names_the_variable_and_never_its_value() -> None:
    text = deploy_entrypoint_text()

    assert 'run "$PYTHON" tools/render_sql.py --catalog "${MIP_DEFAULT_CATALOG:-mip}" \\\n  --storage-root-env MIP_UC_CATALOG_STORAGE_ROOT' in text
    assert "  --storage-root-env MIP_UC_CATALOG_STORAGE_ROOT\n)" in text, "the pipeline namespace bootstrap"
    assert "$MIP_UC_CATALOG_STORAGE_ROOT" not in text.replace('export MIP_UC_CATALOG_STORAGE_ROOT', "")
