"""The optional Lakebase jobs_reader role: off is byte-identical, on is exactly two SELECTs (M2-PR5).

* Unset, the grant reconciliation's statement log (every statement and its
  parameters, through tests/unit/test_lakebase_migrate_grants.py's fake
  cursor) hashes to the digest written from origin/main 3d37e347 before the
  role existed: the M0-PR6 apply artifact-hash proof for today's matrix. Both
  sides ran the same fake; regenerate only for a reviewed change:

      PYTHONPATH=. python tests/unit/test_lakebase_jobs_reader.py --print

* Set, the same transaction revokes everything from the role, then grants
  exactly USAGE on mip_app and SELECT on approvals and call_dispositions, and
  the postflight rejects any other privilege.
* The role must be real and distinct from the App, verifier and executor roles.
* The bundle enables it for the jobs principal, and the OAuth role converger
  knows its contract.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs import lakebase_migrate  # noqa: E402
from jobs import lakebase_migration_jobs_reader as jobs_reader  # noqa: E402
from tests.unit.test_lakebase_migrate_grants import (  # noqa: E402
    _Connection,
    _Cursor,
    _safe_role_security_row,
    _successful_cursor,
    _table_rows,
    _trigger_inventory_rows,
)
from tools.databricks import converge_lakebase_oauth_role  # noqa: E402

APP = "00000000-0000-4000-8000-0000000000aa"
VERIFIER = "00000000-0000-4000-8000-0000000000bb"
JOBS = "00000000-0000-4000-8000-000000000002"
EXECUTOR = "00000000-0000-4000-8000-000000000001"
# sha256 of the unset statement log, written from origin/main 3d37e347.
UNSET_LOG_SHA256 = {
    "app": "44ee65df24bde5ea9f8076969db196987ff5a6c367621360ff8d8d296e9bd334",
    "app+verifier": "3a7f920133c5a05f6c508613feaff0d2481ef792d052c3dede9364c8167a1a3d",
}


def _log_digest(cursor: _Cursor) -> str:
    text = json.dumps([[statement, repr(params)] for statement, params in cursor.executed])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _apply(monkeypatch: pytest.MonkeyPatch, cursor: _Cursor, *, verifier: str | None, **kwargs: Any) -> _Connection:
    connection = _Connection(cursor)
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: APP)
    monkeypatch.setattr(lakebase_migrate, "_resolve_ai_gateway_verifier_role", lambda *a, **k: verifier)
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: connection)
    lakebase_migrate._apply_app_role_grants(
        {},
        ai_gateway_verifier_client_id=verifier,
        role_wait_timeout_s=0,
        role_wait_interval_s=1,
        allow_absent_managed_event_triggers=True,
        allow_absent_provider_schema=True,
        **kwargs,
    )
    return connection


def unset_digests(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    digests = {}
    for label, verifier in (("app", None), ("app+verifier", VERIFIER)):
        cursor = _successful_cursor(APP, verifier)
        _apply(monkeypatch, cursor, verifier=verifier)
        digests[label] = _log_digest(cursor)
    return digests


def test_unset_the_statement_log_is_the_pre_jobs_reader_log(monkeypatch: pytest.MonkeyPatch) -> None:
    assert unset_digests(monkeypatch) == UNSET_LOG_SHA256


def _jobs_reader_cursor() -> _Cursor:
    """The App+verifier fake, plus the jobs reader's role wait, executor read and postflight."""

    cursor = _successful_cursor(APP, VERIFIER)
    results = cursor._fetchall_results
    results.insert(2, [(JOBS,)])  # its pg_roles wait, after the App's and the verifier's
    postflight = [
        [(JOBS,)],
        [("mip_app", "USAGE")],
        _table_rows(),
        [("mip_app", "approvals", "SELECT"), ("mip_app", "call_dispositions", "SELECT")],
        [],
        [],
        [],
        [],
        [_safe_role_security_row(JOBS)],
        [],
        [],
        [],
        _trigger_inventory_rows(),
    ]
    # Before the final ACL event-trigger postflight, after the verifier's postflight.
    results[-1:-1] = postflight
    cursor._fetchone_results.insert(0, (EXECUTOR,))
    cursor._fetchone_results.append((True, False, False, True, False))
    return cursor


