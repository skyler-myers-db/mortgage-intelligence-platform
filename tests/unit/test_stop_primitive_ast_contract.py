"""Every SDK App stop, start, deploy and update in tools/ is reviewed and reached only by the window (O16).

``tools/databricks/stop_primitive_map.py`` is the reviewed map. Its AST scan
must find exactly ``PRIMITIVE_SITES``; the static call graph from each site
back to a CLI ``main`` must find exactly the reviewed ``ENTRY_POINTS``; each
mutating mode must be one the module's parser accepts; and a shell file
outside the two stop libraries that invokes such a mode, or carries an inline
``deploy_and_wait``, fails. The mutation tests add each kind of stray call to
a copy and require the contract to notice.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.databricks import stop_primitive_map as stops

REPO = Path(__file__).resolve().parents[2]
STEP_FILE = REPO / "scripts" / "lib" / "deploy_step_bundle_apply.sh"


def test_the_ast_finds_exactly_the_reviewed_primitive_sites() -> None:
    assert stops.python_primitive_sites() == set(stops.PRIMITIVE_SITES)


def test_every_site_is_reached_only_by_reviewed_entry_points() -> None:
    reached = stops.reaching_entry_points()
    assert set(reached) == set(stops.PRIMITIVE_SITES)
    for site, entries in reached.items():
        assert entries, f"{site} is reached by no CLI entry point"
        assert entries <= set(stops.ENTRY_POINTS), f"{site}: unreviewed {entries - set(stops.ENTRY_POINTS)}"
    assert set().union(*reached.values()) == set(stops.ENTRY_POINTS)


def test_every_reviewed_mode_is_one_the_entry_point_parses() -> None:
    for module, entry in stops.ENTRY_POINTS.items():
        source = (REPO / Path(*module.split(".")).with_suffix(".py")).read_text(encoding="utf-8")
        if module.endswith("app_deployment_rollback"):
            source += (REPO / "tools/databricks/app_deployment_rollback_cli.py").read_text(encoding="utf-8")
        for mode in entry.modes:
            if mode != "(default)":
                assert f'"{mode}"' in source, f"{module} does not parse {mode}"
        if entry.positional:
            assert any(entry.modes.values()) and not all(entry.modes.values())


def test_the_stop_libraries_are_the_only_callers_of_a_mutating_mode() -> None:
    for entry in stops.ENTRY_POINTS.values():
        assert entry.callers <= stops.STOP_LIBRARIES
    assert stops.repo_shell_violations() == []


@pytest.mark.parametrize(
    "stray",
    (
        '  "$PYTHON" -m tools.databricks.app_deployment_rollback restore --app-name "$APP_NAME"\n',
        '  "$PYTHON" -m tools.databricks.app_deployment_rollback \\\n    restore \\\n    --app-name "$A"\n',
        '"$PYTHON" - <<\'PYEOF\'\nworkspace.apps.deploy_and_wait(app_name, deployment)\nPYEOF\n',
        '"$PYTHON" - <<\'PYEOF\'\nclient.apps.start(app_name)\nPYEOF\n',
        '  "$PYTHON" -m tools.databricks.quiesce_window await --app-name "$A"\n',
    ),
)
def test_a_step_file_that_calls_a_primitive_fails_the_contract(tmp_path: Path, stray: str) -> None:
    copy = tmp_path / "scripts" / "lib" / STEP_FILE.name
    copy.parent.mkdir(parents=True)
    (tmp_path / "scripts" / "deploy.sh").write_text("", encoding="utf-8")
    copy.write_text(STEP_FILE.read_text(encoding="utf-8") + "\n" + stray, encoding="utf-8")
    violations = stops.repo_shell_violations(root=tmp_path)
    assert violations and all(STEP_FILE.name in finding for finding in violations)


def _tools_copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO / "tools", root / "tools", ignore=shutil.ignore_patterns("__pycache__"))
    return root


def test_a_new_sdk_primitive_in_a_tool_is_an_unmapped_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_restart.py").write_text(
        "def restart(workspace, name):\n    workspace.apps.stop_and_wait(name)\n"
        "    workspace.apps.start(name)\n\n\ndef main():\n    restart(None, 'x')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    found = stops.python_primitive_sites(root / "tools")
    assert found - set(stops.PRIMITIVE_SITES) == {
        ("tools/databricks/stray_restart.py", "restart", "stop_and_wait"),
        ("tools/databricks/stray_restart.py", "restart", "start"),
    }


def test_a_new_cli_that_reaches_a_reviewed_primitive_is_an_unreviewed_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tools_copy(tmp_path)
    (root / "tools" / "databricks" / "stray_cli.py").write_text(
        "from tools.databricks.stop_app_fail_closed import stop_app_fail_closed\n\n\n"
        "def main():\n    stop_app_fail_closed(app_name='x')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(stops, "REPO", root)
    reached = stops.reaching_entry_points(root / "tools")
    stop_site = ("tools/databricks/stop_app_fail_closed.py", "stop_app_fail_closed", "stop")
    assert "tools.databricks.stray_cli" in reached[stop_site]
    assert "tools.databricks.stray_cli" not in stops.ENTRY_POINTS
