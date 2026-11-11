"""A config-only client's NEW Lakebase instance passes the migrate and grants gates (spike S3 answer 6.8, spike S2b).

Spike S2b read every database on two Provisioned instances created in
2026-11 (PostgreSQL 16.15; tests/fixtures/lakebase_fresh_instance_s2b.json is
its capture, metadata only). Against the contract pinned from client #1's
install, all four were refused twice:

* two managed event-trigger bodies are a newer provider revision
  (``on_create_sequence`` 754 bytes, ``on_create_table_or_view`` 1991 bytes);
* the ``public`` schema ACL has no database-writer rows, which the
  public-schema gate required even in its preflight. (PUBLIC's USAGE, the
  other fresh-database row, was already accepted by the preflight and revoked
  by the closure the grants step and the OAuth-role converge run.)

The contract now accepts exactly the reviewed revisions, each in full, and
nothing looser; and a database that met the old contract plans exactly as
before (no new WR3, no changed migration digest).
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from jobs import lakebase_migration_contracts as contracts
from jobs import lakebase_migration_plan as plan_module
from jobs.lakebase_migration_inputs import (
    grant_matrix_document,
    schema_input_components,
    schema_input_digest,
)
from jobs.lakebase_migration_plan import CheckContext, run_plan
from jobs.lakebase_migration_provider_plane import (
    _close_public_schema_boundary,
    _postflight_public_schema_boundary,
)
from jobs.lakebase_migration_schema_hooks import _postflight_event_trigger_inventory
from tests.unit.test_lakebase_migration_plan import (
    _CTX,
    _MIGRATIONS,
    _REPO_COMPONENTS,
    _REPO_DIGEST,
    _TEXTS,
    _assert_read_only_session,
    _Catalog,
    _install,
)

REPO = Path(__file__).resolve().parents[2]
CAPTURE = json.loads((REPO / "tests" / "fixtures" / "lakebase_fresh_instance_s2b.json").read_text(encoding="utf-8"))
DRIFTED = ("on_create_sequence", "on_create_table_or_view")
DATABASE_OID = 24579
WRITER = f"databricks_writer_{DATABASE_OID}"
CLOSURE = ["SET LOCAL ROLE pg_database_owner", "REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC", "RESET ROLE"]
INSTALL_ACL = sorted(
    [
        ("pg_database_owner", "CREATE", False, "pg_database_owner"),
        ("pg_database_owner", "USAGE", False, "pg_database_owner"),
        ("databricks_superuser", "CREATE", True, "pg_database_owner"),
        ("databricks_superuser", "USAGE", True, "pg_database_owner"),
        (WRITER, "CREATE", False, "pg_database_owner"),
        (WRITER, "USAGE", False, "pg_database_owner"),
    ]
)
FRESH_ACL = sorted(tuple(row) for row in CAPTURE["public_schema_acl_pristine"])
PUBLIC_ROW = ("PUBLIC", "USAGE", False, "pg_database_owner")


def _captured_rows() -> list[tuple[Any, ...]]:
    """The fresh capture in the gate's 22-column SELECT shape."""

    return [
        (
            row["evtname"],
            row["evtevent"],
            row["evtenabled"],
            row["evttags"],
            row["event_owner"],
            row["function_schema"],
            row["function_name"],
            row["function_arguments"],
            row["function_kind"],
            row["function_return_type"],
            row["function_security_definer"],
            row["function_owner"],
            row["function_language"],
            row["function_volatility"],
            row["function_parallel_safety"],
            row["function_leakproof"],
            row["function_strict"],
            row["function_config"],
            row["function_binary"],
            row["function_acl"],
            row["function_source_sha256"],
            row["function_source_bytes"],
        )
        for row in CAPTURE["event_triggers"]
    ]


def _variant_rows(rows: dict[str, contracts._ManagedEventTriggerContractRow]) -> list[tuple[Any, ...]]:
    return [
        (name, *contract[:3], *contract[3:18], None, *contract[18:])
        for name, contract in sorted(rows.items())
    ]


def _with(rows: list[tuple[Any, ...]], name: str, **fields: Any) -> list[tuple[Any, ...]]:
    index = {"sha256": 20, "bytes": 21}
    changed = []
    for row in rows:
        values = list(row)
        if row[0] == name:
            for field, value in fields.items():
                values[index[field]] = value
        changed.append(tuple(values))
    return changed


