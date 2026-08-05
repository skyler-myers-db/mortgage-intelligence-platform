"""Administration-boundary proof for managed serving-query groups."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tools.databricks.serving_query_group_access import (
    ManagedQueryGroupState,
    _hydrated_group,
    inspect_claimed_managed_query_group,
    inspect_managed_query_group,
)

_MAX_EFFECTIVE_GROUPS = 1000


def _assert_group_administration_state(
    client: Any,
    *,
    state: ManagedQueryGroupState,
    principal_id: str,
    authoritative_effective_groups: Mapping[str, str],
) -> ManagedQueryGroupState:
    if state.member_ids not in {(), (principal_id,)}:
        raise RuntimeError(
            "managed serving-query group membership is neither active nor safely retired"
        )
    effective_groups: dict[str, str] = {}
    effective_ids: set[str] = set()
    effective_names: set[str] = set()
    if len(authoritative_effective_groups) > _MAX_EFFECTIVE_GROUPS:
        raise RuntimeError("authoritative managed-group membership snapshot is unbounded")
    for raw_group_id, raw_group_name in authoritative_effective_groups.items():
        group_id = str(raw_group_id or "").strip()
        group_name = str(raw_group_name or "").strip()
        canonical_id = group_id.casefold()
        canonical_name = group_name.casefold()
        if (
            not group_id
            or not group_name
            or canonical_id in effective_ids
            or canonical_name in effective_names
        ):
            raise RuntimeError(
                "authoritative managed-group membership snapshot is ambiguous"
            )
        effective_groups[group_id] = group_name
        effective_ids.add(canonical_id)
        effective_names.add(canonical_name)
    group = _hydrated_group(client, group_id=state.contract.id)
    meta = getattr(group, "meta", None)
    resource_type = str(
        getattr(getattr(meta, "resource_type", None), "value", None)
        or getattr(meta, "resource_type", "")
        or ""
    ).strip()
    if resource_type != "WorkspaceGroup":
        raise RuntimeError(
            "managed serving-query group is not bound to workspace-local SCIM"
        )
    if any(name.casefold() == "admins" for name in effective_groups.values()):
        raise RuntimeError(
            "managed serving-query member has workspace-administration authority"
        )
    return state


def assert_managed_query_group_administration_isolated(
    client: Any,
    *,
    app_name: str,
    account_id: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    authoritative_effective_groups: Mapping[str, str],
) -> ManagedQueryGroupState | None:
    """Prove the signed group identity cannot administer its bound group."""

    account = account_id.strip()
    principal_id = service_principal_id.strip()
    principal = application_id.strip()
    if not account or not endpoint_id.strip() or not principal or not principal_id:
        raise ValueError(
            "account, endpoint, application, and service-principal IDs are required"
        )
    state = inspect_claimed_managed_query_group(
        client,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=principal,
        service_principal_id=principal_id,
        missing_ok=True,
    )
    if state is None:
        return None
    return _assert_group_administration_state(
        client,
        state=state,
        principal_id=principal_id,
        authoritative_effective_groups=authoritative_effective_groups,
    )


def assert_legacy_managed_query_group_administration_isolated(
    client: Any,
    *,
    account_id: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    authoritative_effective_groups: Mapping[str, str],
) -> ManagedQueryGroupState | None:
    """Govern an explicitly admitted deterministic pre-provenance group."""

    if (
        not account_id.strip()
        or not endpoint_id.strip()
        or not application_id.strip()
        or not service_principal_id.strip()
    ):
        raise ValueError(
            "account, endpoint, application, and service-principal IDs are required"
        )
    state = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=True,
    )
    if state is None:
        return None
    return _assert_group_administration_state(
        client,
        state=state,
        principal_id=service_principal_id,
        authoritative_effective_groups=authoritative_effective_groups,
    )
