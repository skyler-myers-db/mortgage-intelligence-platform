"""Shared fail-closed Unity Catalog inventory helpers for agent-runtime audits."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from databricks.sdk.service.catalog import Privilege, SecurableType
from tools.databricks.agent_runtime_uc_baseline import (
    _ACCOUNT_USERS_DIRECT,
    _CATALOG_INFORMATION_SCHEMA_TABLES,
    PrivilegeSource,
)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _strict_text(value: object) -> str:
    if value is None:
        return ""
    if type(value) is not str or value != value.strip():
        raise RuntimeError("agent-runtime inventory returned noncanonical text")
    return value


def _exact_owner(item: object, *, label: str) -> str:
    owner = getattr(item, "owner", None)
    if (
        type(owner) is not str
        or not owner
        or owner != owner.strip()
    ):
        raise RuntimeError(f"{label} returned a noncanonical owner")
    return owner


def _assert_mip_schema_identity(
    schema: object,
    *,
    catalog_name: str,
    schema_name: str,
    full_name: str,
) -> None:
    if (
        _strict_text(getattr(schema, "catalog_name", None)) != catalog_name
        or _strict_text(getattr(schema, "name", None)) != schema_name
        or _strict_text(getattr(schema, "full_name", None)) != full_name
    ):
        raise RuntimeError("MIP schema inventory returned incomplete parent identity")


def _assert_mip_child_identity(
    item: object,
    *,
    catalog_name: str,
    schema_name: str,
    item_name: str,
    full_name: str,
    label: str,
) -> None:
    if (
        _strict_text(getattr(item, "catalog_name", None)) != catalog_name
        or _strict_text(getattr(item, "schema_name", None)) != schema_name
        or _strict_text(getattr(item, "name", None)) != item_name
        or _strict_text(getattr(item, "full_name", None)) != full_name
    ):
        raise RuntimeError(f"MIP {label} inventory returned incomplete parent identity")


def _assert_registered_model_identity(model: object) -> None:
    full_name = _strict_text(getattr(model, "full_name", None))
    parts = full_name.split(".")
    if (
        len(parts) != 3
        or not all(parts)
        or _strict_text(getattr(model, "catalog_name", None)) != parts[0]
        or _strict_text(getattr(model, "schema_name", None)) != parts[1]
        or _strict_text(getattr(model, "name", None)) != parts[2]
    ):
        raise RuntimeError(
            "workspace registered-model inventory returned incomplete parent identity"
        )


def _inference_table_suffix(name: str, *, family_prefix: str) -> str | None:
    pattern = re.compile(
        rf"{re.escape(family_prefix)}_([0-9a-f]{{12}})_payload"
        rf"(?:_request_logs|_assessment_logs)?"
    )
    match = pattern.fullmatch(name)
    return match.group(1) if match is not None else None


def _reviewed_model_family(name: str, *, family_name: str) -> bool:
    return re.fullmatch(rf"{re.escape(family_name)}_[0-9a-f]{{12}}", name) is not None


def _assert_authenticated_runtime(workspace: Any, *, application_id: str) -> set[str]:
    """Bind the visibility inventory to the runtime identity whose access it proves."""

    caller = workspace.current_user.me()
    principals = {
        _strict_text(getattr(caller, "user_name", None)),
        _strict_text(getattr(caller, "application_id", None)),
    } - {""}
    if application_id not in principals:
        raise RuntimeError(
            "agent-runtime UC inventory is not authenticated as the expected runtime identity"
        )
    caller_id = _strict_text(getattr(caller, "id", None))
    if not caller_id:
        raise RuntimeError("agent-runtime identity has no immutable SCIM id")
    groups = getattr(caller, "groups", None)
    if groups is None:
        raise RuntimeError("agent-runtime identity omitted its effective groups collection")
    owner_aliases = {caller_id.casefold(), *(value.casefold() for value in principals)}
    for group in groups:
        group_id = _strict_text(getattr(group, "value", None))
        display = _strict_text(getattr(group, "display", None))
        if not group_id or not display:
            raise RuntimeError("agent-runtime effective group identity is incomplete")
        owner_aliases.update({group_id.casefold(), display.casefold()})
    return owner_aliases


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
        if (
            response is None
            or not hasattr(response, "privilege_assignments")
            or not hasattr(response, "next_page_token")
        ):
            raise RuntimeError(
                "effective permissions returned an invalid response envelope"
            )
        assignments = getattr(response, "privilege_assignments", None)
        if assignments is not None and type(assignments) is not list:
            raise RuntimeError(
                "effective permissions returned a non-list privilege assignment collection"
            )
        assignment_items = [] if assignments is None else assignments
        for assignment in assignment_items:
            source = getattr(assignment, "principal", None)
            if (
                type(source) is not str
                or not source
                or source != source.strip()
            ):
                raise RuntimeError(
                    "effective permissions returned a noncanonical principal"
                )
            assignment_privileges = getattr(assignment, "privileges", None)
            if type(assignment_privileges) is not list:
                raise RuntimeError(
                    "effective permissions returned a non-list privilege collection"
                )
            if len(assignment_privileges) == 0:
                raise RuntimeError(
                    "effective permissions returned an empty privilege assignment"
                )
            for privilege in assignment_privileges:
                raw_name = getattr(privilege, "privilege", None)
                name_value = raw_name.value if isinstance(raw_name, Privilege) else raw_name
                if (
                    type(name_value) is not str
                    or not name_value
                    or name_value != name_value.strip()
                    or name_value != name_value.upper()
                ):
                    raise RuntimeError(
                        f"effective permissions returned a noncanonical privilege for "
                        f"{securable_type} {full_name}"
                    )
                raw_inherited_type = getattr(privilege, "inherited_from_type", None)
                inherited_type_value = (
                    raw_inherited_type.value
                    if isinstance(raw_inherited_type, SecurableType)
                    else raw_inherited_type
                )
                if inherited_type_value is None:
                    inherited_type = ""
                elif (
                    type(inherited_type_value) is not str
                    or not inherited_type_value
                    or inherited_type_value != inherited_type_value.strip()
                    or inherited_type_value != inherited_type_value.upper()
                ):
                    raise RuntimeError(
                        "effective permissions returned a noncanonical inheritance type"
                    )
                else:
                    inherited_type = inherited_type_value
                raw_inherited_name = getattr(privilege, "inherited_from_name", None)
                if raw_inherited_name is None:
                    inherited_name = ""
                elif (
                    type(raw_inherited_name) is not str
                    or not raw_inherited_name
                    or raw_inherited_name != raw_inherited_name.strip()
                ):
                    raise RuntimeError(
                        "effective permissions returned a noncanonical inheritance name"
                    )
                else:
                    inherited_name = raw_inherited_name
                if bool(inherited_type) != bool(inherited_name):
                    raise RuntimeError(
                        "effective permissions returned incomplete inheritance evidence"
                    )
                privileges.setdefault(name_value, set()).add(
                    (source, inherited_type, inherited_name)
                )
        raw_next_token = getattr(response, "next_page_token", None)
        if raw_next_token is None:
            next_token = ""
        elif (
            type(raw_next_token) is not str
            or raw_next_token != raw_next_token.strip()
        ):
            raise RuntimeError(
                "effective permissions returned a noncanonical pagination token"
            )
        else:
            next_token = raw_next_token
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
    return _strict_text(getattr(value, "full_name", None)) or fallback


def _catalog_name(value: object) -> str:
    explicit = _strict_text(getattr(value, "catalog_name", None))
    if explicit:
        return explicit
    full_name = _full_name(value)
    return full_name.split(".", 1)[0] if "." in full_name else ""


def _schema_name(value: object) -> str:
    explicit = _strict_text(getattr(value, "schema_name", None))
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
    owner = _exact_owner(item, label=label)
    if owner.casefold() in owner_aliases:
        raise RuntimeError(f"agent-runtime is an effective owner of forbidden {label}")


def _assert_system_owned(item: object, *, label: str) -> None:
    if _exact_owner(item, label=label) != "System user":
        raise RuntimeError(f"{label} is not owned by Databricks System user")


def _assert_no_catalog_child_privileges(
    workspace: Any,
    *,
    catalog: str,
    catalog_type: str,
    catalog_owner: str,
    principal: str,
    owner_check: Callable[[object], None] | None = None,
) -> None:
    """Inventory a non-MIP catalog so a direct child grant cannot hide below it."""

    normalized_catalog_type = catalog_type.strip().upper()
    expected_catalog_owner = catalog_owner
    if (
        not normalized_catalog_type
        or not expected_catalog_owner
        or expected_catalog_owner != expected_catalog_owner.strip()
    ):
        raise RuntimeError("foreign catalog inventory has incomplete identity evidence")
    managed_online = normalized_catalog_type == "MANAGED_ONLINE_CATALOG"
    information_schema_owner = expected_catalog_owner if managed_online else "System user"

    def check_owner(item: object, *, reviewed_system_object: bool = False) -> None:
        if reviewed_system_object:
            if _exact_owner(
                item,
                label="foreign information-schema object",
            ) != information_schema_owner:
                expected = (
                    "the managed-online catalog owner"
                    if managed_online
                    else "Databricks System user"
                )
                raise RuntimeError(
                    f"foreign information-schema object is not owned by {expected}"
                )
            return
        if owner_check is not None:
            owner_check(item)

    for schema in workspace.schemas.list(catalog, include_browse=True):
        schema_name = _strict_text(getattr(schema, "name", None))
        if not schema_name:
            raise RuntimeError("workspace schema inventory returned an empty name")
        schema_full_name = _full_name(schema, fallback=f"{catalog}.{schema_name}")
        if schema_full_name != f"{catalog}.{schema_name}":
            raise RuntimeError("foreign schema inventory returned an invalid parent identity")
        check_owner(schema, reviewed_system_object=schema_name == "information_schema")
        _assert_privileges(
            workspace,
            securable_type="schema",
            full_name=schema_full_name,
            principal=principal,
            expected=(
                {"USE_SCHEMA"}
                if schema_name == "information_schema" and not managed_online
                else set()
            ),
            expected_source_map=(
                {"USE_SCHEMA": set(_ACCOUNT_USERS_DIRECT)}
                if schema_name == "information_schema" and not managed_online
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
                item_name = _strict_text(getattr(item, "name", None))
                if not item_name:
                    raise RuntimeError(
                        f"workspace {securable_type} inventory returned an empty name"
                    )
                item_full_name = _full_name(
                    item,
                    fallback=f"{schema_full_name}.{item_name}",
                )
                if item_full_name != f"{schema_full_name}.{item_name}":
                    raise RuntimeError(
                        f"foreign {securable_type} inventory returned an invalid parent identity"
                    )
                expected = (
                    {"SELECT"}
                    if securable_type == "table"
                    and schema_name == "information_schema"
                    and item_name in _CATALOG_INFORMATION_SCHEMA_TABLES
                    and not managed_online
                    else set()
                )
                check_owner(
                    item,
                    reviewed_system_object=schema_name == "information_schema",
                )
                _assert_privileges(
                    workspace,
                    securable_type=securable_type,
                    full_name=item_full_name,
                    principal=principal,
                    expected=expected,
                    expected_source_map=(
                        {"SELECT": set(_ACCOUNT_USERS_DIRECT)} if expected else None
                    ),
                )