def test_set_it_grants_exactly_usage_and_two_selects(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _jobs_reader_cursor()
    connection = _apply(monkeypatch, cursor, verifier=VERIFIER, jobs_reader_role=JOBS)

    assert connection.rollback_count == 0 and connection.commit_count == 2
    statements = [statement for statement, _params in cursor.executed]
    quoted = f'"{JOBS}"'
    grants = [s for s in statements if s.startswith("GRANT ") and s.endswith(f"TO {quoted}")]
    assert grants == [
        f'GRANT USAGE ON SCHEMA "mip_app" TO {quoted}',
        f'GRANT SELECT ON TABLE "mip_app"."approvals" TO {quoted}',
        f'GRANT SELECT ON TABLE "mip_app"."call_dispositions" TO {quoted}',
    ]
    revokes = [s for s in statements if s.startswith("REVOKE ") and s.endswith(f"FROM {quoted}")]
    assert f'REVOKE CREATE ON DATABASE "mip_app_state" FROM {quoted}' in revokes
    assert f'REVOKE ALL PRIVILEGES ON TABLE "mip_app"."approvals" FROM {quoted}' in revokes
    assert max(statements.index(s) for s in revokes) < statements.index(grants[0])
    assert ("SELECT rolname FROM pg_roles WHERE rolname = %s", (JOBS,)) in cursor.executed
    assert "SELECT current_user" in statements


class _PrivilegeCursor:
    """Answers the jobs reader postflight by statement, with chosen table privileges."""

    def __init__(self, table_privileges: list[tuple[str, str, str]], sequence_privileges: list[Any]) -> None:
        self.table_privileges, self.sequence_privileges = table_privileges, sequence_privileges
        self._last = ""

    def execute(self, statement: str, params: Any = None) -> None:
        self._last = statement

    def fetchone(self) -> tuple[Any, ...]:
        return (True, False, False, True, False)

    def fetchall(self) -> list[Any]:
        if "has_table_privilege" in self._last and "mip_app' AND c.relname" not in self._last:
            return self.table_privileges
        if "has_sequence_privilege" in self._last:
            return self.sequence_privileges
        if "FROM pg_roles WHERE rolname" in self._last:
            return [(JOBS,)]
        if "__non_base_relation__" in self._last:
            return []  # the table inventory; its mismatch check is patched out
        raise AssertionError("not reached: the privilege checks refuse first")


@pytest.mark.parametrize(
    "table_privileges",
    [
        [("mip_app", "approvals", "SELECT"), ("mip_app", "approvals", "INSERT"), ("mip_app", "call_dispositions", "SELECT")],
        [("mip_app", "approvals", "SELECT"), ("mip_app", "call_dispositions", "SELECT"), ("mip_app", "lead_snoozes", "SELECT")],
        [("mip_app", "approvals", "SELECT"), ("mip_app", "call_dispositions", "SELECT"), ("other", "t", "SELECT")],
        [("mip_app", "approvals", "SELECT")],
    ],
    ids=["insert", "another-table", "another-schema", "missing"],
)
def test_the_postflight_rejects_any_other_table_privilege(
    monkeypatch: pytest.MonkeyPatch, table_privileges: list[tuple[str, str, str]]
) -> None:
    monkeypatch.setattr(jobs_reader, "_postflight_effective_schema_privileges", lambda *a, **k: None)
    monkeypatch.setattr(jobs_reader, "_raise_object_inventory_mismatch", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="jobs reader table privilege postflight failed"):
        jobs_reader.postflight_jobs_reader_grants(_PrivilegeCursor(table_privileges, []), JOBS)


def test_the_postflight_rejects_a_sequence_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("_postflight_effective_schema_privileges", "_raise_object_inventory_mismatch",
                 "_postflight_direct_column_privileges"):
        monkeypatch.setattr(jobs_reader, name, lambda *a, **k: None)
    exact = [("mip_app", "approvals", "SELECT"), ("mip_app", "call_dispositions", "SELECT")]
    with pytest.raises(RuntimeError, match="forbidden sequence privileges"):
        jobs_reader.postflight_jobs_reader_grants(
            _PrivilegeCursor(exact, [("mip_app", "s", "USAGE")]), JOBS
        )


@pytest.mark.parametrize(
    ("value", "distinct_from", "message"),
    [
        ("00000000PLACEHOLDER", {}, "placeholder"),
        ("<jobs-client-id>", {}, "placeholder"),
        (APP, {"app": APP}, "distinct from the app role"),
        (VERIFIER.upper(), {"AI Gateway verifier": VERIFIER}, "distinct from the AI Gateway verifier role"),
        (EXECUTOR, {"migration executor": EXECUTOR}, "distinct from the migration executor role"),
    ],
)
def test_the_role_must_be_real_and_distinct(value: str, distinct_from: dict[str, str], message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        jobs_reader.resolve_jobs_reader_role(value, distinct_from=distinct_from)


def test_unset_or_blank_keeps_the_role_off() -> None:
    for value in (None, "", "  "):
        assert jobs_reader.resolve_jobs_reader_role(value, distinct_from={"app": APP}) is None


def test_the_bundle_enables_it_for_the_jobs_principal_and_the_converger_knows_it() -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    task = bundle["resources"]["jobs"]["mip_lakebase_migrate"]["tasks"][0]["spark_python_task"]
    assert "--jobs-reader-client-id=${var.jobs_principal}" in task["parameters"]
    assert lakebase_migrate.build_parser().parse_args([]).jobs_reader_client_id == ""
    assert converge_lakebase_oauth_role._reviewed_acl_objects("jobs_reader") == (
        {"approvals", "call_dispositions"},
        set(),
        set(),
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["--print"]:
        raise SystemExit("usage: PYTHONPATH=. python tests/unit/test_lakebase_jobs_reader.py --print")
    patch = pytest.MonkeyPatch()
    try:
        print(json.dumps(unset_digests(patch), indent=2))
    finally:
        patch.undo()
