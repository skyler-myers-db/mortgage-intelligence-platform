"""Read-only ``--mode plan`` / ``--mode verify``: decisions, executor probe, and the no-write proof.

Every database interaction goes through a fake psycopg connection whose cursor
records each statement and refuses anything that is not a SELECT, SHOW or
read-only WITH (its own classifier, independent of the production guard), and
whose connection must already be ``read_only`` when the first statement
arrives. A static walk then covers the branches the fakes do not take.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jobs import lakebase_migrate, lakebase_migration_plan
from jobs.lakebase_migration_contracts import (
    _APP_ROLE_ROUTINE_PRIVILEGES,
    _APP_ROLE_SEQUENCE_PRIVILEGES,
    _APP_ROLE_TABLE_PRIVILEGES,
    _APP_TRIGGER_CONTRACT,
)
from jobs.lakebase_migration_inputs import (
    SchemaMigration,
    parse_schema_migrations,
    render_schema_inputs,
    schema_input_digest,
)
from jobs.lakebase_migration_plan import CheckContext, main_read_only, run_plan, run_verify
from jobs.lakebase_migration_read_only import (
    ReadOnlyStatementRefused,
    assert_read_statement,
    read_only_session,
)

ROOT = Path(__file__).resolve().parents[2]
# A stand-in for a service-principal client id: it must never reach a report.
_APP_ROLE = "app-role-client-id-0001"
_CTX = CheckContext(app_role=_APP_ROLE, verifier_role=None)
_MIGRATIONS = (
    SchemaMigration(version="2026_01_01_base", compat="contract", tagged=False),
    SchemaMigration(version="2026_01_02_added_column", compat="expand", tagged=True),
)
_DECLARED = {migration.version for migration in _MIGRATIONS}
_REPO_DIGEST = schema_input_digest(("pre", "seed", "tenant", "post"))


@pytest.fixture(autouse=True)
def _default_to_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("DATABRICKS_BUNDLE_TARGET", raising=False)
    monkeypatch.delenv("MIP_AI_GATEWAY_VERIFIER_CLIENT_ID", raising=False)


# -- fake psycopg -----------------------------------------------------------

_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_DML_RE = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE|INTO)\b", re.IGNORECASE)

Rows = list[tuple[Any, ...]]
Router = Callable[[str, Any], "Rows | None"]


def _is_read(statement: str) -> bool:
    code = _LITERAL_RE.sub("''", _LINE_COMMENT_RE.sub(" ", statement)).strip()
    code = code.removesuffix(";").strip()
    if not code or ";" in code:
        return False
    lead = code.split(None, 1)[0].upper()
    return lead in {"SELECT", "SHOW", "WITH"} and not _DML_RE.search(code)


class _FakeCursor:
    def __init__(self, connection: _FakeConnection, router: Router) -> None:
        self._connection = connection
        self._router = router
        self.executed: list[tuple[str, Any]] = []
        self._rows: Rows = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: object, params: Any = None, *, prepare: bool | None = None) -> None:
        text = str(statement)
        if self._connection.read_only is not True:
            raise AssertionError("a statement arrived before the connection was read-only")
        if prepare is not True:
            raise AssertionError("a read-only statement must be sent as a prepared statement")
        if not _is_read(text):
            raise AssertionError(f"fake psycopg refused a non-read statement: {text[:80]!r}")
        self.executed.append((text, params))
        rows = self._router(text, params)
        if rows is None:
            raise AssertionError(f"unrouted read statement: {text.strip()[:80]!r}")
        self._rows = rows

    def fetchall(self) -> Rows:
        return list(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, router: Router) -> None:
        self.read_only: bool | None = None
        self.cursor_object = _FakeCursor(self, router)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_object

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, router: Router) -> _FakeConnection:
    import psycopg

    connection = _FakeConnection(router)

    def _connect(**kwargs: Any) -> _FakeConnection:
        assert kwargs.get("autocommit") is False
        return connection

    monkeypatch.setattr(psycopg, "connect", _connect)
    return connection


def _assert_read_only_session(connection: _FakeConnection) -> list[str]:
    statements = [text for text, _params in connection.cursor_object.executed]
    assert statements[0] == "SHOW transaction_read_only"
    assert all(_is_read(statement) for statement in statements)
    assert connection.read_only is True
    assert (connection.commits, connection.rollbacks, connection.closed) == (0, 1, True)
    return statements


# -- a catalog the plan reads ----------------------------------------------


@dataclass
class _Catalog:
    applied: set[str] | None = field(default_factory=lambda: set(_DECLARED))
    digest: str | None = _REPO_DIGEST
    authority: tuple[bool, ...] = (True, True, True, True, True)
    unowned: Rows = field(default_factory=list)
    roles: set[str] = field(default_factory=lambda: {_APP_ROLE})

    def route(self, text: str, params: Any) -> Rows | None:
        if text == "SHOW transaction_read_only":
            return [("on",)]
        if text.startswith("SELECT 'check:"):
            return [(1,)]
        if "FROM pg_roles WHERE rolname = %s" in text:
            return [(params[0],)] if params[0] in self.roles else []
        if "pg_has_role(current_user, database_object.datdba, 'USAGE')" in text:
            return [self.authority]
        if "SELECT mip_object.object_kind, mip_object.object_name" in text:
            return list(self.unowned)
        if "to_regclass('mip_app.schema_migrations')" in text:
            present = self.applied is not None
            return [(present, present)]
        if text == "SELECT version FROM mip_app.schema_migrations ORDER BY version":
            return [(version,) for version in sorted(self.applied or ())]
        if "FROM mip_app.deployment_schema_digest WHERE scope = %s" in text:
            assert params == ("lakebase_schema",)
            return [] if self.digest is None else [(self.digest,)]
        return None


def _stub(name: str, *, fail: bool = False, statement: str | None = None) -> tuple[str, Any]:
    def check(cur: Any, ctx: CheckContext) -> None:
        cur.execute(statement or f"SELECT 'check:{name}'")
        if fail:
            raise RuntimeError(f"drift for role {ctx.app_role}")

    return name, check


def _stub_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    preflight_fail: str | None = None,
    verify_fail: str | None = None,
) -> None:
    preflight = ("public_schema_boundary", "trigger_inventory")
    verify = ("trigger_contract", "app_role_grants")
    monkeypatch.setattr(
        lakebase_migration_plan,
        "APPLY_PREFLIGHT_CHECKS",
        tuple(_stub(name, fail=name == preflight_fail) for name in preflight),
    )
    monkeypatch.setattr(
        lakebase_migration_plan,
        "VERIFY_CHECKS",
        tuple(_stub(name, fail=name == verify_fail) for name in verify),
    )


def _plan(
    monkeypatch: pytest.MonkeyPatch,
    catalog: _Catalog,
    **checks: str,
) -> tuple[dict[str, Any], list[str]]:
    connection = _install(monkeypatch, catalog.route)
    _stub_checks(monkeypatch, **checks)
    report = run_plan({"host": "test"}, _CTX, migrations=_MIGRATIONS, repo_digest=_REPO_DIGEST)
    assert _APP_ROLE not in json.dumps(report)
    return report, _assert_read_only_session(connection)


# -- plan decisions ---------------------------------------------------------


def test_plan_is_noop_when_nothing_is_pending_the_digest_matches_and_verify_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, statements = _plan(monkeypatch, _Catalog())

    assert report["decision"] == "noop"
    assert report["blue_restorable"] is True
    assert report["refusals"] == []
    assert report["digest"] == "equal"
    assert report["migrations"]["pending"] == []
    assert report["verify"] == {
        "checks": ["trigger_contract", "app_role_grants"],
        "failed_check": None,
        "passed": True,
    }
    assert "SELECT 'check:trigger_inventory'" in statements


def test_a_pending_expand_version_applies_and_keeps_blue_restorable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, _statements = _plan(monkeypatch, _Catalog(applied={"2026_01_01_base"}))

    assert report["decision"] == "apply"
    assert report["migrations"]["pending"] == ["2026_01_02_added_column"]
    assert report["migrations"]["pending_contract"] == []
    assert report["blue_restorable"] is True


def test_a_pending_untagged_version_counts_as_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    report, _statements = _plan(monkeypatch, _Catalog(applied={"2026_01_02_added_column"}))

    assert report["decision"] == "apply"
    assert report["migrations"]["pending_contract"] == ["2026_01_01_base"]
    assert report["blue_restorable"] is False


@pytest.mark.parametrize(("digest", "state"), ((None, "absent"), ("0" * 64, "differs")))
def test_an_absent_or_different_digest_needs_apply(
    monkeypatch: pytest.MonkeyPatch,
    digest: str | None,
    state: str,
) -> None:
    report, _statements = _plan(monkeypatch, _Catalog(digest=digest))

    assert report["digest"] == state
    assert report["decision"] == "apply"
    # Nothing pending: the change is in inputs no tag reviewed.
    assert report["blue_restorable"] is False


def test_verify_drift_needs_apply_rather_than_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    report, _statements = _plan(monkeypatch, _Catalog(), verify_fail="app_role_grants")

    assert report["decision"] == "apply"
    assert report["refusals"] == []
    assert report["verify"]["failed_check"] == "app_role_grants"
    assert report["verify"]["checks"] == ["trigger_contract"]


def test_executor_probe_names_every_missing_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _Catalog(
        authority=(False, False, True, True, False),
        unowned=[("function", "enforce_campaign_json_contract()"), ("relation", "campaigns")],
    )
    report, _statements = _plan(monkeypatch, catalog)

    assert report["decision"] == "refuse"
    assert report["refusals"] == ["executor_ownership"]
    assert report["executor_missing"] == [
        "database:owner",
        "role:pg_database_owner:SET",
        "schema:mip_app",
        "function:enforce_campaign_json_contract()",
        "relation:campaigns",
    ]


def test_executor_probe_counts_unreviewed_objects_without_naming_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _Catalog(
        unowned=[
            ("relation", "acme_lender_export"),
            ("relation", "saved_views"),
            ("relation", "zz_foreign_copy"),
            ("type", "acme_status"),
        ]
    )
    report, _statements = _plan(monkeypatch, catalog)

    assert report["executor_missing"] == [
        "relation:saved_views",
        "relation:<unreviewed>x2",
        "type:<unreviewed>x1",
    ]
    assert "acme" not in json.dumps(report) and "zz_foreign" not in json.dumps(report)


def test_executor_probe_on_a_fresh_database_needs_create_not_schema_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, _statements = _plan(
        monkeypatch, _Catalog(applied=None, digest=None, authority=(True, True, False, False, False))
    )

    assert report["executor_missing"] == ["database:CREATE"]
    assert report["decision"] == "refuse"


def test_a_fresh_install_plans_every_version(monkeypatch: pytest.MonkeyPatch) -> None:
    report, _statements = _plan(
        monkeypatch,
        _Catalog(applied=None, digest=None, authority=(True, True, True, False, False)),
        verify_fail="trigger_contract",
    )

    assert report["decision"] == "apply"
    assert report["migrations"]["applied"] == 0
    assert report["migrations"]["pending"] == ["2026_01_01_base", "2026_01_02_added_column"]
    assert report["digest"] == "absent"


def test_a_database_ahead_of_the_release_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    report, _statements = _plan(monkeypatch, _Catalog(applied={*_DECLARED, "2026_02_01_newer"}))

    assert report["decision"] == "refuse"
    assert report["refusals"] == ["database_ahead_of_release"]
    assert report["migrations"]["unknown_applied"] == ["2026_02_01_newer"]


def test_an_apply_preflight_failure_refuses_and_skips_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, statements = _plan(monkeypatch, _Catalog(), preflight_fail="trigger_inventory")

    assert report["decision"] == "refuse"
    assert report["refusals"] == ["apply_preflight:trigger_inventory"]
    assert "SELECT 'check:trigger_contract'" not in statements


def test_a_missing_runtime_role_refuses_before_any_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    report, statements = _plan(monkeypatch, _Catalog(roles=set()))

    assert report["decision"] == "refuse"
    assert report["refusals"] == ["runtime_roles"]
    assert len(statements) == 2  # SHOW, then the role lookup


# -- the dispatcher and the facade -----------------------------------------


def _dispatch(mode: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = main_read_only(
        mode,
        {"host": "test"},
        app_name=None,
        verifier_role=None,
        resolve_app_role_fn=lambda: _APP_ROLE,
        schema_inputs_fn=lambda: render_schema_inputs(
            ROOT, lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
        ),
    )
    output = capsys.readouterr().out
    assert _APP_ROLE not in output
    assert output.count("\n") == 1
    return code, json.loads(output)


def test_plan_exit_code_is_zero_for_a_decision_and_two_for_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install(monkeypatch, _Catalog(applied=set()).route)
    _stub_checks(monkeypatch)
    assert _dispatch("plan", capsys)[0] == 0

    _install(monkeypatch, _Catalog(applied=set(), authority=(False, True, True, True, True)).route)
    code, report = _dispatch("plan", capsys)
    assert (code, report["decision"], report["refusals"]) == (2, "refuse", ["executor_ownership"])


def test_the_guard_refuses_a_write_before_the_driver_sees_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = _install(monkeypatch, _Catalog().route)
    _stub_checks(monkeypatch)
    monkeypatch.setattr(
        lakebase_migration_plan,
        "VERIFY_CHECKS",
        (_stub("rogue", statement="UPDATE mip_app.campaigns SET status = 'archived'"),),
    )

    code, report = _dispatch("plan", capsys)

    # A refusal by the fake would surface as read_only_session instead.
    assert (code, report["refusals"], report["decision"]) == (2, ["read_only_guard"], "refuse")
    assert not any("UPDATE" in text for text, _ in connection.cursor_object.executed)
    assert (connection.commits, connection.rollbacks, connection.closed) == (0, 1, True)


def test_unrenderable_inputs_refuse_without_opening_a_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **_kwargs: pytest.fail("connected"))
    code = main_read_only(
        "plan",
        {"host": "test"},
        app_name=None,
        verifier_role=None,
        resolve_app_role_fn=lambda: _APP_ROLE,
        schema_inputs_fn=lambda: ("-- mip:migration version=x compat=maybe\n", ("", "", "", "")),
    )

    assert code == 2
    assert json.loads(capsys.readouterr().out)["refusals"] == ["migration_inputs"]


def test_facade_plan_mode_reads_the_real_inputs_and_never_reaches_apply(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    schema_sql, sql_texts = render_schema_inputs(
        ROOT, lender_name="Summit Mortgage", lender_nmls_id="", tenant_id=""
    )
    declared = {migration.version for migration in parse_schema_migrations(schema_sql)}
    connection = _install(
        monkeypatch, _Catalog(applied=declared, digest=schema_input_digest(sql_texts)).route
    )
    _stub_checks(monkeypatch)
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda **_kwargs: {"host": "test"})
    monkeypatch.setattr(lakebase_migrate, "_resolve_app_role", lambda: _APP_ROLE)
    for mutator in ("_preflight_database_roles", "_run_transaction", "_apply_app_role_grants"):
        monkeypatch.setattr(lakebase_migrate, mutator, lambda *_a, **_k: pytest.fail("apply path"))

    with pytest.raises(SystemExit) as exit_info:
        lakebase_migrate.main(lakebase_instance="instance", lakebase_database="db", mode="plan")

    assert exit_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["decision"], report["digest"]) == ("noop", "equal")
    assert report["migrations"]["declared"] == len(declared) > 40
    _assert_read_only_session(connection)


def test_cli_defaults_to_apply_so_the_bundle_job_is_unchanged() -> None:
    parser = lakebase_migrate.build_parser()

    assert parser.parse_args([]).mode == "apply"
    assert parser.parse_args(["--mode", "plan"]).mode == "plan"
    assert parser.parse_args(["--mode", "verify"]).mode == "verify"
    with pytest.raises(SystemExit):
        parser.parse_args(["--mode", "rehearse"])


# -- verify with the real postflights --------------------------------------


def _trigger_rows() -> Rows:
    return sorted(
        (
            table_schema, table_name, trigger_name, "O", trigger_type, 0, True, True,
            function_schema, function_name, function_arguments, "f", "trigger", False,
            "migration-owner", "migration-owner", True, True, True, False, True, True,
            True, True, True,
        )  # fmt: skip
        for (table_schema, table_name, trigger_name), (
            function_schema,
            function_name,
            function_arguments,
            trigger_type,
        ) in _APP_TRIGGER_CONTRACT.items()
    )


def _app_role_postflight_rows(role: str) -> Iterator[Rows]:
    """One answer per statement of _postflight_app_role_grants, in order."""

    yield [(role,)]
    yield [(True, False, False, True, False)]
    yield [(table,) for table in sorted(_APP_ROLE_TABLE_PRIVILEGES)]
    yield sorted((t, p) for t, ps in _APP_ROLE_TABLE_PRIVILEGES.items() for p in ps)
    yield []
    yield [(sequence,) for sequence in sorted(_APP_ROLE_SEQUENCE_PRIVILEGES)]
    yield sorted((s, p) for s, ps in _APP_ROLE_SEQUENCE_PRIVILEGES.items() for p in ps)
    yield []
    yield [("mip_app", "USAGE")]
    yield []
    yield []
    yield sorted(
        ("mip_app", name, arguments, "f", False, "migration-owner", 0, False, True, False)
        for (name, arguments), privileges in _APP_ROLE_ROUTINE_PRIVILEGES.items()
        if "EXECUTE" in privileges
    )
    yield [(role, False, False, False, False, False, True, True)]
    yield []
    yield []
    yield []
    yield _trigger_rows()


def _verify_router(*, trigger_rows: Rows) -> Router:
    answers: list[tuple[str | None, Rows]] = [
        ("SHOW transaction_read_only", [("on",)]),
        ("FROM pg_roles WHERE rolname = %s", [(_APP_ROLE,)]),
        ("to_regnamespace('mip_app')", [(True,)]),
        ("FROM pg_trigger trigger", trigger_rows),
        ("FROM pg_event_trigger event_trigger", []),
        *((None, rows) for rows in _app_role_postflight_rows(_APP_ROLE)),
    ]

    def route(text: str, _params: Any) -> Rows | None:
        if not answers:
            return None
        marker, rows = answers.pop(0)
        assert marker is None or marker in text, (marker, text[:80])
        return rows

    return route


_SEAMS = CheckContext(
    app_role=_APP_ROLE,
    verifier_role=None,
    allow_absent_managed_event_triggers=True,
    allow_absent_provider_schema=True,
)


def test_verify_runs_the_real_postflights_with_reads_only(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _install(monkeypatch, _verify_router(trigger_rows=_trigger_rows()))

    report = run_verify({"host": "test"}, _SEAMS)

    statements = _assert_read_only_session(connection)
    assert report["passed"] is True, report
    assert report["checks"] == [name for name, _check in lakebase_migration_plan.VERIFY_CHECKS]
    assert len(statements) == 5 + 17
    assert _APP_ROLE not in json.dumps(report)


def test_verify_reports_the_first_failing_postflight_without_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _install(monkeypatch, _verify_router(trigger_rows=_trigger_rows()[1:]))

    report = run_verify({"host": "test"}, _SEAMS)

    _assert_read_only_session(connection)
    assert report == {
        "mode": "verify",
        "refusals": [],
        "checks": ["schema_present"],
        "failed_check": "trigger_contract",
        "passed": False,
    }


def test_verify_refuses_when_the_runtime_role_is_not_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _install(monkeypatch, _Catalog(roles=set()).route)

    report = run_verify({"host": "test"}, _CTX)

    assert report["refusals"] == ["runtime_roles"] and report["passed"] is False
    assert len(_assert_read_only_session(connection)) == 2


def test_read_only_session_refuses_a_session_the_server_did_not_make_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _install(monkeypatch, lambda text, _params: [("off",)])

    with (
        pytest.raises(ReadOnlyStatementRefused, match="not read-only"),
        read_only_session({"host": "test"}),
    ):
        pytest.fail("the body must not run")

    assert (connection.commits, connection.rollbacks, connection.closed) == (0, 1, True)


# -- static proof over every branch ----------------------------------------

_MUTATORS = frozenset(
    {
        "_apply_app_role_grants",
        "_close_public_schema_boundary",
        "_expect_database_rejection",
        "_postflight_no_pre_boundary_sessions",
        "_preflight_database_roles",
        "_quarantine_existing_reviewed_triggers",
        "_quarantine_reviewed_constraints",
        "_run_outreach_integrity_probe",
        "_run_transaction",
    }
)


@dataclass
class _Module:
    functions: dict[str, ast.FunctionDef]
    imports: dict[str, tuple[str, str]]
    assigns: dict[str, ast.expr]


def _modules() -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for path in sorted((ROOT / "jobs").glob("lakebase_migration_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = _Module(functions={}, imports={}, assigns={})
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                module.functions[node.name] = node
            elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("jobs."):
                for alias in node.names:
                    module.imports[alias.asname or alias.name] = (str(node.module), alias.name)
            elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        module.assigns[target.id] = node.value
        modules[f"jobs.{path.stem}"] = module
    return modules


def _reachable_statements(entry: tuple[str, str]) -> tuple[set[str], list[str], int]:
    """Walk every function reachable by name; collect each ``.execute`` statement."""

    modules = _modules()
    seen: set[tuple[str, str]] = set()
    walked_assigns: set[tuple[str, str]] = set()
    statements: list[str] = []
    non_literal = 0
    stack = [entry]
    while stack:
        module_name, name = stack.pop()
        if (module_name, name) in seen:
            continue
        seen.add((module_name, name))
        module = modules[module_name]
        nodes: list[ast.AST] = [module.functions[name]]
        while nodes:
            node = nodes.pop()
            for child in ast.walk(node):
                if isinstance(child, ast.Name):
                    if child.id in module.functions:
                        stack.append((module_name, child.id))
                    elif child.id in module.imports:
                        target = module.imports[child.id]
                        if target[0] in modules and target[1] in modules[target[0]].functions:
                            stack.append(target)
                    elif (
                        child.id in module.assigns
                        and (module_name, child.id) not in walked_assigns
                    ):
                        walked_assigns.add((module_name, child.id))
                        nodes.append(module.assigns[child.id])
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "execute"
                    and child.args
                ):
                    statement = child.args[0]
                    if isinstance(statement, ast.Name) and statement.id in module.assigns:
                        statement = module.assigns[statement.id]
                    if isinstance(statement, ast.Constant) and isinstance(statement.value, str):
                        statements.append(statement.value)
                    else:
                        non_literal += 1
    return {name for _module, name in seen}, statements, non_literal


def test_every_statement_reachable_from_plan_or_verify_is_a_literal_read() -> None:
    reached, statements, non_literal = _reachable_statements(
        ("jobs.lakebase_migration_plan", "main_read_only")
    )

    assert not reached & _MUTATORS
    assert {"_postflight_app_role_grants", "_preflight_executable_schema_hooks"} <= reached
    assert non_literal == 0
    assert len(statements) >= 40
    for statement in statements:
        assert _is_read(statement), statement[:80]
        assert_read_statement(statement)


def test_the_static_walk_is_not_vacuous_on_the_apply_path() -> None:
    reached, statements, non_literal = _reachable_statements(
        ("jobs.lakebase_migration_grants", "_apply_app_role_grants")
    )

    assert "_close_public_schema_boundary" in reached
    assert non_literal > 0  # the f-string GRANT/REVOKE statements
    assert "SET LOCAL ROLE pg_database_owner" in statements
    with pytest.raises(ReadOnlyStatementRefused):
        assert_read_statement("SET LOCAL ROLE pg_database_owner")
