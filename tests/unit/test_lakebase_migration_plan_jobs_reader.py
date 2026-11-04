"""``--mode plan`` reads the jobs reader's grants beside the decision (M2-PR5 x M0-PR8).

The bundle's migration job grants the non-admin jobs identity its Lakebase
``jobs_reader`` role, and the quiesce window skips that job when the plan is a
noop. So the plan reports ``jobs_reader_grants`` (``off``, ``exact`` or
``drift``, an absent role included), which the window plan reads as WR2
when the plan is a noop. It never changes the decision or blue's
restorability: blue never reads as that role. The fakes and the static walk
are the plan suite's own.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from jobs import lakebase_migrate, lakebase_migration_plan
from jobs.lakebase_migration_inputs import render_schema_inputs
from jobs.lakebase_migration_plan import CheckContext, main_read_only, run_plan
from jobs.lakebase_migration_read_only import ReadOnlyStatementRefused
from tests.unit.test_lakebase_migration_plan import (
    _APP_ROLE,
    _CTX,
    _MIGRATIONS,
    _REPO_COMPONENTS,
    _REPO_DIGEST,
    ROOT,
    _assert_read_only_session,
    _Catalog,
    _install,
    _reachable_statements,
    _stub_checks,
)

# A stand-in for the jobs identity's client id: it must never reach a report.
_JOBS_READER = "jobs-reader-client-id-0003"
_CTX_JOBS = CheckContext(app_role=_APP_ROLE, verifier_role=None, jobs_reader_role=_JOBS_READER)


@pytest.fixture(autouse=True)
def _default_to_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABRICKS_BUNDLE_TARGET", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)


def _plan(
    monkeypatch: pytest.MonkeyPatch, catalog: _Catalog, ctx: CheckContext, **checks: str
) -> tuple[dict[str, Any], list[str]]:
    connection = _install(monkeypatch, catalog.route)
    _stub_checks(monkeypatch, **checks)
    report = run_plan(
        {"host": "test"},
        ctx,
        migrations=_MIGRATIONS,
        repo_digest=_REPO_DIGEST,
        repo_components=_REPO_COMPONENTS,
    )
    dumped = json.dumps(report)
    assert _APP_ROLE not in dumped and _JOBS_READER not in dumped
    return report, _assert_read_only_session(connection)


def _postflight(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False, statement: str = "") -> None:
    def postflight(cur: Any, role: str) -> None:
        cur.execute(statement or "SELECT 'check:jobs_reader_grants'")
        if fail:
            raise RuntimeError(f"missing grants for role {role}")

    monkeypatch.setattr(lakebase_migration_plan, "postflight_jobs_reader_grants", postflight)


def test_without_the_jobs_reader_the_plan_reports_off_and_sends_no_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _postflight(monkeypatch)
    off, off_statements = _plan(monkeypatch, _Catalog(), _CTX)
    assert off["jobs_reader_grants"] == "off" and off["decision"] == "noop"
    assert "SELECT 'check:jobs_reader_grants'" not in off_statements


@pytest.mark.parametrize(("fail", "state"), ((False, "exact"), (True, "drift")))
def test_the_jobs_reader_grants_never_change_the_decision_or_blue_restorability(
    monkeypatch: pytest.MonkeyPatch, fail: bool, state: str
) -> None:
    _postflight(monkeypatch, fail=fail)
    report, statements = _plan(monkeypatch, _Catalog(), _CTX_JOBS)
    assert report["jobs_reader_grants"] == state
    assert (report["decision"], report["blue_restorable"], report["verify"]["passed"]) == (
        "noop",
        True,
        True,
    )
    # After every decision input, so a failed read can only add a re-grant.
    assert statements[-1] == "SELECT 'check:jobs_reader_grants'"


def test_an_absent_jobs_reader_role_reads_as_drift_with_the_real_postflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first roll-forward to an installed blue: the role does not exist yet.
    report, statements = _plan(monkeypatch, _Catalog(), _CTX_JOBS)
    assert report["jobs_reader_grants"] == "drift" and report["decision"] == "noop"
    assert statements[-1] == "SELECT rolname FROM pg_roles WHERE rolname = %s"


def test_the_jobs_reader_check_is_still_a_read_only_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _Catalog().route)
    _stub_checks(monkeypatch)
    _postflight(monkeypatch, statement="UPDATE mip_app.kpi_snapshots SET metric = 0")
    with pytest.raises(ReadOnlyStatementRefused):
        run_plan(
            {"host": "test"},
            _CTX_JOBS,
            migrations=_MIGRATIONS,
            repo_digest=_REPO_DIGEST,
            repo_components=_REPO_COMPONENTS,
        )


def _dispatch(capsys: pytest.CaptureFixture[str], jobs_reader_role: str) -> tuple[int, dict[str, Any]]:
    code = main_read_only(
        "plan",
        {"host": "test"},
        app_name=None,
        verifier_role=None,
        resolve_app_role_fn=lambda: _APP_ROLE,
        schema_inputs_fn=lambda: render_schema_inputs(
            ROOT, lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
        ),
        jobs_reader_role=jobs_reader_role,
    )
    output = capsys.readouterr().out
    assert _APP_ROLE not in output and _JOBS_READER not in output
    return code, json.loads(output)


def test_a_jobs_reader_that_is_the_app_role_refuses_before_any_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: pytest.fail("connected"))
    code, report = _dispatch(capsys, _APP_ROLE)
    assert (code, report["decision"], report["refusals"]) == (2, "refuse", ["runtime_roles"])


def test_the_facade_passes_the_jobs_reader_into_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def read_only(mode: str, _conn: dict[str, Any], **kwargs: Any) -> int:
        seen.update(kwargs, mode=mode)
        return 0

    monkeypatch.setattr(lakebase_migrate, "_main_read_only", read_only)
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda **_kwargs: {"host": "test"})
    with pytest.raises(SystemExit) as exit_info:
        lakebase_migrate.main(
            lakebase_instance="instance",
            lakebase_database="db",
            mode="plan",
            jobs_reader_client_id=_JOBS_READER,
        )
    assert exit_info.value.code == 0
    assert (seen["mode"], seen["jobs_reader_role"]) == ("plan", _JOBS_READER)


def test_the_static_walk_reaches_the_jobs_reader_postflight_and_finds_only_reads() -> None:
    reached, _statements, non_literal = _reachable_statements(
        ("jobs.lakebase_migration_plan", "main_read_only")
    )
    assert {"postflight_jobs_reader_grants", "resolve_jobs_reader_role"} <= reached
    assert non_literal == 0
