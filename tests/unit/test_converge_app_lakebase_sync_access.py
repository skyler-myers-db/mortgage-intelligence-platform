from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

from tools.databricks.converge_app_lakebase_sync_access import (
    converge_app_lakebase_sync_access,
)

CATALOG = "mip_app_state"
SYNC_SCHEMA = "mip_sync"
SYNC_TABLES = ("source_readiness", "segment_population", "funnel_snapshot_daily")
APP_ID = "app-client-id"
APP_SCIM_ID = "74635290620767"
WAREHOUSE_ID = "warehouse-id"


def _response(rows: list[list[object]] | None = None, *, state: str = "SUCCEEDED") -> object:
    data = rows or []
    return SimpleNamespace(
        status=SimpleNamespace(state=state, error=None if state == "SUCCEEDED" else "failed"),
        result=SimpleNamespace(data_array=data, truncated=False),
        manifest=SimpleNamespace(total_row_count=len(data)),
    )


class _ServicePrincipals:
    def __init__(self, *, application_id: str = APP_ID, scim_id: str = APP_SCIM_ID) -> None:
        self.application_id = application_id
        self.scim_id = scim_id

    def get(self, scim_id: str) -> object:
        assert scim_id == APP_SCIM_ID
        return SimpleNamespace(id=self.scim_id, application_id=self.application_id)


class _Groups:
    def __init__(self, group_names: tuple[str, ...]) -> None:
        self.groups = [
            SimpleNamespace(
                id=f"group-{index}",
                display_name=name,
                members=[
                    SimpleNamespace(value=APP_SCIM_ID if index == 0 else f"group-{index - 1}")
                ],
            )
            for index, name in enumerate(group_names)
        ]

    def list(self, **kwargs: object) -> list[object]:
        assert kwargs == {"attributes": "id,displayName"}
        return [
            SimpleNamespace(id=group.id, display_name=group.display_name) for group in self.groups
        ]

    def get(self, group_id: str) -> object:
        return next(group for group in self.groups if group.id == group_id)