class _Cursor:
    """Answers the provider-plane reads by their text; records every other statement."""

    def __init__(self, route: Callable[[str, Any], list[tuple[Any, ...]] | None]) -> None:
        self._route = route
        self.writes: list[str] = []
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, statement: object, params: Any = None) -> None:
        rows = self._route(str(statement), params)
        if rows is None:
            self.writes.append(str(statement))
            rows = []
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Database:
    """A database's managed triggers and public-schema ACL; PUBLIC's row goes only under pg_database_owner, as live."""

    def __init__(self, triggers: list[tuple[Any, ...]], acl: list[tuple[str, str, bool, str]]) -> None:
        self.triggers = triggers
        self.acl = set(acl)
        self.role: str | None = None

    def route(self, text: str, params: Any) -> list[tuple[Any, ...]] | None:
        if "FROM pg_event_trigger event_trigger" in text:
            return list(self.triggers)
        if "CROSS JOIN pg_database database_object" in text and "namespace.nspname = 'public'" in text:
            return [("public", "pg_database_owner", DATABASE_OID)]
        if "aclexplode(namespace.nspacl)" in text and "namespace.nspname = 'public'" in text:
            return sorted(self.acl)
        if "has_schema_privilege(target.role_name" in text:
            return [(role, "USAGE") for role in params[0]] if PUBLIC_ROW in self.acl else []
        if any(marker in text for marker in ("aclexplode(routine.proacl)", "provider_relation", "FROM pg_default_acl default_acl")):
            return []
        if "JOIN pg_roles database_owner ON database_owner.oid = database_object.datdba" in text:
            return [("deployer-PLACEHOLDER", "deployer-PLACEHOLDER", True)]
        if text == "SET LOCAL ROLE pg_database_owner":
            self.role = "pg_database_owner"
        elif text == "RESET ROLE":
            self.role = None
        elif text == "REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC" and self.role == "pg_database_owner":
            self.acl.discard(PUBLIC_ROW)
        return None


def _gate(triggers: list[tuple[Any, ...]]) -> None:
    _postflight_event_trigger_inventory(_Cursor(_Database(triggers, INSTALL_ACL).route), "app-role", principal_label="schema preflight")


# -- the managed event triggers -------------------------------------------------


def test_the_fresh_capture_is_exactly_the_reviewed_2026_11_revision() -> None:
    fresh = next(variant for variant in contracts._MANAGED_EVENT_TRIGGER_VARIANTS if variant.revision == "fresh-2026-11")
    captured = {row[0]: row for row in _captured_rows()}
    assert sorted(captured) == sorted(fresh.rows)
    for name, contract in fresh.rows.items():
        row = captured[name]
        assert (row[1], row[2], tuple(sorted(row[3])), *row[4:19], row[20], row[21]) == tuple(contract), name
        assert row[19] in contracts._MANAGED_EVENT_TRIGGER_FUNCTION_ACLS
    assert "16.15" in fresh.seen_on and CAPTURE["server_version_num"] == 160015


def test_the_two_revisions_differ_only_in_the_two_drifted_bodies() -> None:
    install, fresh = (variant.rows for variant in contracts._MANAGED_EVENT_TRIGGER_VARIANTS)
    assert install is contracts._MANAGED_EVENT_TRIGGER_CONTRACT
    for name in install:
        differing = {field for field in install[name]._fields if getattr(install[name], field) != getattr(fresh[name], field)}
        assert differing == ({"function_source_sha256", "function_source_bytes"} if name in DRIFTED else set()), name


@pytest.mark.parametrize("revision", ["install", "fresh-capture"])
def test_both_reviewed_revisions_pass_the_event_trigger_gate(revision: str) -> None:
    _gate(_variant_rows(contracts._MANAGED_EVENT_TRIGGER_CONTRACT) if revision == "install" else _captured_rows())


@pytest.mark.parametrize("name", DRIFTED)
@pytest.mark.parametrize("change", ["substituted_byte", "added_byte"])
def test_a_body_one_byte_off_either_revision_is_refused(name: str, change: str) -> None:
    for rows in (_captured_rows(), _variant_rows(contracts._MANAGED_EVENT_TRIGGER_CONTRACT)):
        size = next(row[21] for row in rows if row[0] == name)
        # A one-byte edit: the same length with another digest, or one more byte.
        other = hashlib.sha256(b"x" * size).hexdigest()
        drifted = _with(rows, name, sha256=other) if change == "substituted_byte" else _with(rows, name, bytes=size + 1)
        with pytest.raises(RuntimeError, match=rf"drifted=\['{name}'\]"):
            _gate(drifted)


def test_rows_mixing_the_two_revisions_are_refused() -> None:
    install = {row[0]: row for row in _variant_rows(contracts._MANAGED_EVENT_TRIGGER_CONTRACT)}
    fresh = {row[0]: row for row in _captured_rows()}
    mixed = [install["on_create_schema"], install["on_create_sequence"], fresh["on_create_table_or_view"]]
    with pytest.raises(RuntimeError, match=r"drifted=\[\], mixed_revisions=True"):
        _gate(mixed)


# -- the public schema ------------------------------------------------------------


def _close(acl: list[tuple[str, str, bool, str]]) -> tuple[_Database, list[str]]:
    database = _Database(_captured_rows(), acl)
    cursor = _Cursor(database.route)
    _close_public_schema_boundary(cursor, ("app-role",), principal_label="ACL public-schema cutover")
    return database, cursor.writes


