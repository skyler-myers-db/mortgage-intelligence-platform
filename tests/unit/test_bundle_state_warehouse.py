"""The SQL warehouse comes from the deployer's bundle state, not an input (M1-PR3).

* ``python -m tools.databricks.bundle_state warehouse-id`` reads the client
  target's direct-engine state read-only and prints the warehouse it binds
  (masked under GitHub Actions), nothing when it binds none, and exits 2 on
  any read error or a document that is not the reviewed shape.
* The preflight's ``bundle warehouse`` block uses that warehouse, refuses an
  operator ``DATABRICKS_WAREHOUSE_ID`` that names another one, takes the
  operator's value only as a first install's bootstrap warehouse, and refuses
  a first install with neither.
* databricks.yml declares no ``sql_warehouse_id`` variable any more, and
  bundle_env exports none.
"""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from databricks.sdk.errors import NotFound, PermissionDenied

from tests.fixtures.client_profiles import DEMO_PROFILE, IDENTITY_IDS, env_file_text
from tests.fixtures.deploy_block_harness import block, drive
from tests.fixtures.live_workspace import bundle_state
from tools.databricks import bundle_state as state

REPO = Path(__file__).resolve().parents[2]
WAREHOUSE = "a0b1c2d3e4f5a6b7"


class _Workspace:
    def __init__(self, body: object = None, error: BaseException | None = None) -> None:
        self.paths: list[str] = []
        self._body = body
        self._error = error
        self.workspace = SimpleNamespace(download=self._download)

    def _download(self, path: str) -> Any:
        self.paths.append(path)
        if self._error is not None:
            raise self._error
        if self._body is None:
            raise NotFound("no state")
        return io.BytesIO(self._body if isinstance(self._body, bytes) else json.dumps(self._body).encode())


def _main(tmp_path: Path, workspace: _Workspace) -> int:
    env_file = tmp_path / ".env.local"
    env_file.write_text(env_file_text(DEMO_PROFILE), encoding="utf-8")
    return state.main(["warehouse-id"], environ={}, env_file=env_file, client=workspace)


@pytest.fixture(autouse=True)
def _outside_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def test_the_state_path_is_the_client_targets_root() -> None:
    path = state.state_path(deployer=IDENTITY_IDS["deployer"].upper(), client_key="client-one")

    assert path == (
        f"/Workspace/Users/{IDENTITY_IDS['deployer']}/.bundle/mortgage-intelligence-platform/client-one"
        "/state/resources.json"
    )


