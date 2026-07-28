"""Fail-closed serving-endpoint ACL convergence helpers."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any, Literal

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.serving import (
    ServingEndpointAccessControlRequest,
    ServingEndpointPermissionLevel,
)
from tools.databricks.m2m_access_policy import resolve_effective_groups
from tools.databricks.serving_endpoint_identity import (
    is_platform_foundation_endpoint,
)
from tools.databricks.serving_endpoint_legacy_query import (
    endpoint_has_legacy_direct_query_principal,  # noqa: F401 - public re-export
    inspect_legacy_pre_provenance_group,
)
from tools.databricks.serving_query_group_access import (
    assert_claimed_managed_query_group_members,
    ensure_managed_query_group,
    ensure_managed_query_membership,
    managed_query_group_name,
    quiesce_claimed_managed_query_group,
    remove_managed_query_membership,
)
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
)

_LEVEL_RANK = {"CAN_VIEW": 1, "CAN_QUERY": 2, "CAN_MANAGE": 3}
QueryAccessMode = Literal["managed", "direct", "mixed", "none"]


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _direct_level(entry: object) -> str | None:
    direct = [
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
        if getattr(permission, "inherited", None) is not True
    ]
    valid = [level for level in direct if level in _LEVEL_RANK]
    return max(valid, key=_LEVEL_RANK.__getitem__) if valid else None


def _effective_query_or_manage(entry: object) -> str | None:
    effective = [
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
    ]
    query_capable = [level for level in effective if level in {"CAN_QUERY", "CAN_MANAGE"}]
    return max(query_capable, key=_LEVEL_RANK.__getitem__) if query_capable else None


def _all_levels(entry: object) -> set[str]:
    return {
        _text(getattr(permission, "permission_level", None))
        for permission in (getattr(entry, "all_permissions", None) or [])
        if _text(getattr(permission, "permission_level", None))
    }


def _principal_entries(permissions: object, principal: str) -> tuple[object, ...]:
    return tuple(
        entry
        for entry in getattr(permissions, "access_control_list", None) or []
        if str(getattr(entry, "service_principal_name", "") or "") == principal
    )


def _principal_entry(permissions: object, principal: str) -> object | None:
    entries = _principal_entries(permissions, principal)
    if len(entries) > 1:
        raise RuntimeError(f"serving endpoint ACL contains duplicate entries for {principal!r}")
    return entries[0] if entries else None


def _group_entries(permissions: object, group_name: str) -> tuple[object, ...]:
    return tuple(
        entry
        for entry in getattr(permissions, "access_control_list", None) or []
        if str(getattr(entry, "group_name", "") or "").strip() == group_name
    )


def _exact_group_entry(permissions: object, group_name: str) -> object | None:
    entries = _group_entries(permissions, group_name)
    if len(entries) > 1:
        raise RuntimeError(
            f"serving endpoint ACL contains duplicate entries for group {group_name!r}"
        )
    return entries[0] if entries else None


def _service_principal_id(client: Any, application_id: str) -> str:
    escaped = application_id.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in client.service_principals.list(filter=f'applicationId eq "{escaped}"')
        if str(getattr(principal, "application_id", "") or "") == application_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one service principal for application_id {application_id!r}, "
            f"found {len(matches)}"
        )
    principal_id = str(getattr(matches[0], "id", "") or "").strip()
    if not principal_id:
        raise RuntimeError(f"service principal {application_id!r} has no immutable SCIM id")
    return principal_id


def _effective_group_names(
    client: Any,
    *,
    service_principal: str,
    service_principal_id: str | None,
) -> set[str]:
    principal_id = service_principal_id or _service_principal_id(client, service_principal)
    return set(resolve_effective_groups(client, sp_id=principal_id).values())


def _group_query_or_manage(
    permissions: object,
    *,
    effective_group_names: set[str],
) -> tuple[str, str] | None:
    for entry in getattr(permissions, "access_control_list", None) or []:
        group_name = str(getattr(entry, "group_name", "") or "").strip()
        if group_name not in effective_group_names:
            continue
        level = _effective_query_or_manage(entry)
        if level is not None:
            return group_name, level
    return None


def _groups_with_access(
    permissions: object,
    *,
    effective_group_names: set[str],
) -> set[str]:
    return {
        str(getattr(entry, "group_name", "") or "").strip()
        for entry in getattr(permissions, "access_control_list", None) or []
        if str(getattr(entry, "group_name", "") or "").strip() in effective_group_names
        and _all_levels(entry)
    }


def _direct_acl_contract(permissions: object) -> tuple[tuple[str, str, str], ...]:
    contract: list[tuple[str, str, str]] = []
    for entry in getattr(permissions, "access_control_list", None) or []:
        level = _direct_level(entry)
        if level is None:
            continue
        identities = (
            ("user_name", str(getattr(entry, "user_name", "") or "").strip()),
            ("group_name", str(getattr(entry, "group_name", "") or "").strip()),
            (
                "service_principal_name",
                str(getattr(entry, "service_principal_name", "") or "").strip(),
            ),
        )
        present = [(kind, name) for kind, name in identities if name]
        if len(present) != 1:
            raise RuntimeError("serving endpoint ACL contains an ambiguous direct principal")
        kind, name = present[0]
        contract.append((kind, name, level))
    principals = [(kind, name) for kind, name, _level in contract]
    if len(principals) != len(set(principals)):
        raise RuntimeError("serving endpoint ACL contains a duplicated direct principal")
    return tuple(sorted(contract))


def _endpoint_id(client: Any, endpoint_name: str, *, missing_ok: bool) -> str | None:
    try:
        details = client.serving_endpoints.get(endpoint_name)
    except (NotFound, ResourceDoesNotExist):
        if missing_ok:
            return None
        raise
    endpoint_id = str(getattr(details, "id", "") or "").strip()
    if not endpoint_id:
        if missing_ok and is_platform_foundation_endpoint(details):
            return None
        raise RuntimeError(f"serving endpoint {endpoint_name!r} has no immutable id")
    return endpoint_id


def _exact_query_access_mode(
    permissions: object,
    *,
    endpoint_id: str,
    service_principal: str,
    effective_group_names: set[str],
) -> QueryAccessMode:
    entry = _principal_entry(permissions, service_principal)
    direct = (
        entry is not None
        and _direct_level(entry) == "CAN_QUERY"
        and _all_levels(entry) == {"CAN_QUERY"}
        and _effective_query_or_manage(entry) == "CAN_QUERY"
    )
    if entry is not None and not direct:
        raise RuntimeError("query access inspection found a non-exact direct principal ACL")
    managed_group = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=service_principal,
    )
    groups = _groups_with_access(
        permissions,
        effective_group_names=effective_group_names,
    )
    residual_groups = groups.difference({managed_group})
    if residual_groups:
        raise RuntimeError("query access inspection found non-managed effective group access")
    group_entry = _exact_group_entry(permissions, managed_group)
    managed = managed_group in groups
    if managed and (
        _direct_level(group_entry or object()) != "CAN_QUERY"
        or _all_levels(group_entry or object()) != {"CAN_QUERY"}
        or _effective_query_or_manage(group_entry or object()) != "CAN_QUERY"
    ):
        raise RuntimeError("query access inspection found a non-exact managed group ACL")
    if direct and managed:
        return "mixed"
    if direct:
        return "direct"
    if managed:
        return "managed"
    return "none"


def inspect_exact_query_access_mode(
    client: Any,
    *,
    app_name: str,
    endpoint_name: str,
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
    legacy_pinned: bool = False,
) -> QueryAccessMode:
    """Return one principal's exact query-access mode on one endpoint."""

    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    principal_id = str(service_principal_id or "").strip() or _service_principal_id(
        client,
        principal,
    )
    group_names = (
        set(effective_group_names)
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=principal_id,
        )
    )
    endpoint_id = _endpoint_id(client, endpoint_name, missing_ok=False)
    assert endpoint_id is not None
    mode = _exact_query_access_mode(
        client.serving_endpoints.get_permissions(endpoint_id),
        endpoint_id=endpoint_id,
        service_principal=principal,
        effective_group_names=group_names,
    )
    if mode in {"managed", "mixed"}:
        try:
            assert_claimed_managed_query_group_members(
                client,
                app_name=app_name,
                endpoint_id=endpoint_id,
                application_id=principal,
                service_principal_id=principal_id,
                expected_member_ids=(principal_id,),
            )
        except MissingClaimedGroupProvenanceError:
            if not legacy_pinned:
                raise
            state = inspect_legacy_pre_provenance_group(
                client,
                endpoint_id=endpoint_id,
                application_id=principal,
                service_principal_id=principal_id,
            )
            assert state is not None
            if state.member_ids != (principal_id,):
                raise RuntimeError(
                    "legacy-pinned managed serving-query group lacks its exact member"
                ) from None
    return mode


