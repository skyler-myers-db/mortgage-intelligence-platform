"""Signed-group-aware inspection for legacy serving query principals."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema
from tools.databricks.serving_query_group_access import (
    ManagedQueryGroupState,
    inspect_claimed_managed_query_group,
    inspect_managed_query_group,
    managed_query_group_external_id,
    managed_query_group_name,
)
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
)

_PATCH_SCHEMA = PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP


class LegacyPreProvenanceGroupContractError(RuntimeError):
    """The explicitly admitted v1 group is absent or identity-drifted."""


def inspect_legacy_pre_provenance_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    missing_ok: bool = False,
) -> ManagedQueryGroupState | None:
    """Inspect only the exact deterministic v1 group admitted for migration."""

    try:
        state = inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=missing_ok,
            expected_external_id=managed_query_group_external_id(
                endpoint_id=endpoint_id,
                application_id=application_id,
            ),
        )
    except RuntimeError as exc:
        raise LegacyPreProvenanceGroupContractError(
            "legacy managed serving-query group contract drifted"
        ) from exc
    if state is None:
        return None
    if set(state.member_ids) not in ({service_principal_id}, set()):
        raise RuntimeError(
            "legacy managed serving-query group contains an unrelated member"
        )
    return state


def remove_legacy_pre_provenance_membership(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None],
) -> bool:
    """Empty only an exact deterministic v1 group under a held writer."""

    principal_id = service_principal_id.strip()
    if not principal_id or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
        for character in principal_id
    ):
        raise RuntimeError("legacy managed serving-query SCIM ID is unsafe")
    state = inspect_legacy_pre_provenance_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        missing_ok=True,
    )
    if state is None or not state.member_ids:
        return False
    if state.member_ids != (principal_id,):
        raise RuntimeError(
            "legacy managed serving-query group lacks its exact member"
        )
    assert_single_writer()
    client.groups.patch(
        id=state.contract.id,
        operations=[
            Patch(
                op=PatchOp.REMOVE,
                path=f'members[value eq "{principal_id}"]',
            )
        ],
        schemas=[_PATCH_SCHEMA],
    )
    postflight = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_group_id=state.contract.id,
        expected_external_id=state.contract.external_id,
    )
    assert postflight is not None
    if postflight.member_ids:
        raise RuntimeError(
            "legacy managed serving-query group did not converge to empty"
        )
    return True


def endpoint_has_legacy_direct_query_principal(
    client: Any,
    *,
    app_name: str,
    endpoint_name: str,
    runtime_manager_application_id: str,
    approved_managed_query_application_ids: Collection[str] = (),
    approved_empty_managed_query_application_ids: Collection[str] = (),
) -> bool:
    """Inspect whether an endpoint retains a pre-managed-group query principal."""

    # The import is intentionally lazy: serving_endpoint_acl re-exports this
    # focused inspector while retaining the shared ACL parsing primitives.
    from tools.databricks import serving_endpoint_acl as acl

    runtime_manager = runtime_manager_application_id.strip()
    if not runtime_manager:
        raise ValueError("runtime manager application ID is required")
    endpoint_id = acl._endpoint_id(client, endpoint_name, missing_ok=False)
    assert endpoint_id is not None
    approved_applications = tuple(
        str(value).strip() for value in approved_managed_query_application_ids
    )
    approved_empty_applications = tuple(
        str(value).strip() for value in approved_empty_managed_query_application_ids
    )
    if (
        any(not value for value in approved_applications)
        or any(not value for value in approved_empty_applications)
        or len(approved_applications) != len(set(approved_applications))
        or len(approved_empty_applications) != len(set(approved_empty_applications))
        or set(approved_applications).intersection(approved_empty_applications)
    ):
        raise ValueError(
            "approved managed-query application IDs must be non-empty and distinct"
        )
    reviewed_applications = (*approved_applications, *approved_empty_applications)
    permissions = client.serving_endpoints.get_permissions(endpoint_id)
    acl._direct_acl_contract(permissions)
    for entry in getattr(permissions, "access_control_list", None) or []:
        principal = str(
            getattr(entry, "service_principal_name", "") or ""
        ).strip()
        group = str(getattr(entry, "group_name", "") or "").strip()
        levels = acl._all_levels(entry)
        if principal == runtime_manager and levels == {"CAN_MANAGE"}:
            continue
        if group.casefold() == "admins" and levels == {"CAN_MANAGE"}:
            continue
        approved_application = next(
            (
                application_id
                for application_id in reviewed_applications
                if group
                == managed_query_group_name(
                    endpoint_id=endpoint_id,
                    application_id=application_id,
                )
            ),
            None,
        )
        if (
            approved_application is not None
            and acl._direct_level(entry) == "CAN_QUERY"
            and levels == {"CAN_QUERY"}
        ):
            principal_id = acl._service_principal_id(client, approved_application)
            try:
                state = inspect_claimed_managed_query_group(
                    client,
                    app_name=app_name,
                    endpoint_id=endpoint_id,
                    application_id=approved_application,
                    service_principal_id=principal_id,
                )
            except MissingClaimedGroupProvenanceError as proof_error:
                try:
                    state = inspect_legacy_pre_provenance_group(
                        client,
                        endpoint_id=endpoint_id,
                        application_id=approved_application,
                        service_principal_id=principal_id,
                    )
                except LegacyPreProvenanceGroupContractError as legacy_error:
                    raise proof_error from legacy_error
                assert state is not None
                return True
            assert state is not None
            if approved_application in approved_empty_applications:
                if state.member_ids:
                    raise RuntimeError(
                        "approved empty managed serving-query group retains a member"
                    )
                continue
            if set(state.member_ids) not in ({principal_id}, set()):
                raise RuntimeError(
                    "approved managed serving-query group contains an unrelated member"
                )
            continue
        if acl._direct_level(entry) in {"CAN_QUERY", "CAN_MANAGE"}:
            return True
    return False
