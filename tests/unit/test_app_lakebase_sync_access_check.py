"""The read-only Lakebase sync-access check runs only SELECTs and matches the postflight."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.unit import test_converge_app_lakebase_sync_access as sync_tests
from tests.unit.test_campaign_treatment_access_check import _Spy
from tools.databricks import converge_app_lakebase_sync_access as converger
from tools.databricks.app_lakebase_sync_access_check import check_app_lakebase_sync_access

CATALOG, SYNC_SCHEMA, SYNC_TABLES = (
    sync_tests.CATALOG,
    sync_tests.SYNC_SCHEMA,
    sync_tests.SYNC_TABLES,
)
APP_ID, GROUP = sync_tests.APP_ID, "mip-sync-readers"
_IDENTIFIERS = (
    CATALOG,
    SYNC_SCHEMA,
    *SYNC_TABLES,
    "campaigns",
    "mip_app",
    APP_ID,
    sync_tests.APP_SCIM_ID,
    GROUP,
    sync_tests.WAREHOUSE_ID,
)
_READ_ONLY_METHODS = {"get", "list", "execute_statement"}


def _check(mode: str, **workspace_kwargs: Any) -> tuple[list[str], list[str], list[str]]:
    workspace, execution = sync_tests._workspace(groups=(GROUP,), **workspace_kwargs)
    calls: list[str] = []
    drift = check_app_lakebase_sync_access(
        warehouse_id=sync_tests.WAREHOUSE_ID,
        app_application_id=APP_ID,
        app_scim_id=sync_tests.APP_SCIM_ID,
        sync_catalog=CATALOG,
        sync_schema=SYNC_SCHEMA,
        sync_tables=",".join(SYNC_TABLES),
        mode=mode,  # type: ignore[arg-type]
        workspace=_Spy(workspace, calls),  # type: ignore[arg-type]
    )
    statements = execution.statements
    assert statements, "the check ran no audit at all"
    for statement in statements:
        assert statement.startswith("/* mip_sync_") and "SELECT" in statement, statement
        assert not any(verb in statement for verb in ("GRANT ", "REVOKE ", "ALTER ")), statement
    assert {call.rsplit(".", 1)[-1] for call in calls} <= _READ_ONLY_METHODS, sorted(set(calls))
    for line in drift:
        assert not any(identifier in line for identifier in _IDENTIFIERS), line
    return drift, statements, calls


def test_exact_runtime_boundary_reports_no_drift() -> None:
    drift, statements, _ = _check("runtime", query_rows=sync_tests._runtime_postflight())

    assert drift == []
    assert len(statements) == 7


def test_quiesce_reports_every_retained_runtime_privilege() -> None:
    drift, _, _ = _check("quiesce", query_rows=sync_tests._runtime_postflight())

    assert drift == [
        "sync catalog: direct privileges are ['USE CATALOG'], reviewed []",
        "sync schema: USE SCHEMA is retained in quiesce mode",
        *["reviewed synced table: SELECT is retained in quiesce mode"] * len(SYNC_TABLES),
    ]


def test_runtime_drift_is_reported_symbolically_and_completely() -> None:
    rows = sync_tests._runtime_postflight(
        catalog_rows=[[CATALOG, "USE CATALOG", GROUP, "NONE"]],
        schema_rows=[[CATALOG, "mip_app", "USE SCHEMA", APP_ID, "NONE"]],
        table_rows=[
            [CATALOG, SYNC_SCHEMA, SYNC_TABLES[0], "SELECT", APP_ID, "NONE"],
            [CATALOG, SYNC_SCHEMA, SYNC_TABLES[1], "MODIFY", APP_ID, "NONE"],
        ],
        ownership_rows=[["SCHEMA", CATALOG, SYNC_SCHEMA, None, APP_ID]],
    )

    drift, _, _ = _check("runtime", query_rows=rows)

    assert drift == [
        "sync catalog: USE CATALOG is inherited through a group",
        "sync catalog: direct privileges are [], reviewed ['USE CATALOG']",
        "unreviewed schema: App holds USE SCHEMA",
        "sync schema: direct privileges are [], reviewed ['USE SCHEMA']",
        "reviewed synced table: unexpected MODIFY",
        "reviewed synced tables: SELECT is missing on 2 of 3",
        "ownership: App owns a Lakebase sync schema object",
    ]


def test_runtime_absent_targets_are_drift_and_quiesce_absent_catalog_is_not() -> None:
    drift, _, _ = _check(
        "runtime",
        schemas=("mip_app", "public"),
        tables=(("mip_app", "campaigns"),),
        query_rows={"mip_sync_postflight_catalog_privileges": []},
    )
    assert drift[:2] == [
        "sync schema: absent (runtime convergence refuses)",
        "reviewed synced tables: 3 of 3 absent (runtime convergence refuses)",
    ]

    quiesced, statements, _ = _check("quiesce", query_rows={"mip_sync_catalog_presence": []})
    assert quiesced == []
    assert len(statements) == 1


def test_cli_check_exits_one_on_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace, execution = sync_tests._workspace(query_rows=sync_tests._runtime_postflight())
    monkeypatch.setattr(
        "tools.databricks.app_lakebase_sync_access_check.WorkspaceClient", lambda: workspace
    )
    argv = [
        "--warehouse-id", sync_tests.WAREHOUSE_ID,
        "--app-application-id", APP_ID,
        "--app-scim-id", sync_tests.APP_SCIM_ID,
        "--sync-catalog", CATALOG,
        "--sync-schema", SYNC_SCHEMA,
        "--sync-tables", ",".join(SYNC_TABLES),
        "--check",
    ]  # fmt: skip

    assert converger.main([*argv, "--mode", "quiesce"]) == 1
    output = capsys.readouterr().out
    assert "[mip-lakebase-sync-access] drift mode=quiesce: sync schema" in output
    assert converger.main([*argv, "--mode", "runtime"]) == 0
    assert "check passed mode=runtime" in capsys.readouterr().out
    assert not any("GRANT" in sql or "REVOKE" in sql for sql in execution.statements)


_GRANTEES = st.sampled_from((APP_ID, GROUP))
_SOURCES = st.sampled_from(("NONE", "CATALOG", "SCHEMA"))
_SCHEMAS = st.sampled_from((SYNC_SCHEMA, "mip_app", "public"))
_TABLES = st.sampled_from((*SYNC_TABLES, "campaigns"))
_CATALOG_ROWS = st.lists(
    st.tuples(st.sampled_from(("USE CATALOG", "BROWSE", "MANAGE")), _GRANTEES, _SOURCES).map(
        lambda row: [CATALOG, *row]
    ),
    max_size=3,
)
_SCHEMA_ROWS = st.lists(
    st.tuples(_SCHEMAS, st.sampled_from(("USE SCHEMA", "SELECT")), _GRANTEES, _SOURCES).map(
        lambda row: [CATALOG, *row]
    ),
    max_size=3,
)
_TABLE_ROWS = st.lists(
    st.tuples(_SCHEMAS, _TABLES, st.sampled_from(("SELECT", "MODIFY")), _GRANTEES, _SOURCES).map(
        lambda row: [CATALOG, *row]
    ),
    max_size=4,
)
_OWNERSHIP_ROWS = st.lists(
    st.sampled_from(
        (
            ["CATALOG", CATALOG, None, None, APP_ID],
            ["SCHEMA", CATALOG, SYNC_SCHEMA, None, GROUP],
            ["TABLE", CATALOG, SYNC_SCHEMA, SYNC_TABLES[0], APP_ID],
        )
    ),
    max_size=2,
)


@settings(
    max_examples=300,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    mode=st.sampled_from(("quiesce", "runtime")),
    catalog_rows=_CATALOG_ROWS,
    schema_rows=_SCHEMA_ROWS,
    table_rows=_TABLE_ROWS,
    ownership_rows=_OWNERSHIP_ROWS,
)
def test_property_collected_findings_are_empty_exactly_when_the_postflight_passes(
    mode: str,
    catalog_rows: list[list[object]],
    schema_rows: list[list[object]],
    table_rows: list[list[object]],
    ownership_rows: list[list[object]],
) -> None:
    rows = {
        "mip_sync_postflight_catalog_privileges": catalog_rows,
        "mip_sync_postflight_schema_privileges": schema_rows,
        "mip_sync_postflight_table_privileges": table_rows,
        "mip_sync_postflight_ownership": ownership_rows,
    }
    workspace, _ = sync_tests._workspace(groups=(GROUP,), query_rows=rows)
    kwargs: dict[str, Any] = {
        "warehouse_id": sync_tests.WAREHOUSE_ID,
        "catalog": CATALOG,
        "sync_schema": SYNC_SCHEMA,
        "sync_tables": SYNC_TABLES,
        "application_id": APP_ID,
        "grantees": (APP_ID, GROUP),
        "mode": mode,
    }
    findings: list[str] = []
    converger._postflight(workspace, findings=findings, **kwargs)  # type: ignore[arg-type]
    try:
        converger._postflight(workspace, **kwargs)  # type: ignore[arg-type]
        passed = True
    except RuntimeError:
        passed = False

    assert passed is (findings == [])
    for line in findings:
        assert not any(identifier in line for identifier in _IDENTIFIERS), line