def audit_global_serving_endpoint_access(
    client: Any,
    *,
    app_name: str,
    reviewed_endpoint_names: Collection[str],
    service_principal: str,
    expected_permission_level: Literal["CAN_QUERY", "CAN_MANAGE"] = "CAN_QUERY",
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
    legacy_pinned_endpoint_names: Collection[str] = (),
) -> None:
    """Prove one exact access level on only the reviewed endpoints."""

    reviewed = {str(name).strip() for name in reviewed_endpoint_names if str(name).strip()}
    if not reviewed or len(reviewed) != len(reviewed_endpoint_names):
        raise ValueError("reviewed endpoint names must be non-empty and distinct")
    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    if expected_permission_level not in {"CAN_QUERY", "CAN_MANAGE"}:
        raise ValueError("expected permission level must be CAN_QUERY or CAN_MANAGE")
    legacy_pinned = {
        str(name).strip() for name in legacy_pinned_endpoint_names if str(name).strip()
    }
    if len(legacy_pinned) != len(legacy_pinned_endpoint_names) or not legacy_pinned.issubset(
        reviewed
    ):
        raise ValueError("legacy-pinned endpoint names must be a distinct reviewed subset")
    if legacy_pinned and expected_permission_level != "CAN_QUERY":
        raise ValueError("legacy-pinned endpoints are valid only for CAN_QUERY audits")
    principal_id = str(service_principal_id or "").strip()
    if expected_permission_level == "CAN_QUERY" and not principal_id:
        principal_id = _service_principal_id(client, principal)
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=principal_id or None,
        )
    )
    try:
        visible = list(client.serving_endpoints.list())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"cannot list serving endpoints for customer-serving ACL audit: {exc}"
        ) from exc
    endpoint_ids: dict[str, str] = {}
    for endpoint in visible:
        name = str(
            (endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", ""))
            or ""
        ).strip()
        if not name:
            raise RuntimeError("cannot audit serving ACLs: a visible endpoint has no name")
        if name in endpoint_ids:
            raise RuntimeError(f"cannot audit serving ACLs: duplicate endpoint {name!r}")
        details = client.serving_endpoints.get(name)
        endpoint_id = str(getattr(details, "id", "") or "").strip()
        if not endpoint_id:
            if is_platform_foundation_endpoint(details):
                continue
            raise RuntimeError(f"serving endpoint {name!r} has no immutable id")
        endpoint_ids[name] = endpoint_id
    missing = reviewed.difference(endpoint_ids)
    if missing:
        raise RuntimeError(
            "reviewed serving endpoint(s) are not visible to the admin audit: "
            + ", ".join(sorted(missing))
        )
    for name, endpoint_id in sorted(endpoint_ids.items()):
        try:
            permissions = client.serving_endpoints.get_permissions(endpoint_id)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot inspect serving ACL for {name!r}: {exc}") from exc
        entry = _principal_entry(permissions, principal)
        groups = _groups_with_access(
            permissions,
            effective_group_names=group_names,
        )
        if name in reviewed:
            if expected_permission_level == "CAN_QUERY":
                mode = _exact_query_access_mode(
                    permissions,
                    endpoint_id=endpoint_id,
                    service_principal=principal,
                    effective_group_names=group_names,
                )
                allowed = {"managed", "direct", "mixed"} if name in legacy_pinned else {"managed"}
                if mode not in allowed:
                    raise RuntimeError(
                        f"exact customer-serving CAN_QUERY audit failed for {principal!r} "
                        "on reviewed "
                        f"endpoint {name!r}; require its approved exact query-access mode"
                    )
                if mode in {"managed", "mixed"}:
                    try:
                        assert_claimed_managed_query_group_members(
                            client,
                            app_name=app_name,
                            endpoint_id=endpoint_id,
                            application_id=principal,
                            service_principal_id=principal_id,
                            expected_member_ids=(principal_id,),
                        )
                    except MissingClaimedGroupProvenanceError:
                        if name not in legacy_pinned:
                            raise
                        state = inspect_legacy_pre_provenance_group(
                            client,
                            endpoint_id=endpoint_id,
                            application_id=principal,
                            service_principal_id=principal_id,
                        )
                        assert state is not None
                        if state.member_ids != (principal_id,):
                            raise RuntimeError(
                                "legacy-pinned managed serving-query group lacks "
                                "its exact member"
                            ) from None
            elif (
                _direct_level(entry or object()) != "CAN_MANAGE"
                or _all_levels(entry or object()) != {"CAN_MANAGE"}
                or groups
            ):
                raise RuntimeError(
                    f"exact customer-serving CAN_MANAGE audit failed for {principal!r} "
                    f"on reviewed endpoint {name!r}; remove inherited, group, or broader access"
                )
        elif (entry is not None and _all_levels(entry)) or groups:
            raise RuntimeError(
                f"{principal!r} retains forbidden access to unrelated serving endpoint "
                f"{name!r}; remove direct or effective group access"
            )


def audit_global_no_serving_endpoint_access(
    client: Any,
    *,
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
) -> None:
    """Prove no effective access to customer-created serving endpoints."""

    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=service_principal_id,
        )
    )
    try:
        visible = list(client.serving_endpoints.list())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"cannot list serving endpoints for customer-serving ACL audit: {exc}"
        ) from exc
    for endpoint in visible:
        name = str(
            (endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", ""))
            or ""
        ).strip()
        if not name:
            raise RuntimeError("cannot audit serving ACLs: a visible endpoint has no name")
        details = client.serving_endpoints.get(name)
        endpoint_id = str(getattr(details, "id", "") or "").strip()
        if not endpoint_id:
            if is_platform_foundation_endpoint(details):
                continue
            raise RuntimeError(f"serving endpoint {name!r} has no immutable id")
        permissions = client.serving_endpoints.get_permissions(endpoint_id)
        entry = _principal_entry(permissions, principal)
        groups = _groups_with_access(
            permissions,
            effective_group_names=group_names,
        )
        if (entry is not None and _all_levels(entry)) or groups:
            raise RuntimeError(
                f"{principal!r} retains forbidden access to serving endpoint {name!r}"
            )


def grant_direct_can_query(
    client: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    endpoint_name: str,
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
    assert_single_writer: Callable[[], None] | None = None,
) -> None:
    """Grant CAN_QUERY through an atomically revocable endpoint-bound group."""
    if assert_single_writer is None:
        raise RuntimeError(
            "serving endpoint query activation requires the deployment lease"
        )
    writer = assert_single_writer
    endpoint_id = _endpoint_id(client, endpoint_name, missing_ok=False)
    assert endpoint_id is not None
    principal_id = service_principal_id or _service_principal_id(client, service_principal)
    permissions = client.serving_endpoints.get_permissions(endpoint_id)
    direct_entry = _principal_entry(permissions, service_principal)
    if direct_entry is not None and _all_levels(direct_entry):
        raise RuntimeError(
            f"legacy direct serving ACL remains for {service_principal!r} on "
            f"{endpoint_name!r}; the provider has no atomic principal delete"
        )
    managed_group = managed_query_group_name(
        endpoint_id=endpoint_id, application_id=service_principal
    )
    try:
        ensure_managed_query_group(
            client, app_name=app_name,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            endpoint_id=endpoint_id,
            application_id=service_principal,
            service_principal_id=principal_id,
            assert_single_writer=writer,
        )
        group_entry = _exact_group_entry(permissions, managed_group)
        if _direct_level(group_entry or object()) != "CAN_QUERY" or _all_levels(
            group_entry or object()
        ) != {"CAN_QUERY"}:
            writer()
            client.serving_endpoints.update_permissions(
                endpoint_id,
                access_control_list=[
                    ServingEndpointAccessControlRequest(
                        group_name=managed_group,
                        permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                    )
                ],
            )
        ensure_managed_query_membership(
            client, app_name=app_name,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            endpoint_id=endpoint_id,
            application_id=service_principal,
            service_principal_id=principal_id,
            assert_single_writer=writer,
        )
        permissions = client.serving_endpoints.get_permissions(endpoint_id)
        assert_claimed_managed_query_group_members(
            client,
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=service_principal,
            service_principal_id=principal_id,
            expected_member_ids=(principal_id,),
        )
        group_names = (
            effective_group_names
            if effective_group_names is not None
            else _effective_group_names(
                client,
                service_principal=service_principal,
                service_principal_id=principal_id,
            )
        )
        group_names = {*group_names, managed_group}
        entry = _principal_entry(permissions, service_principal)
        groups = _groups_with_access(
            permissions,
            effective_group_names=group_names,
        )
        group_entry = _exact_group_entry(permissions, managed_group)
        if (
            entry is not None
            or _direct_level(group_entry or object()) != "CAN_QUERY"
            or _all_levels(group_entry or object()) != {"CAN_QUERY"}
            or _effective_query_or_manage(group_entry or object()) != "CAN_QUERY"
            or groups != {managed_group}
        ):
            raise RuntimeError(
                f"exact least-privilege CAN_QUERY postflight failed for "
                f"{service_principal!r} on {endpoint_name!r}; require only its "
                "managed query group"
            )
    except Exception as activation_error:
        try:
            quiesce_claimed_managed_query_group(
                client,
                app_name=app_name,
                endpoint_id=endpoint_id,
                application_id=service_principal,
                service_principal_id=principal_id,
                assert_single_writer=writer,
            )
        except MissingClaimedGroupProvenanceError:
            current = client.serving_endpoints.get_permissions(endpoint_id)
            if _exact_group_entry(current, managed_group) is None:
                raise activation_error from None
            raise RuntimeError(
                "serving endpoint query activation failed without quiescence proof"
            ) from activation_error
        except Exception as quiescence_error:
            raise RuntimeError(
                "serving endpoint query activation failed and quiescence "
                "did not converge"
            ) from quiescence_error
        raise


def revoke_direct_permissions(
    client: Any,
    *,
    app_name: str,
    endpoint_name: str,
    service_principal: str,
    missing_ok: bool = False,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
    assert_single_writer: Callable[[], None] | None = None,
) -> bool:
    """Atomically remove one identity without replacing the endpoint ACL."""

    endpoint_id = _endpoint_id(client, endpoint_name, missing_ok=missing_ok)
    if endpoint_id is None:
        return False
    principal_id = str(service_principal_id or "").strip() or _service_principal_id(
        client,
        service_principal,
    )
    before = client.serving_endpoints.get_permissions(endpoint_id)
    managed_group = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=service_principal,
    )
    group_names = (
        set(effective_group_names)
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=service_principal,
            service_principal_id=principal_id,
        )
    )
    residual_groups = group_names.difference({managed_group})
    remaining = _principal_entry(before, service_principal)
    groups = _groups_with_access(
        before,
        effective_group_names=residual_groups,
    )
    if (remaining is not None and _all_levels(remaining)) or groups:
        raise RuntimeError(
            f"effective serving permission remains for {service_principal!r} on "
            f"{endpoint_name!r}; remove residual direct or inherited group access "
            "before retrying"
        )
    try:
        removed = remove_managed_query_membership(
            client,
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=service_principal,
            service_principal_id=principal_id,
            assert_single_writer=assert_single_writer,
        )
    except MissingClaimedGroupProvenanceError:
        group_entry = _exact_group_entry(before, managed_group)
        if managed_group in group_names and _all_levels(group_entry or object()):
            raise RuntimeError(
                "unclaimed managed serving-query group grants effective access"
            ) from None
        return False
    postflight = client.serving_endpoints.get_permissions(endpoint_id)
    group_names = group_names.difference({managed_group})
    remaining = _principal_entry(postflight, service_principal)
    groups = _groups_with_access(
        postflight,
        effective_group_names=group_names,
    )
    if (remaining is not None and _all_levels(remaining)) or groups:
        raise RuntimeError(
            f"effective serving permission remains for {service_principal!r} on "
            f"{endpoint_name!r}; remove residual direct or inherited group access "
            "before retrying"
        )
    return removed


