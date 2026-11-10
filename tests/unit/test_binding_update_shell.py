"""The release's binding step in the shell: a resources send is always followed by the migration job (S2).

Spike S2 measured that every App update that carries resources, an identical
array included, grants CREATE ON DATABASE to the App's Lakebase role again
with no restart, and that only the migration job's revoke takes it back. So
the step files record the send (``APP_ROLE_REGRANT_OWED``) and the migration
block runs that job inside the same window, after the binding update, even
when the migration plan has nothing to change; a skip with a send recorded
refuses. A description-only difference is applied online in the no-window
branch, after the resource diff proved the bindings equal. The harness is
``tests/unit/test_quiesce_window_shell.py``'s: the real libraries and step
lines, a fake ``$PYTHON`` that records each call.
"""

from __future__ import annotations

from pathlib import Path

from tests.fixtures.deploy_script import deploy_entrypoint_text
from tests.unit.test_quiesce_window_shell import LIB, _has, _migration_block, _run


def test_a_skip_with_a_resources_send_recorded_refuses_and_never_skips_the_revoke(
    tmp_path: Path,
) -> None:
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_CODE=none; APP_ROLE_REGRANT_OWED=1\n'
        + _migration_block()
        + 'echo "final=$QUIESCE_WINDOW_STATE"\n',
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "holds CREATE ON DATABASE until the migration job revokes it" in result.stderr
    assert not _has(calls, "mip_lakebase_migrate") and "final=" not in result.stdout
    assert "skip the Lakebase migration" not in result.stdout


def test_no_send_and_a_noop_plan_still_skip_the_migration_with_blue_serving(tmp_path: Path) -> None:
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_CODE=none; APP_ROLE_REGRANT_OWED=0\n'
        + _migration_block()
        + 'echo "final=$QUIESCE_WINDOW_STATE"\n',
    )
    assert result.returncode == 0, result.stderr
    assert "skip the Lakebase migration" in result.stdout and not _has(calls, "mip_lakebase_migrate")
    assert "final=blue_serving" in result.stdout


def test_a_wr1_window_runs_the_migration_job_after_its_binding_update_whatever_the_plan(
    tmp_path: Path,
) -> None:
    # The migration plan was noop (no WR3): the window is WR1's alone, and its
    # migration job still runs, after the send, before blue starts again.
    result, calls = _run(
        tmp_path,
        'QUIESCE_WINDOW_STATE=blue_serving; QUIESCE_WINDOW_CODE=window; QUIESCE_WINDOW_REASONS=WR1\n'
        "ensure_quiesce_window_open_for WR4\n"
        "ensure_quiesce_window_open_for WR1\n"
        'echo "binding-send" >> "$CALLS"; APP_ROLE_REGRANT_OWED=1\n'
        "ensure_quiesce_window_open_for WR2\n"
        + _migration_block()
        + 'echo "final=$QUIESCE_WINDOW_STATE"\n',
    )
    assert result.returncode == 0, result.stderr
    order = [
        next(index for index, call in enumerate(calls) if needle in call)
        for needle in ("quiesce_window open", "binding-send", "bundle run mip_lakebase_migrate", "quiesce_window start")
    ]
    assert order == sorted(order), calls
    assert _has(calls, "bundle run mip_lakebase_migrate") == 1 and _has(calls, "quiesce_window open") == 1
    assert "final=blue_serving" in result.stdout


def test_the_step_text_records_every_send_and_applies_a_description_only_difference_online() -> None:
    text = deploy_entrypoint_text()
    step = text.index('step "update existing Databricks App resource bindings without source activation"')

    def at(needle: str, start: int = step) -> int:
        return text.index(needle, start)

    no_window = at('step "prove the serving App\'s resource bindings already equal the bundle (no window)"')
    diff = at("tools.databricks.app_resource_bindings diff", no_window)
    fixup = at("tools.databricks.app_description_fixup", diff)
    otherwise = at("\n  else\n", fixup)
    update = at('run databricks apps update "$_GRANTS_APP_NAME"', otherwise)
    owed = at("APP_ROLE_REGRANT_OWED=1", update)
    migrate = at("databricks bundle run mip_lakebase_migrate", owed)
    close = at("\nclose_quiesce_window\n", migrate)

    assert no_window < diff < fixup < otherwise < update < owed < migrate < close
    # The fix-up never sends resources and runs only once the diff proved them equal.
    fixup_call = text[fixup : text.index("\n    assert_expected_app_identity", fixup)]
    assert '--payload "$APP_RESOURCE_BINDING_PAYLOAD"' in fixup_call
    assert '--expected-app-id "$_EXISTING_APP_ID"' in fixup_call
    first_install = at("databricks apps create", text.index('step "create stopped Databricks App'))
    assert at("APP_ROLE_REGRANT_OWED=1", first_install) < step, "the first install's create records it too"
    assert "APP_ROLE_REGRANT_OWED=0" in (LIB.parent / "deploy.sh").read_text(encoding="utf-8")