class _Statements:
    def __init__(
        self,
        *,
        schemas: tuple[str, ...],
        tables: tuple[tuple[str, str], ...] = (),
        query_rows: dict[str, list[list[object]]] | None = None,
        query_state: dict[str, str] | None = None,
    ) -> None:
        self.schemas = schemas
        self.tables = tables
        self.query_rows = query_rows or {}
        self.query_state = query_state or {}
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _label(statement: str) -> str | None:
        labels = (
            "mip_sync_catalog_presence",
            "mip_sync_schema_presence",
            "mip_sync_table_presence",
            "mip_sync_postflight_catalog_privileges",
            "mip_sync_postflight_schema_privileges",
            "mip_sync_postflight_table_privileges",
            "mip_sync_postflight_ownership",
        )
        return next((label for label in labels if label in statement), None)

    def execute_statement(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        statement = str(kwargs["statement"])
        label = self._label(statement)
        if label is None:
            return _response()
        if label in self.query_rows:
            rows = self.query_rows[label]
        elif label == "mip_sync_catalog_presence":
            rows = [[CATALOG]]
        elif label == "mip_sync_schema_presence":
            rows = [[schema] for schema in sorted(self.schemas)]
        elif label == "mip_sync_table_presence":
            rows = [[schema, table] for schema, table in self.tables]
        else:
            rows = []
        return _response(rows, state=self.query_state.get(label, "SUCCEEDED"))

    @property
    def statements(self) -> list[str]:
        return [str(call["statement"]).strip() for call in self.calls]


def _runtime_postflight(
    *,
    schema_rows: list[list[object]] | None = None,
    table_rows: list[list[object]] | None = None,
    ownership_rows: list[list[object]] | None = None,
    catalog_rows: list[list[object]] | None = None,
) -> dict[str, list[list[object]]]:
    return {
        "mip_sync_postflight_catalog_privileges": catalog_rows
        if catalog_rows is not None
        else [[CATALOG, "USE CATALOG", APP_ID, "NONE"]],
        "mip_sync_postflight_schema_privileges": schema_rows
        if schema_rows is not None
        else [[CATALOG, SYNC_SCHEMA, "USE SCHEMA", APP_ID, "NONE"]],
        "mip_sync_postflight_table_privileges": table_rows
        if table_rows is not None
        else [[CATALOG, SYNC_SCHEMA, table, "SELECT", APP_ID, "NONE"] for table in SYNC_TABLES],
        "mip_sync_postflight_ownership": ownership_rows or [],
    }


def _workspace(
    *,
    schemas: tuple[str, ...] = ("mip_app", "mip_sync", "public"),
    tables: tuple[tuple[str, str], ...] = (
        ("mip_app", "campaigns"),
        ("mip_sync", "source_readiness"),
        ("mip_sync", "segment_population"),
        ("mip_sync", "funnel_snapshot_daily"),
        ("public", "schema_version"),
    ),
    groups: tuple[str, ...] = (),
    query_rows: dict[str, list[list[object]]] | None = None,
    query_state: dict[str, str] | None = None,
    application_id: str = APP_ID,
    scim_id: str = APP_SCIM_ID,
) -> tuple[object, _Statements]:
    execution = _Statements(
        schemas=schemas,
        tables=tables,
        query_rows=query_rows,
        query_state=query_state,
    )
    return (
        SimpleNamespace(
            statement_execution=execution,
            service_principals=_ServicePrincipals(
                application_id=application_id,
                scim_id=scim_id,
            ),
            groups=_Groups(groups),
        ),
        execution,
    )


def _converge(workspace: object, *, mode: str) -> bool:
    return converge_app_lakebase_sync_access(
        warehouse_id=WAREHOUSE_ID,
        app_application_id=APP_ID,
        app_scim_id=APP_SCIM_ID,
        sync_catalog=CATALOG,
        sync_schema=SYNC_SCHEMA,
        sync_tables=",".join(SYNC_TABLES),
        mode=mode,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
    )


def test_runtime_removes_direct_legacy_and_table_residue_before_exact_grants() -> None:
    workspace, execution = _workspace(query_rows=_runtime_postflight())

    assert _converge(workspace, mode="runtime")

    statements = execution.statements
    for schema, table in (
        ("mip_app", "campaigns"),
        *(("mip_sync", table) for table in SYNC_TABLES),
        ("public", "schema_version"),
    ):
        assert (
            f"REVOKE ALL PRIVILEGES ON TABLE `{CATALOG}`.`{schema}`.`{table}` " f"FROM `{APP_ID}`"
        ) in statements
        assert (
            f"REVOKE MANAGE ON TABLE `{CATALOG}`.`{schema}`.`{table}` " f"FROM `{APP_ID}`"
        ) in statements
    for schema in ("mip_app", "mip_sync", "public"):
        assert (
            f"REVOKE ALL PRIVILEGES ON SCHEMA `{CATALOG}`.`{schema}` FROM `{APP_ID}`"
        ) in statements
        assert (
            f"REVOKE MANAGE, EXTERNAL USE SCHEMA ON SCHEMA `{CATALOG}`.`{schema}` "
            f"FROM `{APP_ID}`"
        ) in statements
    assert f"REVOKE ALL PRIVILEGES ON CATALOG `{CATALOG}` FROM `{APP_ID}`" in statements
    assert f"REVOKE MANAGE ON CATALOG `{CATALOG}` FROM `{APP_ID}`" in statements
    assert f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `{APP_ID}`" in statements
    assert f"GRANT USE SCHEMA ON SCHEMA `{CATALOG}`.`{SYNC_SCHEMA}` TO `{APP_ID}`" in statements
    for table in SYNC_TABLES:
        assert (
            f"GRANT SELECT ON TABLE `{CATALOG}`.`{SYNC_SCHEMA}`.`{table}` TO `{APP_ID}`"
            in statements
        )
    assert all(call["warehouse_id"] == WAREHOUSE_ID for call in execution.calls)
    assert all(call["wait_timeout"] == "50s" for call in execution.calls)
    assert all(
        call["on_wait_timeout"] == ExecuteStatementRequestOnWaitTimeout.CANCEL
        for call in execution.calls
    )


def test_quiesce_removes_direct_access_without_restoring_any_grant() -> None:
    workspace, execution = _workspace()

    assert _converge(workspace, mode="quiesce")

    assert any("REVOKE ALL PRIVILEGES ON TABLE" in sql for sql in execution.statements)
    assert any("REVOKE ALL PRIVILEGES ON SCHEMA" in sql for sql in execution.statements)
    assert not any(sql.startswith("GRANT ") for sql in execution.statements)


def test_quiesce_rejects_inherited_group_privilege_after_direct_removal() -> None:
    query_rows = {
        "mip_sync_postflight_schema_privileges": [
            [CATALOG, "public", "SELECT", "nested-app-readers", "NONE"]
        ]
    }
    workspace, _execution = _workspace(
        groups=("direct-app-readers", "nested-app-readers"),
        query_rows=query_rows,
    )

    with pytest.raises(RuntimeError, match="effective quiesced schema privilege"):
        _converge(workspace, mode="quiesce")


def test_catalog_wide_inventory_revokes_direct_access_in_fourth_schema() -> None:
    workspace, execution = _workspace(
        schemas=("information_schema", "mip_app", "mip_sync", "public", "shadow"),
        tables=(
            *((SYNC_SCHEMA, table) for table in SYNC_TABLES),
            ("shadow", "unreviewed_data"),
        ),
        query_rows=_runtime_postflight(),
    )

    assert _converge(workspace, mode="runtime")
    assert (
        f"REVOKE ALL PRIVILEGES ON TABLE `{CATALOG}`.`shadow`.`unreviewed_data` " f"FROM `{APP_ID}`"
    ) in execution.statements
    assert (
        f"REVOKE ALL PRIVILEGES ON SCHEMA `{CATALOG}`.`shadow` FROM `{APP_ID}`"
        in execution.statements
    )
    assert not any("`information_schema`" in statement for statement in execution.statements)


def test_catalog_inventory_rejects_duplicate_information_schema_rows() -> None:
    workspace, _execution = _workspace(
        query_rows={
            "mip_sync_schema_presence": [
                ["information_schema"],
                ["information_schema"],
            ]
        }
    )

    with pytest.raises(RuntimeError, match="duplicate rows"):
        _converge(workspace, mode="quiesce")


def test_catalog_wide_postflight_rejects_fourth_schema_group_access() -> None:
    rows = _runtime_postflight(
        schema_rows=[
            [CATALOG, SYNC_SCHEMA, "USE SCHEMA", APP_ID, "NONE"],
            [CATALOG, "shadow", "USE SCHEMA", "nested-app-readers", "NONE"],
        ]
    )
    workspace, _execution = _workspace(
        schemas=("mip_app", "mip_sync", "public", "shadow"),
        groups=("nested-app-readers",),
        query_rows=rows,
    )

    with pytest.raises(RuntimeError, match="unrelated Lakebase schema"):
        _converge(workspace, mode="runtime")


def test_catalog_wide_postflight_rejects_fourth_schema_ownership() -> None:
    workspace, _execution = _workspace(
        schemas=("mip_app", "mip_sync", "public", "shadow"),
        query_rows=_runtime_postflight(
            ownership_rows=[["TABLE", CATALOG, "shadow", "owned_data", APP_ID]]
        ),
    )

    with pytest.raises(RuntimeError, match="effective owner"):
        _converge(workspace, mode="runtime")


def test_catalog_wide_queries_have_no_three_schema_predicate() -> None:
    workspace, execution = _workspace(query_rows=_runtime_postflight())

    assert _converge(workspace, mode="runtime")

    catalog_wide_labels = (
        "mip_sync_schema_presence",
        "mip_sync_postflight_schema_privileges",
        "mip_sync_postflight_table_privileges",
        "mip_sync_postflight_ownership",
    )
    for label in catalog_wide_labels:
        statement = next(sql for sql in execution.statements if label in sql)
        assert "schema_name IN" not in statement
        assert "table_schema IN" not in statement
        if label != "mip_sync_schema_presence":
            assert "information_schema" in statement


def test_runtime_rejects_target_privilege_inherited_through_group() -> None:
    rows = _runtime_postflight(
        schema_rows=[[CATALOG, SYNC_SCHEMA, "USE SCHEMA", "app-readers", "NONE"]]
    )
    workspace, _execution = _workspace(groups=("app-readers",), query_rows=rows)

    with pytest.raises(RuntimeError, match="inherits a target Lakebase schema privilege"):
        _converge(workspace, mode="runtime")


def test_postflight_rejects_unrelated_grantee_even_if_sql_predicate_is_violated() -> None:
    rows = _runtime_postflight(
        schema_rows=[[CATALOG, SYNC_SCHEMA, "USE SCHEMA", "unrelated-group", "NONE"]]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="unrelated grant"):
        _converge(workspace, mode="runtime")


@pytest.mark.parametrize("extra_privilege", ["MODIFY", "CREATE TABLE", "MANAGE"])
def test_runtime_rejects_extra_target_schema_privilege(extra_privilege: str) -> None:
    rows = _runtime_postflight(
        schema_rows=[
            [CATALOG, SYNC_SCHEMA, "USE SCHEMA", APP_ID, "NONE"],
            [CATALOG, SYNC_SCHEMA, extra_privilege, APP_ID, "NONE"],
        ]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="schema privileges are not exact"):
        _converge(workspace, mode="runtime")


def test_runtime_rejects_wrong_target_table_privilege() -> None:
    rows = _runtime_postflight(
        table_rows=[[CATALOG, SYNC_SCHEMA, "source_readiness", "MODIFY", APP_ID, "NONE"]]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="Unexpected effective target Lakebase table privilege"):
        _converge(workspace, mode="runtime")


def test_runtime_accepts_direct_select_on_every_exact_target_table() -> None:
    rows = _runtime_postflight(
        table_rows=[
            [CATALOG, SYNC_SCHEMA, table, "SELECT", APP_ID, "NONE"] for table in SYNC_TABLES
        ]
    )
    workspace, _execution = _workspace(query_rows=rows)

    assert _converge(workspace, mode="runtime")


def test_runtime_rejects_schema_inherited_target_table_select() -> None:
    rows = _runtime_postflight(
        table_rows=[[CATALOG, SYNC_SCHEMA, "source_readiness", "SELECT", APP_ID, "SCHEMA"]]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="not a direct exact-table grant"):
        _converge(workspace, mode="runtime")


def test_runtime_rejects_target_schema_privilege_inherited_from_catalog() -> None:
    rows = _runtime_postflight(
        schema_rows=[[CATALOG, SYNC_SCHEMA, "USE SCHEMA", APP_ID, "CATALOG"]]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="inherited from a broader object"):
        _converge(workspace, mode="runtime")


def test_missing_sync_schema_is_safe_in_quiesce_but_fatal_in_runtime() -> None:
    quiesce_workspace, quiesce_execution = _workspace(
        schemas=("mip_app", "public"),
        tables=(("mip_app", "campaigns"),),
    )
    assert not _converge(quiesce_workspace, mode="quiesce")
    assert not any(f".`{SYNC_SCHEMA}`" in sql for sql in quiesce_execution.statements)

    runtime_workspace, runtime_execution = _workspace(
        schemas=("mip_app", "public"),
        tables=(("mip_app", "campaigns"),),
    )
    with pytest.raises(RuntimeError, match="before the Lakebase sync schema exists"):
        _converge(runtime_workspace, mode="runtime")
    assert not any(sql.startswith(("GRANT ", "REVOKE ")) for sql in runtime_execution.statements)


@pytest.mark.parametrize(
    ("catalog", "schema", "message"),
    [
        ("Mip_App_State", SYNC_SCHEMA, "sync_catalog"),
        ("mip-app-state", SYNC_SCHEMA, "sync_catalog"),
        (CATALOG, "Mip_Sync", "sync_schema"),
        (CATALOG, "mip-sync", "sync_schema"),
        (CATALOG, "public", "legacy"),
        (CATALOG, "mip_app", "legacy"),
        (CATALOG, "information_schema", "reserved"),
    ],
)
def test_rejects_invalid_or_legacy_identifiers(catalog: str, schema: str, message: str) -> None:
    workspace, execution = _workspace(query_rows=_runtime_postflight())

    with pytest.raises(ValueError, match=message):
        converge_app_lakebase_sync_access(
            warehouse_id=WAREHOUSE_ID,
            app_application_id=APP_ID,
            app_scim_id=APP_SCIM_ID,
            sync_catalog=catalog,
            sync_schema=schema,
            sync_tables=",".join(SYNC_TABLES),
            mode="runtime",
            workspace=workspace,  # type: ignore[arg-type]
        )
    assert execution.calls == []


@pytest.mark.parametrize(
    "sync_tables",
    (
        "source_readiness,source_readiness",
        "source_readiness,Unreviewed",
        "source_readiness,",
    ),
)
def test_rejects_invalid_or_duplicate_sync_table_allowlist(sync_tables: str) -> None:
    workspace, execution = _workspace(query_rows=_runtime_postflight())

    with pytest.raises(ValueError, match="sync table|sync_tables"):
        converge_app_lakebase_sync_access(
            warehouse_id=WAREHOUSE_ID,
            app_application_id=APP_ID,
            app_scim_id=APP_SCIM_ID,
            sync_catalog=CATALOG,
            sync_schema=SYNC_SCHEMA,
            sync_tables=sync_tables,
            mode="runtime",
            workspace=workspace,  # type: ignore[arg-type]
        )
    assert execution.calls == []


def test_runtime_rejects_missing_reviewed_synced_table_before_grants() -> None:
    workspace, execution = _workspace(
        tables=(
            (SYNC_SCHEMA, "source_readiness"),
            (SYNC_SCHEMA, "segment_population"),
        ),
        query_rows=_runtime_postflight(),
    )

    with pytest.raises(RuntimeError, match="funnel_snapshot_daily"):
        _converge(workspace, mode="runtime")
    assert not any(sql.startswith("GRANT ") for sql in execution.statements)


def test_runtime_rejects_effective_select_on_unreviewed_sync_table() -> None:
    rows = _runtime_postflight(
        table_rows=[
            *[[CATALOG, SYNC_SCHEMA, table, "SELECT", APP_ID, "NONE"] for table in SYNC_TABLES],
            [CATALOG, SYNC_SCHEMA, "unreviewed", "SELECT", APP_ID, "NONE"],
        ]
    )
    workspace, _execution = _workspace(
        tables=(
            *((SYNC_SCHEMA, table) for table in SYNC_TABLES),
            (SYNC_SCHEMA, "unreviewed"),
        ),
        query_rows=rows,
    )

    with pytest.raises(RuntimeError, match="Unexpected effective target"):
        _converge(workspace, mode="runtime")


@pytest.mark.parametrize(
    ("groups", "ownership"),
    [
        ((), [["SCHEMA", CATALOG, SYNC_SCHEMA, None, APP_ID]]),
        (("nested-app-owners",), [["TABLE", CATALOG, SYNC_SCHEMA, "events", "nested-app-owners"]]),
        (("nested-app-owners",), [["CATALOG", CATALOG, None, None, "nested-app-owners"]]),
    ],
)
def test_rejects_direct_or_nested_group_ownership(
    groups: tuple[str, ...], ownership: list[list[object]]
) -> None:
    workspace, _execution = _workspace(
        groups=groups,
        query_rows=_runtime_postflight(ownership_rows=ownership),
    )

    with pytest.raises(RuntimeError, match="effective owner"):
        _converge(workspace, mode="runtime")


def test_rejects_forbidden_catalog_privilege() -> None:
    rows = _runtime_postflight(
        catalog_rows=[
            [CATALOG, "USE CATALOG", APP_ID, "NONE"],
            [CATALOG, "CREATE SCHEMA", APP_ID, "NONE"],
        ]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="Unexpected effective Lakebase sync catalog"):
        _converge(workspace, mode="runtime")


@pytest.mark.parametrize("mode", ["quiesce", "runtime"])
def test_rejects_stale_direct_catalog_browse(mode: str) -> None:
    rows = _runtime_postflight(
        catalog_rows=([[CATALOG, "USE CATALOG", APP_ID, "NONE"]] if mode == "runtime" else [])
        + [[CATALOG, "BROWSE", APP_ID, "NONE"]]
    )
    workspace, _execution = _workspace(query_rows=rows)

    with pytest.raises(RuntimeError, match="catalog privileges are not exact"):
        _converge(workspace, mode=mode)


def test_quiesce_rejects_stale_direct_catalog_use() -> None:
    workspace, _execution = _workspace(
        query_rows={
            "mip_sync_postflight_catalog_privileges": [[CATALOG, "USE CATALOG", APP_ID, "NONE"]]
        }
    )

    with pytest.raises(RuntimeError, match="catalog privileges are not exact"):
        _converge(workspace, mode="quiesce")


def test_fails_closed_on_saturated_or_invalid_sql_results() -> None:
    saturated_rows = [[CATALOG, SYNC_SCHEMA, "SELECT", APP_ID, "NONE"] for _ in range(1001)]
    workspace, _execution = _workspace(
        query_rows={"mip_sync_postflight_schema_privileges": saturated_rows}
    )
    with pytest.raises(RuntimeError, match="saturated its fail-closed row limit"):
        _converge(workspace, mode="quiesce")

    invalid_workspace, _invalid_execution = _workspace(
        query_rows={"mip_sync_postflight_table_privileges": [[CATALOG, SYNC_SCHEMA]]}
    )
    with pytest.raises(RuntimeError, match="invalid row shape"):
        _converge(invalid_workspace, mode="quiesce")


def test_rejects_mismatched_application_and_scim_identity_before_sql() -> None:
    workspace, execution = _workspace(application_id="different-app")

    with pytest.raises(RuntimeError, match="identifiers do not match"):
        _converge(workspace, mode="quiesce")
    assert execution.calls == []


def test_fails_closed_when_bounded_statement_does_not_succeed() -> None:
    workspace, _execution = _workspace(query_state={"mip_sync_catalog_presence": "FAILED"})

    with pytest.raises(RuntimeError, match="state=FAILED"):
        _converge(workspace, mode="quiesce")