def converge_exact_direct_can_query(
    client: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    reviewed_endpoint_names: Collection[str],
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
    legacy_pinned_endpoint_names: Collection[str] = (),
    assert_single_writer: Callable[[], None] | None = None,
) -> None:
    """Converge managed CAN_QUERY while preserving reviewed legacy pins read-only."""
    reviewed = {str(name).strip() for name in reviewed_endpoint_names if str(name).strip()}
    if not reviewed or len(reviewed) != len(reviewed_endpoint_names):
        raise ValueError("reviewed endpoint names must be non-empty and distinct")
    legacy_pinned = {
        str(name).strip() for name in legacy_pinned_endpoint_names if str(name).strip()
    }
    if len(legacy_pinned) != len(legacy_pinned_endpoint_names) or not legacy_pinned.issubset(
        reviewed
    ):
        raise ValueError("legacy-pinned endpoint names must be a distinct reviewed subset")
    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=service_principal_id,
        )
    )
    visible: dict[str, str] = {}
    for endpoint in client.serving_endpoints.list():
        name = str(
            (endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", ""))
            or ""
        ).strip()
        if not name or name in visible:
            raise RuntimeError("serving endpoint inventory has a missing or duplicate name")
        endpoint_id = _endpoint_id(client, name, missing_ok=True)
        if endpoint_id is None:
            continue
        visible[name] = endpoint_id
    missing = reviewed.difference(visible)
    if missing:
        raise RuntimeError(
            "reviewed serving endpoint(s) are absent from global inventory: "
            + ", ".join(sorted(missing))
        )
    principal_id = str(service_principal_id or "").strip()
    if not principal_id:
        principal_id = _service_principal_id(client, principal)
    for name in sorted(legacy_pinned):
        mode = inspect_exact_query_access_mode(
            client,
            app_name=app_name,
            endpoint_name=name,
            service_principal=principal,
            service_principal_id=principal_id,
            effective_group_names=group_names,
            legacy_pinned=True,
        )
        if mode not in {"managed", "direct", "mixed"}:
            raise RuntimeError(
                f"legacy-pinned endpoint {name!r} has no exact query access to preserve"
            )
    audited_groups = set(group_names)
    for endpoint_id in visible.values():
        audited_groups.discard(
            managed_query_group_name(
                endpoint_id=endpoint_id,
                application_id=principal,
            )
        )
    for name in sorted(set(visible) - reviewed):
        revoke_direct_permissions(
            client,
            app_name=app_name,
            endpoint_name=name,
            service_principal=principal,
            missing_ok=True,
            service_principal_id=principal_id,
            effective_group_names=group_names,
            assert_single_writer=assert_single_writer,
        )
    for name in sorted(reviewed - legacy_pinned):
        grant_direct_can_query(
            client,
            app_name=app_name,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            endpoint_name=name,
            service_principal=principal,
            service_principal_id=principal_id,
            effective_group_names=group_names,
            assert_single_writer=assert_single_writer,
        )
    audited_groups.update(
        _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=principal_id,
        )
    )
    audit_global_serving_endpoint_access(
        client,
        app_name=app_name,
        reviewed_endpoint_names=reviewed,
        service_principal=principal,
        expected_permission_level="CAN_QUERY",
        service_principal_id=principal_id,
        effective_group_names=audited_groups,
        legacy_pinned_endpoint_names=legacy_pinned,
    )


