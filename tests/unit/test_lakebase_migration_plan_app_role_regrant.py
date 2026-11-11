"""``--mode plan`` reports the App role's CREATE ON DATABASE apart from the decision (spike S2).

Spike S2 measured that every App update that carries resource bindings, an
identical array included, grants CREATE ON DATABASE to the App's Lakebase role
again (``=c`` -> ``=Cc``) with no restart, and that only the migration job's
revoke takes it back. The plan therefore checks the App role's matrix with
that one privilege set apart and reports it as ``app_role_regrant``: a
re-grant alone keeps the decision ``noop`` and blue restorable, and the
quiesce window plan reads ``owed`` as a WR2 window whose migration job runs
the revoke. ``--mode verify`` and apply's postflight still refuse it.
"""

from __future__ import annotations

from typing import Any

import pytest

from jobs import lakebase_migration_plan
from jobs.lakebase_migration_plan import (
    VERIFY_CHECKS,
    _app_role_grants,
    _app_role_grants_but_regrant,
    plan_verify_checks,
)
from jobs.lakebase_migration_postflight import _postflight_app_role_grants
from tests.unit.test_lakebase_migration_plan import _Catalog, _plan


@pytest.fixture(autouse=True)
def _default_to_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABRICKS_BUNDLE_TARGET", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)


@pytest.mark.parametrize(("create", "state"), ((False, "exact"), (True, "owed")))
def test_a_re_grant_alone_is_reported_apart_and_never_turns_noop_into_apply(
    monkeypatch: pytest.MonkeyPatch, create: bool, state: str
) -> None:
    report, statements = _plan(monkeypatch, _Catalog(database_create=create))

    assert report["app_role_regrant"] == state
    assert (report["decision"], report["blue_restorable"]) == ("noop", True)
    assert report["verify"]["passed"] is True
    assert statements[-1] == lakebase_migration_plan._APP_ROLE_DATABASE_CREATE_SQL


def test_an_unreadable_answer_reads_as_owed_so_the_window_runs_the_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Unreadable(_Catalog):
        def route(self, text: str, params: Any) -> Any:
            if text == lakebase_migration_plan._APP_ROLE_DATABASE_CREATE_SQL:
                raise RuntimeError("current transaction is aborted")
            return super().route(text, params)

    report, _statements = _plan(monkeypatch, _Unreadable())

    assert report["app_role_regrant"] == "owed"


def test_a_plan_refused_at_the_runtime_roles_reads_nothing_more_and_the_window_records_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.databricks.quiesce_window_facts import _migration

    report, statements = _plan(monkeypatch, _Catalog(roles=set()))

    assert (report["decision"], report["app_role_regrant"]) == ("refuse", "unchecked")
    assert lakebase_migration_plan._APP_ROLE_DATABASE_CREATE_SQL not in statements
    assert _migration(report)["app_role_regrant"] == "unchecked"
    with pytest.raises(ValueError, match="CREATE ON DATABASE"):
        _migration({**report, "decision": "noop", "jobs_reader_grants": "exact"})


def test_any_other_drift_still_needs_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    report, _statements = _plan(monkeypatch, _Catalog(database_create=True), verify_fail="app_role_grants")

    assert report["decision"] == "apply"
    assert report["verify"]["failed_check"] == "app_role_grants"
    assert report["app_role_regrant"] == "owed"


def test_only_the_plans_verify_sets_the_privilege_apart() -> None:
    plan_checks = dict(plan_verify_checks())
    verify_checks = dict(VERIFY_CHECKS)

    assert list(plan_checks) == list(verify_checks), "the same checks, in the same order"
    assert verify_checks["app_role_grants"] is _app_role_grants, "--mode verify stays strict"
    assert plan_checks["app_role_grants"] is _app_role_grants_but_regrant
    assert {name for name in plan_checks if plan_checks[name] is not verify_checks[name]} == {
        "app_role_grants"
    }


class _Stop(Exception):
    """Raised by the cursor once the database/schema check has passed."""


class _PostflightCursor:
    """Answers the role row and the database/schema privileges, then stops the postflight."""

    def __init__(self, role: str, *, create: bool, temporary: bool = False) -> None:
        self.role = role
        self.answers = [
            [(role,)],
            [(True, create, temporary, True, False)],
        ]
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, _statement: str, _params: Any = None) -> None:
        if not self.answers:
            raise _Stop
        self.rows = self.answers.pop(0)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self.rows)

    def fetchone(self) -> tuple[Any, ...]:
        return self.rows[0]


def test_the_postflight_refuses_the_privilege_unless_the_plan_sets_it_apart() -> None:
    role = "app-role-PLACEHOLDER"

    with pytest.raises(RuntimeError, match="database_create=True"):
        _postflight_app_role_grants(_PostflightCursor(role, create=True), role)
    with pytest.raises(_Stop):
        _postflight_app_role_grants(_PostflightCursor(role, create=True), role, allow_database_create=True)
    # The allowance is CREATE ON DATABASE alone: TEMPORARY (never re-granted, S2) still refuses.
    with pytest.raises(RuntimeError, match="database_temporary=True"):
        _postflight_app_role_grants(
            _PostflightCursor(role, create=True, temporary=True), role, allow_database_create=True
        )
