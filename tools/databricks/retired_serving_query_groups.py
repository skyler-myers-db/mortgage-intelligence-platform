"""Retire exact endpoint-bound query groups after their endpoint is absent."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import assert_runtime_creator
from tools.databricks.app_gateway_access_mode import inspect_gateway_query_access_mode
from tools.databricks.serving_query_group_access import (
    assert_managed_query_group_members,
    remove_managed_query_membership,
    retire_managed_query_group,
)


def exact_service_principal_scim_id(workspace: Any, *, application_id: str) -> str:
    """Resolve one application ID to its immutable workspace SCIM ID."""

    application = application_id.strip()
    if not application:
        raise ValueError("application ID is required for managed query group retirement")
    escaped = application.replace("\\", "\\\\").replace('"', '\\"')
    matches = [
        principal
        for principal in workspace.service_principals.list(
            filter=f'applicationId eq "{escaped}"',
        )
        if str(getattr(principal, "application_id", "") or "").strip() == application
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one service principal for retirement application {application!r}"
        )
    scim_id = str(getattr(matches[0], "id", "") or "").strip()
    if not scim_id:
        raise RuntimeError("retirement service principal has no immutable SCIM ID")
    return scim_id


def retire_endpoint_query_groups(
    workspace: Any,
    *,
    endpoint_name: str,
    endpoint_id: str,
    principals: tuple[tuple[str, str], ...],
    assert_single_writer: Callable[[], None],
) -> None:
    """Delete exact groups only while their immutable endpoint remains absent."""

    normalized = tuple(
        (application_id.strip(), scim_id.strip())
        for application_id, scim_id in principals
    )
    if any(not application_id or not scim_id for application_id, scim_id in normalized):
        raise ValueError("complete application and SCIM identities are required for group retirement")
    if len({application_id for application_id, _scim_id in normalized}) != len(normalized):
        raise ValueError("managed query group retirement applications must be distinct")
    for application_id, scim_id in normalized:
        try:
            workspace.serving_endpoints.get(endpoint_name)
        except (NotFound, ResourceDoesNotExist):
            pass
        else:
            raise RuntimeError("managed query group cannot retire before its endpoint is absent")
        assert_single_writer()
        retire_managed_query_group(
            workspace,
            endpoint_id=endpoint_id,
            application_id=application_id,
            service_principal_id=scim_id,
            assert_single_writer=assert_single_writer,
        )


def revoke_live_managed_query_access(
    workspace: Any,
    *,
    endpoint_name: str,
    endpoint_id: str,
    endpoint_creator: str,
    application_id: str,
    scim_id: str,
    identity_label: str,
    assert_single_writer: Callable[[], None],
    endpoint_identity: Callable[[Any, str], tuple[str, str]],
) -> str:
    """Atomically empty one exact group while its pinned endpoint remains live."""

    expected_endpoint = (endpoint_id, endpoint_creator)
    if endpoint_identity(workspace, endpoint_name) != expected_endpoint:
        raise RuntimeError("live managed-query endpoint identity drifted before retirement")
    mode = inspect_gateway_query_access_mode(
        workspace,
        endpoint_name=endpoint_name,
        application_id=application_id,
        scim_id=scim_id,
        identity_label=identity_label,
    )
    if endpoint_identity(workspace, endpoint_name) != expected_endpoint:
        raise RuntimeError("live managed-query endpoint identity drifted during access inspection")
    if mode in {"direct", "mixed"}:
        raise RuntimeError(
            f"direct pinned Gateway {identity_label} access cannot be atomically retired"
        )
    if mode == "none":
        return mode
    if endpoint_identity(workspace, endpoint_name) != expected_endpoint:
        raise RuntimeError("live managed-query endpoint identity drifted before membership revoke")
    remove_managed_query_membership(
        workspace,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=scim_id,
        assert_single_writer=assert_single_writer,
    )
    if endpoint_identity(workspace, endpoint_name) != expected_endpoint:
        raise RuntimeError("live managed-query endpoint identity drifted during membership revoke")
    assert_managed_query_group_members(
        workspace,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_member_ids=(),
    )
    post_mode = inspect_gateway_query_access_mode(
        workspace,
        endpoint_name=endpoint_name,
        application_id=application_id,
        scim_id=scim_id,
        identity_label=identity_label,
    )
    if post_mode != "none":
        raise RuntimeError("live managed-query membership retirement did not remove exact access")
    if endpoint_identity(workspace, endpoint_name) != expected_endpoint:
        raise RuntimeError("live managed-query endpoint identity drifted after membership revoke")
    return mode


def delete_pinned_gateway(
    workspace: Any,
    *,
    endpoint: str | None,
    endpoint_id: str | None,
    creator: str | None,
    delete_allowed: bool,
    green_endpoint: str,
    runtime_application_id: str,
    app_principal: str,
    app_principal_id: str,
    verifier_application_id: str | None,
    verifier_scim_id: str | None,
    timeout_s: int,
    assert_single_writer: Callable[[], None],
    endpoint_identity: Callable[[Any, str], tuple[str, str]],
    revoke_app_access: Callable[..., str],
    retire_query_groups: Callable[..., None] = retire_endpoint_query_groups,
) -> None:
    """Delete one signed old Gateway, then retire its exact orphan groups."""

    values = (endpoint, endpoint_id, creator)
    if not any(values):
        return
    if not all(values):
        raise RuntimeError("old Gateway cutover requires its complete pinned identity")
    assert endpoint is not None and endpoint_id is not None and creator is not None
    cleanup_principals = (
        (
            (app_principal, app_principal_id),
            (str(verifier_application_id or ""), str(verifier_scim_id or "")),
        )
        if verifier_application_id or verifier_scim_id
        else ()
    )
    if bool(verifier_application_id) != bool(verifier_scim_id):
        raise ValueError("verifier application and SCIM IDs must be supplied together")
    if not delete_allowed and not verifier_application_id:
        raise ValueError(
            "verifier application and SCIM IDs are required for a nondeletable old Gateway"
        )
    if endpoint == green_endpoint:
        raise RuntimeError("old Gateway endpoint equals green; refusing destructive cutover")
    if delete_allowed:
        assert_runtime_creator(
            creator,
            application_id=runtime_application_id,
            resource=f"pinned old Gateway endpoint {endpoint}",
        )
    try:
        actual = endpoint_identity(workspace, endpoint)
    except (NotFound, ResourceDoesNotExist):
        if cleanup_principals:
            retire_query_groups(
                workspace,
                endpoint_name=endpoint,
                endpoint_id=endpoint_id,
                principals=cleanup_principals,
                assert_single_writer=assert_single_writer,
            )
        return
    if actual != (endpoint_id, creator):
        raise RuntimeError("old Gateway endpoint changed; refusing destructive cutover")
    verifier_mode = "none"
    if verifier_application_id:
        verifier_mode = inspect_gateway_query_access_mode(
            workspace,
            endpoint_name=endpoint,
            application_id=str(verifier_application_id),
            scim_id=str(verifier_scim_id),
            identity_label="verifier",
        )
        if not delete_allowed and verifier_mode in {"direct", "mixed"}:
            raise RuntimeError(
                "direct old Gateway verifier access cannot be atomically revoked and "
                "the pinned endpoint is not runtime-owned"
            )
    access_mode = revoke_app_access(
        workspace,
        endpoint_name=endpoint,
        app_client_id=app_principal,
        app_scim_id=app_principal_id,
        missing_ok=True,
        assert_before_mutation=assert_single_writer,
    )
    if access_mode in {"legacy", "mixed"} and not delete_allowed:
        raise RuntimeError(
            "legacy old Gateway App access cannot be atomically revoked and "
            "the pinned endpoint is not runtime-owned"
        )
    if access_mode not in {"legacy", "mixed"} and endpoint_identity(
        workspace, endpoint
    ) != (endpoint_id, creator):
        raise RuntimeError("old Gateway endpoint changed while revoking its App access")
    if verifier_mode == "managed":
        revoke_live_managed_query_access(
            workspace,
            endpoint_name=endpoint,
            endpoint_id=endpoint_id,
            endpoint_creator=creator,
            application_id=str(verifier_application_id),
            scim_id=str(verifier_scim_id),
            identity_label="verifier",
            assert_single_writer=assert_single_writer,
            endpoint_identity=endpoint_identity,
        )
    if not delete_allowed:
        return
    assert_single_writer()
    if endpoint_identity(workspace, endpoint) != (endpoint_id, creator):
        raise RuntimeError("old Gateway endpoint changed before its pinned deletion")
    workspace.serving_endpoints.delete(endpoint)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            workspace.serving_endpoints.get(endpoint)
        except (NotFound, ResourceDoesNotExist):
            if cleanup_principals:
                retire_query_groups(
                    workspace,
                    endpoint_name=endpoint,
                    endpoint_id=endpoint_id,
                    principals=cleanup_principals,
                    assert_single_writer=assert_single_writer,
                )
            return
        time.sleep(5)
    raise TimeoutError("old Gateway endpoint remained after governed cleanup")


def retire_pinned_supervisor(
    workspace: Any,
    *,
    canonical_name: str,
    old_id: str | None,
    old_endpoint: str | None,
    old_endpoint_id: str | None,
    old_creator: str | None,
    old_create_time: str | None,
    app_principal: str,
    app_principal_id: str,
    proxy_application_id: str | None,
    proxy_scim_id: str | None,
    cleanup_enabled: bool,
    timeout_s: int,
    assert_single_writer: Callable[[], None],
    agent_by_id: Callable[[str], dict[str, Any] | None],
    endpoint_identity: Callable[[Any, str], tuple[str, str]],
    revoke_app_access: Callable[..., str],
    delete_agent: Callable[[list[str]], object],
    retire_query_groups: Callable[..., None] = retire_endpoint_query_groups,
) -> None:
    """Retire one signed Supervisor tuple and its endpoint-bound query groups."""

    if not old_id:
        return
    if not all([old_endpoint, old_endpoint_id, old_creator, old_create_time]):
        raise RuntimeError("old Supervisor cutover requires its complete pinned identity")
    assert old_endpoint and old_endpoint_id and old_creator and old_create_time
    principals = (
        (
            (app_principal, app_principal_id),
            (str(proxy_application_id), str(proxy_scim_id)),
        )
        if cleanup_enabled
        else ()
    )

    def cleanup() -> None:
        if principals:
            retire_query_groups(
                workspace,
                endpoint_name=old_endpoint,
                endpoint_id=old_endpoint_id,
                principals=principals,
                assert_single_writer=assert_single_writer,
            )

    try:
        actual_endpoint = endpoint_identity(workspace, old_endpoint)
    except (NotFound, ResourceDoesNotExist) as exc:
        if agent_by_id(old_id) is not None:
            raise RuntimeError("old Supervisor still exists without its pinned endpoint") from exc
        cleanup()
        return
    if actual_endpoint != (old_endpoint_id, old_creator):
        raise RuntimeError("old managed endpoint changed; refusing destructive cutover")
    old = agent_by_id(old_id)
    if old is not None:
        pinned = (
            str(old.get("display_name") or ""),
            str(old.get("endpoint_name") or ""),
            str(old.get("creator") or ""),
            str(old.get("create_time") or ""),
        )
        if pinned != (canonical_name, old_endpoint, old_creator, old_create_time):
            raise RuntimeError(
                "old Supervisor changed after provisioning; refusing destructive cutover"
            )
    old_access_mode = revoke_app_access(
        workspace,
        endpoint_name=old_endpoint,
        app_client_id=app_principal,
        app_scim_id=app_principal_id,
        missing_ok=old is None,
        assert_before_mutation=assert_single_writer,
    )
    if cleanup_enabled:
        proxy_mode = inspect_gateway_query_access_mode(
            workspace,
            endpoint_name=old_endpoint,
            application_id=str(proxy_application_id),
            scim_id=str(proxy_scim_id),
            identity_label="proxy",
        )
        if proxy_mode == "managed":
            revoke_live_managed_query_access(
                workspace,
                endpoint_name=old_endpoint,
                endpoint_id=old_endpoint_id,
                endpoint_creator=old_creator,
                application_id=str(proxy_application_id),
                scim_id=str(proxy_scim_id),
                identity_label="proxy",
                assert_single_writer=assert_single_writer,
                endpoint_identity=endpoint_identity,
            )
    if old is not None:
        if old_access_mode not in {"legacy", "mixed"}:
            if endpoint_identity(workspace, old_endpoint) != (
                old_endpoint_id,
                old_creator,
            ):
                raise RuntimeError("old managed endpoint changed while revoking its App bypass")
            if agent_by_id(old_id) != old:
                raise RuntimeError("old Supervisor changed while revoking its App bypass")
        assert_single_writer()
        if endpoint_identity(workspace, old_endpoint) != (old_endpoint_id, old_creator):
            raise RuntimeError("old managed endpoint changed before Supervisor deletion")
        if agent_by_id(old_id) != old:
            raise RuntimeError("old Supervisor changed before its pinned deletion")
        delete_agent(
            [
                "supervisor-agents",
                "delete-supervisor-agent",
                f"supervisor-agents/{old_id}",
            ]
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if agent_by_id(old_id) is None:
                break
            time.sleep(5)
        else:
            raise TimeoutError("old Supervisor was not deleted after the governed cutover")
    try:
        orphan_identity = endpoint_identity(workspace, old_endpoint)
    except (NotFound, ResourceDoesNotExist):
        cleanup()
        return
    if orphan_identity != (old_endpoint_id, old_creator):
        raise RuntimeError("old managed endpoint identity changed; refusing orphan cleanup")
    assert_single_writer()
    if endpoint_identity(workspace, old_endpoint) != (old_endpoint_id, old_creator):
        raise RuntimeError("old managed endpoint changed before orphan deletion")
    workspace.serving_endpoints.delete(old_endpoint)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            workspace.serving_endpoints.get(old_endpoint)
        except (NotFound, ResourceDoesNotExist):
            cleanup()
            return
        time.sleep(5)
    raise TimeoutError("old managed Supervisor endpoint remained after explicit cleanup")