def revoke_all_direct_permissions(
    client: Any,
    *,
    app_name: str,
    service_principal: str,
    service_principal_id: str | None = None,
    effective_group_names: set[str] | None = None,
    assert_single_writer: Callable[[], None] | None = None,
) -> None:
    """Remove every managed membership and prove no customer-serving access."""

    principal = service_principal.strip()
    if not principal:
        raise ValueError("service principal application ID is required")
    group_names = (
        effective_group_names
        if effective_group_names is not None
        else _effective_group_names(
            client,
            service_principal=principal,
            service_principal_id=service_principal_id,
        )
    )
    principal_id = str(service_principal_id or "").strip()
    if not principal_id:
        principal_id = _service_principal_id(client, principal)
    visible: dict[str, str] = {}
    for endpoint in client.serving_endpoints.list():
        name = str(
            (endpoint.get("name") if isinstance(endpoint, dict) else getattr(endpoint, "name", ""))
            or ""
        ).strip()
        if not name or name in visible:
            raise RuntimeError("serving endpoint inventory has a missing or duplicate name")
        endpoint_id = _endpoint_id(client, name, missing_ok=True)
        if endpoint_id is not None:
            visible[name] = endpoint_id
    audited_groups = set(group_names)
    for endpoint_id in visible.values():
        audited_groups.discard(
            managed_query_group_name(
                endpoint_id=endpoint_id,
                application_id=principal,
            )
        )
    errors: list[str] = []
    for name in sorted(visible):
        try:
            revoke_direct_permissions(
                client,
                app_name=app_name,
                endpoint_name=name,
                service_principal=principal,
                missing_ok=True,
                service_principal_id=principal_id,
                effective_group_names=group_names,
                assert_single_writer=assert_single_writer,
            )
        except Exception as exc:  # noqa: BLE001 - attempt every endpoint revoke
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    try:
        if principal_id:
            audited_groups.update(
                _effective_group_names(
                    client,
                    service_principal=principal,
                    service_principal_id=principal_id,
                )
            )
        audit_global_no_serving_endpoint_access(
            client,
            service_principal=principal,
            service_principal_id=principal_id,
            effective_group_names=audited_groups,
        )
    except Exception as exc:  # noqa: BLE001 - include the complete postflight
        errors.append(f"postflight: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError("customer-serving deny policy did not converge: " + "; ".join(errors))
