"""deploy.sh wiring of the App's observed-lineage view (optional evidence).

The ``observed lineage view: publish`` block runs the helper as the deploying
identity, then grants the App ``SELECT`` on that one view, never on the
system schema. Helper exit 3 (the deployer cannot read
``system.access.table_lineage``) is the only non-zero outcome that continues:
it warns, naming the missing privilege, and grants nothing. Any other failure
stops the deploy. The block is read from ``deploy_entrypoint_text()`` and
driven in the strict harness with a stub helper and a recording
``apply_uc_grant``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import block, code, drive, function, stub_tool
from tests.fixtures.deploy_script import deploy_entrypoint_text

START = "# >>> observed lineage view: publish >>>"
END = "# <<< observed lineage view: publish <<<"
APP = "app-sp-client-id"
ENV = {"_GRANTS_WAREHOUSE_ID": "wh-1", "_GRANTS_CATALOG": "summit_mortgage", "APP_SP_CLIENT_ID": APP}
ARGV = ["--warehouse-id", "wh-1", "--catalog", "summit_mortgage"]
GRANT = f"GRANT SELECT ON TABLE summit_mortgage.audit.asset_lineage TO `{APP}`"


def _recording_grant(repo: Path) -> str:
    log = repo / "grants.log"
    return f"apply_uc_grant() {{ printf '%s\\n' \"$1\" >> {str(log)!r}; }}\n"


def _drive(repo: Path, *, exit_code: int, dry_run: int = 0) -> tuple[int, str, str, list[str], bool]:
    called = stub_tool(
        repo, "ensure_asset_lineage_view", argv=ARGV, body=f"sys.exit({exit_code})\n"
    )
    grant_fn = function("apply_uc_grant") if dry_run else _recording_grant(repo)
    result = drive(repo, grant_fn + block(START, END), dry_run=dry_run, env=ENV, epilogue="")
    log = repo / "grants.log"
    grants = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result.returncode, result.stdout, result.stderr, grants, called.exists()


def test_the_block_runs_after_the_base_grants_and_before_the_secret_scopes() -> None:
    text = deploy_entrypoint_text()
    start = text.index(START)

    assert text.index("initialize_uc_targets_and_reconcile_function_grants\n") < start
    assert text.index("\nGRANTS_EOF\n") < start
    assert start < text.index('step "provision pii-salt secret scope')
    assert start < text.index("# >>> app release source: publish >>>")


def test_the_block_names_one_helper_and_one_view_grant_only() -> None:
    shell = code(block(START, END))

    assert shell.count("-m tools.") == 1
    assert shell.count("apply_uc_grant ") == 1
    assert (
        'apply_uc_grant "GRANT SELECT ON TABLE ${_GRANTS_CATALOG}.audit.asset_lineage '
        'TO \\`${APP_SP_CLIENT_ID}\\`"'
    ) in shell
    assert shell.count("GRANT ") == 1
    assert "ON SCHEMA" not in shell and "ON CATALOG" not in shell


def test_a_published_view_is_granted_to_the_app(tmp_path: Path) -> None:
    rc, stdout, stderr, grants, called = _drive(tmp_path, exit_code=0)

    assert rc == 0, stderr
    assert called
    assert grants == [GRANT]
    assert "reached-after-block" in stdout
    assert "WARNING" not in stderr


def test_a_deployer_without_system_table_access_warns_and_grants_nothing(tmp_path: Path) -> None:
    rc, stdout, stderr, grants, called = _drive(tmp_path, exit_code=3)

    assert rc == 0, stderr
    assert called
    assert grants == []
    assert "reached-after-block" in stdout
    assert "WARNING: observed lineage stays unavailable" in stderr
    for privilege in (
        "USE CATALOG on system",
        "USE SCHEMA on system.access",
        "SELECT on system.access.table_lineage",
    ):
        assert privilege in stderr
    assert "summit_mortgage.audit.asset_lineage was not published" in stderr


@pytest.mark.parametrize("exit_code", [1, 2, 4])
def test_any_other_helper_failure_stops_the_deploy(tmp_path: Path, exit_code: int) -> None:
    rc, stdout, stderr, grants, _called = _drive(tmp_path, exit_code=exit_code)

    assert rc == exit_code
    assert grants == []
    assert "reached-after-block" not in stdout
    assert f"observed-lineage view publish failed (exit {exit_code})" in stderr


def test_a_dry_run_calls_no_helper_and_only_reports_the_grant(tmp_path: Path) -> None:
    rc, stdout, stderr, _grants, called = _drive(tmp_path, exit_code=4, dry_run=1)

    assert rc == 0, stderr
    assert not called
    assert f"would grant: {GRANT}" in stdout
    assert "reached-after-block" in stdout