def test_a_fresh_database_has_public_usage_revoked_and_passes() -> None:
    assert PUBLIC_ROW in FRESH_ACL and not any(row[0].startswith("databricks_writer_") for row in FRESH_ACL)
    database, writes = _close(FRESH_ACL)
    assert writes == CLOSURE
    assert database.acl == set(FRESH_ACL) - {PUBLIC_ROW}
    _postflight_public_schema_boundary(
        _Cursor(database.route), ("app-role",), principal_label="schema preflight", allow_legacy_public_usage=False
    )


def test_a_compliant_database_runs_exactly_the_closure_it_ran_before() -> None:
    """Nothing new on the install: the same three statements, a REVOKE PostgreSQL finds nothing to remove for."""

    database, writes = _close(INSTALL_ACL)
    assert writes == CLOSURE and database.acl == set(INSTALL_ACL)


@pytest.mark.parametrize(
    "acl",
    [
        [*FRESH_ACL, (WRITER, "CREATE", False, "pg_database_owner")],
        [row for row in FRESH_ACL if row[0] != "databricks_superuser" or row[1] != "CREATE"],
        [*FRESH_ACL, ("app-role", "USAGE", False, "pg_database_owner")],
        [*INSTALL_ACL, ("PUBLIC", "CREATE", False, "pg_database_owner")],
    ],
    ids=("half_a_writer_grant", "missing_superuser_create", "runtime_role_usage", "public_create"),
)
def test_a_public_acl_outside_both_revisions_is_refused(acl: list[tuple[str, str, bool, str]]) -> None:
    with pytest.raises(RuntimeError, match="public-schema ACL mismatch"):
        _close(acl)


# -- a database that met the old contract plans exactly as before ------------------


def _plan_with_real_provider_gates(monkeypatch: pytest.MonkeyPatch, database: _Database) -> dict[str, Any]:
    checks = (
        ("public_schema_boundary", plan_module._public_schema_boundary),
        ("event_trigger_inventory", plan_module._event_trigger_inventory),
    )
    monkeypatch.setattr(plan_module, "APPLY_PREFLIGHT_CHECKS", checks)
    monkeypatch.setattr(plan_module, "VERIFY_CHECKS", checks)
    catalog = _Catalog()

    def route(text: str, params: Any) -> list[tuple[Any, ...]] | None:
        rows = database.route(text, params)
        return catalog.route(text, params) if rows is None else rows

    connection = _install(monkeypatch, route)
    report = run_plan(
        {"host": "test"},
        CheckContext(app_role=_CTX.app_role, verifier_role=None),
        migrations=_MIGRATIONS,
        repo_digest=_REPO_DIGEST,
        repo_components=_REPO_COMPONENTS,
    )
    _assert_read_only_session(connection)
    return report


@pytest.mark.parametrize(
    ("triggers", "acl"),
    [
        (_variant_rows(contracts._MANAGED_EVENT_TRIGGER_CONTRACT), INSTALL_ACL),
        (_captured_rows(), sorted(set(FRESH_ACL) - {PUBLIC_ROW})),
    ],
    ids=("client_1_install", "fresh_instance_after_its_first_migration"),
)
def test_a_compliant_database_plans_noop_with_the_real_provider_gates(
    monkeypatch: pytest.MonkeyPatch, triggers: list[tuple[Any, ...]], acl: list[tuple[str, str, bool, str]]
) -> None:
    """No new window reason: the decision is noop, so the release plans no WR3."""

    report = _plan_with_real_provider_gates(monkeypatch, _Database(triggers, acl))
    assert (report["decision"], report["refusals"], report["digest"], report["catalog"]) == ("noop", [], "equal", "equal")
    assert report["verify"]["checks"] == ["public_schema_boundary", "event_trigger_inventory"]


def test_the_real_provider_gates_still_refuse_a_drifted_body_in_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: the gates above really ran."""

    drifted = _with(_captured_rows(), "on_create_sequence", bytes=755)
    report = _plan_with_real_provider_gates(monkeypatch, _Database(drifted, INSTALL_ACL))
    assert report["decision"] == "refuse" and report["refusals"] == ["apply_preflight:event_trigger_inventory"]


def test_the_migration_digest_reads_no_provider_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The schema-input digest and its components are the SQL texts and the grant matrix only."""

    source = (REPO / "jobs" / "lakebase_migration_inputs.py").read_text(encoding="utf-8")
    imported = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "jobs.lakebase_migration_contracts"
        for alias in node.names
    }
    assert imported and all(name.startswith(("_APP_ROLE_", "_AI_GATEWAY_")) for name in imported)
    before = (schema_input_digest(_TEXTS), schema_input_components(_TEXTS), grant_matrix_document())
    monkeypatch.setattr(contracts, "_MANAGED_EVENT_TRIGGER_VARIANTS", contracts._MANAGED_EVENT_TRIGGER_VARIANTS[:1])
    monkeypatch.setattr(contracts, "_public_schema_acl_variants", lambda writer: ())
    assert (schema_input_digest(_TEXTS), schema_input_components(_TEXTS), grant_matrix_document()) == before