def test_warehouse_id_prints_the_bound_warehouse(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    workspace = _Workspace(bundle_state(**{"sql_warehouses.mip_serverless_sql": WAREHOUSE}))

    assert _main(tmp_path, workspace) == 0

    assert capsys.readouterr().out == f"{WAREHOUSE}\n"
    assert workspace.paths == [
        f"/Users/{IDENTITY_IDS['deployer']}/.bundle/mortgage-intelligence-platform/"
        f"{DEMO_PROFILE['MIP_CLIENT']}/state/resources.json"
    ], "the Workspace API path, read once"


def test_under_actions_the_warehouse_is_masked_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    workspace = _Workspace(bundle_state(**{"sql_warehouses.mip_serverless_sql": WAREHOUSE}))

    assert _main(tmp_path, workspace) == 0
    out = capsys.readouterr()
    assert out.out == f"{WAREHOUSE}\n"
    assert f"::add-mask::{WAREHOUSE}" in out.err


@pytest.mark.parametrize("body", [None, bundle_state(**{"apps.mip_app": "mip-app"})], ids=["no-state", "no-warehouse"])
def test_a_first_install_prints_nothing(body: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main(tmp_path, _Workspace(body)) == 0

    out = capsys.readouterr()
    assert out.out == ""
    assert "binds no SQL warehouse yet" in out.err


@pytest.mark.parametrize(
    ("workspace", "needle"),
    [
        (_Workspace(error=PermissionDenied(f"denied for {IDENTITY_IDS['deployer']}")), "PermissionDenied"),
        (_Workspace(b"{not json"), "not valid JSON"),
        (_Workspace(bundle_state() | {"state_version": 1}), "version is not the reviewed one"),
        (_Workspace(bundle_state(**{"sql_warehouses.mip_serverless_sql": ""})), "has no live id"),
    ],
    ids=["unreadable", "not-json", "unknown-version", "no-live-id"],
)
def test_a_read_error_or_an_unreviewed_document_exits_2(
    workspace: _Workspace, needle: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, workspace) == 2

    out = capsys.readouterr()
    assert out.out == "" and needle in out.err
    assert IDENTITY_IDS["deployer"] not in out.err


def test_the_cli_takes_exactly_one_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert state.main([]) == 2 and state.main(["warehouse-id", "--extra"]) == 2
    assert "usage" in capsys.readouterr().err


# --------------------------------------------------------------- the preflight


START = "# >>> bundle warehouse >>>"
END = "# <<< bundle warehouse <<<"


def _preflight(tmp_path: Path, *, state_output: str | None, operator: str = "", dry_run: int = 0) -> Any:
    package = tmp_path / "tools" / "databricks"
    package.mkdir(parents=True, exist_ok=True)
    for init in (tmp_path / "tools" / "__init__.py", package / "__init__.py"):
        init.write_text("", encoding="utf-8")
    body = "raise SystemExit(2)" if state_output is None else f"print({state_output!r}, end='')"
    (package / "bundle_state.py").write_text(
        textwrap.dedent(
            f"""\
            import sys
            assert sys.argv[1:] == ["warehouse-id"], sys.argv
            open("bundle_state.called", "w").close()
            {body}
            """
        ),
        encoding="utf-8",
    )
    shell = (
        "deployment_control_value() { "
        f"[[ \"$1\" == DATABRICKS_WAREHOUSE_ID ]] && printf '%s' {operator!r} || printf ''; }}\n"
        + block(START, END)
    )
    return drive(
        tmp_path,
        shell,
        dry_run=dry_run,
        env={},
        epilogue='echo "warehouse=${_GRANTS_WAREHOUSE_ID} source=${WAREHOUSE_SOURCE} exported=$(printenv DATABRICKS_WAREHOUSE_ID)"\n',
    )


def test_the_preflight_uses_the_bundle_states_warehouse(tmp_path: Path) -> None:
    result = _preflight(tmp_path, state_output=WAREHOUSE)

    assert result.returncode == 0, result.stderr
    assert f"warehouse={WAREHOUSE} source=bundle-state exported={WAREHOUSE}" in result.stdout
    assert (tmp_path / "bundle_state.called").exists()


def test_an_operator_warehouse_that_agrees_is_accepted_and_one_that_differs_refuses(tmp_path: Path) -> None:
    agrees = _preflight(tmp_path, state_output=WAREHOUSE, operator=WAREHOUSE)
    assert agrees.returncode == 0, agrees.stderr

    differs = _preflight(tmp_path, state_output=WAREHOUSE, operator="another-warehouse")
    assert differs.returncode == 2
    assert "names another warehouse than this deployer's bundle state binds" in differs.stderr
    assert "reached-after-block" not in differs.stdout


def test_a_first_install_bootstraps_on_the_operators_warehouse_or_refuses(tmp_path: Path) -> None:
    bootstrap = _preflight(tmp_path, state_output="", operator="bootstrap-warehouse")
    assert bootstrap.returncode == 0, bootstrap.stderr
    assert "warehouse=bootstrap-warehouse source=first-install-bootstrap" in bootstrap.stdout

    neither = _preflight(tmp_path, state_output="")
    assert neither.returncode == 2
    assert "binds no SQL warehouse yet (a first install)" in neither.stderr


def test_an_unreadable_state_refuses(tmp_path: Path) -> None:
    result = _preflight(tmp_path, state_output=None, operator=WAREHOUSE)

    assert result.returncode == 2
    assert "bundle state could not be read; nothing was changed" in result.stderr


def test_a_dry_run_reads_no_state(tmp_path: Path) -> None:
    result = _preflight(tmp_path, state_output=WAREHOUSE, dry_run=1)

    assert result.returncode == 0, result.stderr
    assert "source=dry-run (bundle state unread)" in result.stdout
    assert not (tmp_path / "bundle_state.called").exists()


def test_the_warehouse_switches_to_the_bundles_after_the_apply() -> None:
    text = block("# >>> bundle warehouse: after apply >>>", "# <<< bundle warehouse: after apply <<<")

    assert 'WAREHOUSE_SOURCE" == "bundle-state" && "$_APPLIED_WAREHOUSE_ID" != "$_GRANTS_WAREHOUSE_ID"' in text
    assert '_GRANTS_WAREHOUSE_ID="$_APPLIED_WAREHOUSE_ID"' in text


# --------------------------------------------------------------- the dead variable


def test_no_bundle_variable_names_a_warehouse() -> None:
    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    source = (REPO / "tools" / "databricks" / "bundle_env.py").read_text(encoding="utf-8")

    assert "sql_warehouse_id" not in bundle["variables"]
    assert "${var.sql_warehouse_id}" not in (REPO / "databricks.yml").read_text(encoding="utf-8")
    assert 'env["BUNDLE_VAR_sql_warehouse_id"]' not in source
    assert 'env.pop("BUNDLE_VAR_sql_warehouse_id", None)' in source, "an ambient export is dropped"
