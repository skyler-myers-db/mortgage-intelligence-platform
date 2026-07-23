"""Shared fail-closed Unity Catalog inventory helpers for agent-runtime audits."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools.databricks.agent_runtime_uc_baseline import (
    _ACCOUNT_USERS_DIRECT,
    _CATALOG_INFORMATION_SCHEMA_TABLES,
    PrivilegeSource,
)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _effective_privileges(
    workspace: Any,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
) -> set[str]:
    return set(
        _effective_privilege_sources(
            workspace,
            securable_type=securable_type,
            full_name=full_name,
            principal=principal,
        )
    )


def _effective_privilege_sources(
    workspace: Any,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
) -> dict[str, set[PrivilegeSource]]:
    """Read every effective page and preserve the principal behind each action."""

    token: str | None = None
    seen_tokens: set[str] = set()
    privileges: dict[str, set[PrivilegeSource]] = {}
    while True:
        response = workspace.grants.get_effective(
            securable_type,
            full_name,
            principal=principal,
            max_results=1000,
            page_token=token,
        )
        for assignment in getattr(response, "privilege_assignments", None) or []:
            source = _text(getattr(assignment, "principal", None))
            if not source:
                raise RuntimeError("effective permissions returned an empty principal")
            for privilege in getattr(assignment, "privileges", None) or []:
                name = _text(getattr(privilege, "privilege", None)).upper()
                if not name:
                    raise RuntimeError(
                        f"effective permissions returned an empty privilege for "
                        f"{securable_type} {full_name}"
                    )
                inherited_type = _text(getattr(privilege, "inherited_from_type", None)).upper()
                inherited_name = _text(getattr(privilege, "inherited_from_name", None))
                privileges.setdefault(name, set()).add((source, inherited_type, inherited_name))
        next_token = _text(getattr(response, "next_page_token", None))
        if not next_token:
            return privileges
        if next_token in seen_tokens:
            raise RuntimeError("effective permissions pagination repeated a page token")
        seen_tokens.add(next_token)
        token = next_token


def _assert_privileges(
    workspace: Any,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
    expected: set[str],
    expected_source_map: dict[str, set[PrivilegeSource]] | None = None,
) -> None:
    actual_sources = _effective_privilege_sources(
        workspace,
        securable_type=securable_type,
        full_name=full_name,
        principal=principal,
    )
    actual = set(actual_sources)
    source_mismatch = expected_source_map is not None and actual_sources != expected_source_map
    if actual != expected or source_mismatch:
        raise RuntimeError(
            "agent-runtime effective UC boundary failed for "
            f"{securable_type} {full_name}: expected={sorted(expected)}, "
            f"actual={sorted(actual)}, sources={actual_sources}"
        )


def _full_name(value: object, *, fallback: str = "") -> str:
    return _text(getattr(value, "full_name", None)) or fallback


def _catalog_name(value: object) -> str:
    explicit = _text(getattr(value, "catalog_name", None))
    if explicit:
        return explicit
    full_name = _full_name(value)
    return full_name.split(".", 1)[0] if "." in full_name else ""


def _schema_name(value: object) -> str:
    explicit = _text(getattr(value, "schema_name", None))
    if explicit:
        return explicit
    parts = _full_name(value).split(".", 2)
    return parts[1] if len(parts) == 3 else ""


def _assert_not_runtime_owned(
    item: object,
    *,
    owner_aliases: set[str],
    label: str,
) -> None:
    owner = _text(getattr(item, "owner", None))
    if not owner:
        raise RuntimeError(f"{label} returned an empty owner")
    if owner.casefold() in owner_aliases:
        raise RuntimeError(f"agent-runtime is an effective owner of forbidden {label}")


def _assert_system_owned(item: object, *, label: str) -> None:
    if _text(getattr(item, "owner", None)) != "System user":
        raise RuntimeError(f"{label} is not owned by Databricks System user")


def _assert_no_catalog_child_privileges(
    workspace: Any,
    *,
    catalog: str,
    principal: str,
    owner_check: Callable[[object], None] | None = None,
) -> None:
    """Inventory a non-MIP catalog so a direct child grant cannot hide below it."""

    def check_owner(item: object, *, reviewed_system_object: bool = False) -> None:
        if owner_check is None:
            return
        if reviewed_system_object:
            if _text(getattr(item, "owner", None)) != "System user":
                raise RuntimeError("foreign information-schema object is not owned by System user")
            return
        owner_check(item)

    for schema in workspace.schemas.list(catalog, include_browse=True):
        schema_name = _text(getattr(schema, "name", None))
        if not schema_name:
            raise RuntimeError("workspace schema inventory returned an empty name")
        schema_full_name = _full_name(schema, fallback=f"{catalog}.{schema_name}")
        check_owner(schema, reviewed_system_object=schema_name == "information_schema")
        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected={"USE_SCHEMA"} if schema_name == "information_schema" else set(),
            expected_source_map=(
                {"USE_SCHEMA": set(_ACCOUNT_USERS_DIRECT)}
                if schema_name == "information_schema"
                else None
            ),
        )
        inventory: tuple[tuple[str, Any], ...] = (
            (
                "function",
                workspace.functions.list(catalog, schema_name, include_browse=True),
            ),
            (
                "table",
                workspace.tables.list(
                    catalog,
                    schema_name,
                    include_browse=True,
                    omit_columns=True,
                    omit_properties=True,
                ),
            ),
            (
                "volume",
                workspace.volumes.list(catalog, schema_name, include_browse=True),
            ),
        )
        for securable_type, objects in inventory:
            for item in objects:
                item_name = _text(getattr(item, "name", None))
                if not item_name:
                    raise RuntimeError(
                        f"workspace {securable_type} inventory returned an empty name"
                    )
                expected = (
                    {"SELECT"}
                    if securable_type == "table"
                    and schema_name == "information_schema"
                    and item_name in _CATALOG_INFORMATION_SCHEMA_TABLES
                    else set()
                )
                check_owner(
                    item,
                    reviewed_system_object=(
                        securable_type == "table"
                        and schema_name == "information_schema"
                        and item_name in _CATALOG_INFORMATION_SCHEMA_TABLES
                    ),
                )
                _assert_privileges(
                    workspace,
                    securable_type=securable_type,
                    full_name=_full_name(
                        item,
                        fallback=f"{schema_full_name}.{item_name}",
                    ),
                    principal=principal,
                    expected=expected,
                    expected_source_map=(
                        {"SELECT": set(_ACCOUNT_USERS_DIRECT)} if expected else None
                    ),
                )
