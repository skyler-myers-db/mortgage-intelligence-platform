"""Name the signed App deployment lease holder in the lease-root ACL.

A human deployer's SCIM ``/Me`` userName is an email, and its directory ACL
entry is ``user_name``. A service-principal deployer (the GitHub OIDC CI
deployer) reports its application id as ``/Me`` userName, and Databricks
records its ACL entry as ``service_principal_name``. A non-email holder must
resolve, by exact applicationId lookup, to exactly one workspace service
principal; zero or several matches fail closed rather than falling back to a
``user_name`` entry that would never converge.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.workspace import (
    WorkspaceObjectAccessControlRequest,
    WorkspaceObjectPermissionLevel,
)

USER_FIELD = "user_name"
SERVICE_PRINCIPAL_FIELD = "service_principal_name"
_GROUP_FIELD = "group_name"


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def holder_acl_field(workspace: Any, holder: str) -> str:
    """Return the ACL principal field that names ``holder``, or fail closed."""

    if not holder or holder != holder.strip():
        raise RuntimeError("App deployment lease holder identity is invalid")
    if "@" in holder:
        return USER_FIELD
    escaped = holder.replace("\\", "\\\\").replace('"', '\\"')
    try:
        principals = list(
            workspace.service_principals.list(filter=f'applicationId eq "{escaped}"')
        )
    except Exception as exc:
        raise RuntimeError(
            "App deployment lease holder service principal could not be resolved"
        ) from exc
    matches = [item for item in principals if _field(item, "application_id") == holder]
    if len(matches) != 1:
        raise RuntimeError(
            "App deployment lease holder must resolve to exactly one workspace "
            "service principal"
        )
    return SERVICE_PRINCIPAL_FIELD


def is_holder_entry(holder_field: str, holder: str, entry: object) -> bool:
    """Whether one ACL entry names exactly the holder, in its resolved field only."""

    names = {name: _field(entry, name) for name in (USER_FIELD, SERVICE_PRINCIPAL_FIELD, _GROUP_FIELD)}
    return names[holder_field] == holder and not any(
        value for name, value in names.items() if name != holder_field
    )


def holder_manage_request(workspace: Any, holder: str) -> WorkspaceObjectAccessControlRequest:
    """Build the holder's exact CAN_MANAGE entry in its resolved principal field."""

    level = WorkspaceObjectPermissionLevel.CAN_MANAGE
    if holder_acl_field(workspace, holder) == SERVICE_PRINCIPAL_FIELD:
        return WorkspaceObjectAccessControlRequest(
            service_principal_name=holder, permission_level=level
        )
    return WorkspaceObjectAccessControlRequest(user_name=holder, permission_level=level)
