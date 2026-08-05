"""Reviewed Databricks samples-catalog baseline for runtime identities."""

from __future__ import annotations

from typing import Any

from tools.databricks.agent_runtime_uc_baseline import (
    _ACCOUNT_USERS_DIRECT,
    _CATALOG_INFORMATION_SCHEMA_TABLES,
    _SAMPLES_INHERITED,
    _SAMPLES_SCHEMA_PRIVILEGES,
)
from tools.databricks.agent_runtime_uc_inventory import (
    _assert_privileges,
    _assert_system_owned,
    _full_name,
    _strict_text,
)


def _assert_samples_catalog_baseline(workspace: Any, *, principal: str) -> None:
    """Require the exact Databricks-managed samples inheritance contract."""

    for schema in workspace.schemas.list("samples", include_browse=True):
        _assert_system_owned(schema, label="samples schema")
        schema_name = _strict_text(getattr(schema, "name", None))
        schema_full_name = _full_name(schema, fallback=f"samples.{schema_name}")
        schema_sources = {
            action: set(_SAMPLES_INHERITED)
            for action in _SAMPLES_SCHEMA_PRIVILEGES
        }
        if schema_name == "information_schema":
            schema_sources["USE_SCHEMA"] = {
                *_SAMPLES_INHERITED,
                *_ACCOUNT_USERS_DIRECT,
            }
        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected=set(_SAMPLES_SCHEMA_PRIVILEGES),
            expected_source_map=schema_sources,
        )
        for function in workspace.functions.list(
            "samples",
            schema_name,
            include_browse=True,
        ):
            _assert_system_owned(function, label="samples function")
            function_name = _strict_text(getattr(function, "name", None))
            _assert_privileges(
                workspace,
                securable_type="function",
                full_name=_full_name(
                    function,
                    fallback=f"{schema_full_name}.{function_name}",
                ),
                principal=principal,
                expected={"EXECUTE"},
                expected_source_map={"EXECUTE": set(_SAMPLES_INHERITED)},
            )
        for table in workspace.tables.list(
            "samples",
            schema_name,
            include_browse=True,
            omit_columns=True,
            omit_properties=True,
        ):
            _assert_system_owned(table, label="samples table")
            table_name = _strict_text(getattr(table, "name", None))
            table_sources = set(_SAMPLES_INHERITED)
            if (
                schema_name == "information_schema"
                and table_name in _CATALOG_INFORMATION_SCHEMA_TABLES
            ):
                table_sources.update(_ACCOUNT_USERS_DIRECT)
            _assert_privileges(
                workspace,
                securable_type="table",
                full_name=_full_name(
                    table,
                    fallback=f"{schema_full_name}.{table_name}",
                ),
                principal=principal,
                expected={"SELECT"},
                expected_source_map={"SELECT": table_sources},
            )
        for volume in workspace.volumes.list(
            "samples",
            schema_name,
            include_browse=True,
        ):
            _assert_system_owned(volume, label="samples volume")
            volume_name = _strict_text(getattr(volume, "name", None))
            _assert_privileges(
                workspace,
                securable_type="volume",
                full_name=_full_name(
                    volume,
                    fallback=f"{schema_full_name}.{volume_name}",
                ),
                principal=principal,
                expected={"READ_VOLUME"},
                expected_source_map={"READ_VOLUME": set(_SAMPLES_INHERITED)},
            )
