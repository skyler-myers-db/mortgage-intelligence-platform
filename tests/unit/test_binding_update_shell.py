"""The release's binding step in the shell: a resources send is always followed by the migration job (S2).

Spike S2 measured that every App update that carries resources, an identical
array included, grants CREATE ON DATABASE to the App's Lakebase role again
with no restart, and that only the migration job's revoke takes it back. So
the step files record the send (``APP_ROLE_REGRANT_OWED``) and the migration
block runs that job inside the same window, after the binding update, even
when the migration plan has nothing to change; a skip with a send recorded
refuses. The binding step sends resources only in a WR1 window (or an
ungoverned run): a run with no WR1, a window opened for WR4 included, proves
the bindings equal, with no send and so no re-grant. A description-only
difference is applied online only while blue serves: every create-update
the spikes measured ran on a serving App, and a WR4 window has stopped it.
The harness is ``tests/unit/test_quiesce_window_shell.py``'s: the real
libraries and step lines, a fake ``$PYTHON`` that records each call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

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

    no_window = at('step "prove the App\'s resource bindings already equal the bundle (no WR1, no resources send)"')
    diff = at("tools.databricks.app_resource_bindings diff", no_window)
    fixup = at("tools.databricks.app_description_fixup", diff)
    otherwise = at("\n  else\n", fixup)
    update = at('run databricks apps update "$_GRANTS_APP_NAME"', otherwise)
    owed = at("APP_ROLE_REGRANT_OWED=1", update)
    migrate = at("databricks bundle run mip_lakebase_migrate", owed)
    close = at("\nclose_quiesce_window\n", migrate)

    assert no_window < diff < fixup < otherwise < update < owed < migrate < close
    # The fix-up never sends resources and runs only once the diff proved them equal.
    fixup_call = text[fixup : text.index("\n      assert_expected_app_identity", fixup)]
    assert '--payload "$APP_RESOURCE_BINDING_PAYLOAD"' in fixup_call
    assert '--expected-app-id "$_EXISTING_APP_ID"' in fixup_call
    first_install = at("databricks apps create", text.index('step "create stopped Databricks App'))
    assert at("APP_ROLE_REGRANT_OWED=1", first_install) < step, "the first install's create records it too"
    assert "APP_ROLE_REGRANT_OWED=0" in (LIB.parent / "deploy.sh").read_text(encoding="utf-8")


def _binding_branch() -> str:
    """The step file's real lines from the no-WR1 test through the send branch's end."""

    text = (LIB / "deploy_step_bundle_apply.sh").read_text(encoding="utf-8")
    start = text.index('  if [[ -n "${QUIESCE_WINDOW_STATE:-}" ]] && ! quiesce_window_has_reason WR1; then\n')
    end = text.index("\n  fi\nfi\n", start) + len("\n  fi\n")
    return text[start:end]


_BINDING_STUBS = """APP_RESOURCE_BINDING_PAYLOAD="$TMPDIR/payload.json"; APP_RESOURCE_BINDING_BEFORE="$TMPDIR/before.json"
APP_RESOURCE_BINDING_AFTER="$TMPDIR/after.json"; APP_ROLE_REGRANT_OWED=0
assert_expected_app_identity() { echo "identity $1" >> "$CALLS"; }
databricks() { echo "databricks $*" >> "$CALLS"; }
binding_python() {
  printf '%s\\n' "$*" >> "$CALLS"
  case "$*" in *"app_resource_bindings diff"*) return "${DIFF_RC:-0}" ;; esac
}
PYTHON=binding_python
"""


def _binding_step(
    tmp_path: Path, state: str, reasons: str, **env: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    return _run(
        tmp_path,
        f"QUIESCE_WINDOW_STATE={state}; QUIESCE_WINDOW_REASONS={reasons}\n"
        + _BINDING_STUBS
        + _binding_branch()
        + 'echo "owed=$APP_ROLE_REGRANT_OWED"\n',
        **env,
    )


@pytest.mark.parametrize(
    ("state", "reasons", "fixups"),
    (
        ("blue_serving", "-", 1),
        ("window_open", "WR4", 0),
        ("window_open", "WR2,WR4", 0),
        ("blue_degraded", "-", 1),
    ),
    ids=("no-window", "wr4-window", "wr2-wr4-window", "blue-degraded"),
)
def test_a_run_with_no_wr1_never_sends_resources_and_owes_no_revoke(
    tmp_path: Path, state: str, reasons: str, fixups: int
) -> None:
    """Review of #337: a WR4 window used to send an equal set and re-grant CREATE ON DATABASE.

    Round 2: the description fix-up runs only while blue serves. A WR4 window
    has stopped the App, and no spike measured a create-update on a stopped one.
    """

    result, calls = _binding_step(tmp_path, state, reasons)

    assert result.returncode == 0, result.stderr
    assert not _has(calls, "apps update") and not _has(calls, "app_resource_bindings verify")
    assert _has(calls, "app_resource_bindings diff") == 1
    assert _has(calls, "app_description_fixup") == fixups
    assert ("leave the App description to a later release" in result.stdout) is (fixups == 0)
    assert "owed=0" in result.stdout


def test_a_binding_diff_with_no_wr1_refuses_and_sends_nothing(tmp_path: Path) -> None:
    result, calls = _binding_step(tmp_path, "window_open", "WR4", DIFF_RC="3")

    assert result.returncode == 2
    assert "No resources were sent" in result.stderr
    assert not _has(calls, "apps update") and not _has(calls, "app_description_fixup")
    assert "owed=" not in result.stdout


@pytest.mark.parametrize(
    ("state", "reasons"),
    (("window_open", "WR1"), ("window_open", "WR1,WR4"), ("", "")),
    ids=("wr1-window", "wr1-wr4-window", "ungoverned"),
)
def test_a_wr1_window_or_an_ungoverned_run_sends_and_records_the_re_grant(
    tmp_path: Path, state: str, reasons: str
) -> None:
    result, calls = _binding_step(tmp_path, state, reasons)

    assert result.returncode == 0, result.stderr
    assert _has(calls, "databricks apps update mip-app --json @") == 1
    assert _has(calls, "app_resource_bindings verify") == 1
    assert not _has(calls, "app_resource_bindings diff") and not _has(calls, "app_description_fixup")
    assert "owed=1" in result.stdout
