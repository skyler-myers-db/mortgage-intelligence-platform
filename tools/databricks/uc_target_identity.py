"""Exact workspace/account target identity resolution for UC governance."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tools.databricks.uc_owner_policy import TargetServicePrincipal


def _exact_matches(
    items: Iterable[object],
    *,
    application_id: str,
    plane: str,
) -> list[object]:
    if not application_id or application_id != application_id.strip():
        raise RuntimeError("agent-runtime application id is not canonical")
    matches: list[object] = []
    for item in items:
        raw_application_id = getattr(item, "application_id", None)
        if (
            not isinstance(raw_application_id, str)
            or not raw_application_id
            or raw_application_id != raw_application_id.strip()
        ):
            raise RuntimeError(
                f"agent-runtime {plane} identity returned a noncanonical application id"
            )
        if (
            raw_application_id != application_id
            and raw_application_id.casefold() == application_id.casefold()
        ):
            raise RuntimeError(
                f"agent-runtime {plane} identity returned a case-variant application id"
            )
        if raw_application_id == application_id:
            matches.append(item)
    return matches


def _identity_fields(
    principal: object,
    *,
    plane: str,
) -> tuple[str, str]:
    scim_id = getattr(principal, "id", None)
    display_name = getattr(principal, "display_name", "") or ""
    if (
        not isinstance(scim_id, str)
        or not scim_id
        or scim_id != scim_id.strip()
        or not isinstance(display_name, str)
        or display_name != display_name.strip()
        or getattr(principal, "active", None) is not True
    ):
        raise RuntimeError(
            f"agent-runtime {plane} identity is noncanonical or inactive"
        )
    return scim_id, display_name


def workspace_target_identity(
    workspace: Any,
    *,
    application_id: str,
) -> TargetServicePrincipal:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = _exact_matches(
        workspace.service_principals.list(
            filter=f'applicationId eq "{escaped}"'
        ),
        application_id=application_id,
        plane="workspace",
    )
    if len(matches) != 1:
        raise RuntimeError(
            "agent-runtime identity did not resolve exactly once in workspace SCIM"
        )
    scim_id, display_name = _identity_fields(matches[0], plane="workspace")
    return TargetServicePrincipal(
        application_id=application_id,
        scim_id=scim_id,
        display_name=display_name,
    )


def account_target_identity(
    account: Any,
    *,
    application_id: str,
) -> tuple[str, str]:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = _exact_matches(
        account.service_principals.list(
            filter=f'applicationId eq "{escaped}"'
        ),
        application_id=application_id,
        plane="account",
    )
    if len(matches) != 1:
        raise RuntimeError(
            "agent-runtime identity did not resolve exactly once in account SCIM"
        )
    return _identity_fields(matches[0], plane="account")


__all__ = ["account_target_identity", "workspace_target_identity"]
