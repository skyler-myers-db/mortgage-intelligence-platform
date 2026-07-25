"""Exact mutation phase for attested historical agent endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.historical_agent_endpoint_types import (
    QueryGroupPrincipals,
    ReviewedSupervisor,
    RuntimeEndpointInventory,
    SupervisorCleanupProof,
)
from tools.databricks.serving_query_group_access import (
    ManagedQueryGroupState,
    inspect_managed_query_group,
)


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


class CleanupJournal(Protocol):
    def read(self) -> SupervisorCleanupProof | None: ...

    def proof_for(
        self,
        supervisor: ReviewedSupervisor,
        *,
        runtime_application_id: str,
    ) -> SupervisorCleanupProof: ...

    def stage(self, proof: SupervisorCleanupProof) -> None: ...

    def clear(
        self,
        proof: SupervisorCleanupProof,
        *,
        assert_resources_absent: Callable[[], None],
    ) -> None: ...


def _item_text(value: object, field: str) -> str:
    if isinstance(value, dict):
        return str(value.get(field) or "").strip()
    return str(getattr(value, field, "") or "").strip()


def _permission_level(permission: object) -> str:
    value = getattr(permission, "permission_level", None)
    return str(getattr(value, "value", value) or "").strip()


def _exact_group_acl_binding(
    permissions: object,
    *,
    group_name: str,
) -> bool:
    entries = [
        entry
        for entry in (getattr(permissions, "access_control_list", None) or [])
        if _item_text(entry, "group_name") == group_name
    ]
    if len(entries) > 1:
        raise RuntimeError("historical endpoint ACL duplicates a managed query group")
    if not entries:
        return False
    levels = {
        _permission_level(permission)
        for permission in (getattr(entries[0], "all_permissions", None) or [])
        if _permission_level(permission)
    }
    direct = {
        _permission_level(permission)
        for permission in (getattr(entries[0], "all_permissions", None) or [])
        if getattr(permission, "inherited", None) is not True and _permission_level(permission)
    }
    if levels != {"CAN_QUERY"} or direct != {"CAN_QUERY"}:
        raise RuntimeError("historical endpoint managed query ACL is not exact CAN_QUERY")
    return True


def _capture_retirable_query_groups(
    client: Any,
    *,
    endpoint_id: str,
    principals: tuple[tuple[str, str], ...],
    permissions: object,
) -> tuple[tuple[str, str, ManagedQueryGroupState], ...]:
    groups: list[tuple[str, str, ManagedQueryGroupState]] = []
    for application_id, scim_id in principals:
        state = inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=True,
        )
        if state is None:
            continue
        bound = _exact_group_acl_binding(
            permissions,
            group_name=state.contract.name,
        )
        if not bound:
            raise RuntimeError(
                "historical managed query group is not bound to the exact live endpoint ACL"
            )
        if set(state.member_ids) not in ({scim_id}, set()):
            raise RuntimeError(
                "historical endpoint group contains an unrelated member before deletion"
            )
        groups.append((application_id, scim_id, state))
    return tuple(groups)


def _retire_live_endpoint_query_groups(
    client: Any,
    *,
    endpoint_name: str,
    endpoint_id: str,
    endpoint_creator: str,
    principals: tuple[tuple[str, str], ...],
    assert_single_writer: Callable[[], None],
) -> None:
    """Delete exact groups before their attested endpoint can become absent.

    This ordering removes the crash window that previously required reconstructing
    orphan candidates from hash-shaped names. A retry may observe an already-absent
    group while the endpoint remains live; that state is idempotent and authorizes
    no other group deletion.
    """

    details = client.serving_endpoints.get(endpoint_name)
    if (
        _item_text(details, "id"),
        _item_text(details, "creator"),
    ) != (endpoint_id, endpoint_creator):
        raise RuntimeError("historical endpoint changed before exact group retirement")
    permissions = client.serving_endpoints.get_permissions(endpoint_id)
    groups = _capture_retirable_query_groups(
        client,
        endpoint_id=endpoint_id,
        principals=principals,
        permissions=permissions,
    )
    expected_endpoint = (endpoint_id, endpoint_creator)
    for application_id, scim_id, expected_state in groups:
        current_endpoint = client.serving_endpoints.get(endpoint_name)
        if (
            _item_text(current_endpoint, "id"),
            _item_text(current_endpoint, "creator"),
        ) != expected_endpoint:
            raise RuntimeError("historical endpoint changed during exact group retirement")
        current = inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        if current != expected_state or set(current.member_ids) not in (
            {scim_id},
            set(),
        ):
            raise RuntimeError("historical managed query group changed before deletion")
        if not _exact_group_acl_binding(
            client.serving_endpoints.get_permissions(endpoint_id),
            group_name=expected_state.contract.name,
        ):
            raise RuntimeError("historical managed query ACL changed before group deletion")
        assert_single_writer()
        current_endpoint = client.serving_endpoints.get(endpoint_name)
        current = inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        if (
            (
                _item_text(current_endpoint, "id"),
                _item_text(current_endpoint, "creator"),
            )
            != expected_endpoint
            or current != expected_state
            or not _exact_group_acl_binding(
                client.serving_endpoints.get_permissions(endpoint_id),
                group_name=expected_state.contract.name,
            )
        ):
            raise RuntimeError("historical endpoint, group, or ACL changed at deletion boundary")
        try:
            client.groups.delete(expected_state.contract.id)
        except Exception:  # noqa: BLE001 - prove whether an ambiguous delete committed
            if (
                inspect_managed_query_group(
                    client,
                    endpoint_id=endpoint_id,
                    application_id=application_id,
                    missing_ok=True,
                )
                is not None
            ):
                raise
        if (
            inspect_managed_query_group(
                client,
                endpoint_id=endpoint_id,
                application_id=application_id,
                missing_ok=True,
            )
            is not None
        ):
            raise RuntimeError("historical managed query group retirement did not converge")
        current_endpoint = client.serving_endpoints.get(endpoint_name)
        if (
            _item_text(current_endpoint, "id"),
            _item_text(current_endpoint, "creator"),
        ) != expected_endpoint:
            raise RuntimeError("historical endpoint changed after exact group retirement")


def _supervisor_by_id(client: Any, supervisor_id: str) -> dict[str, Any] | None:
    try:
        payload = client.api_client.do(
            "GET",
            f"/api/2.1/supervisor-agents/{quote(supervisor_id, safe='')}",
        )
    except (NotFound, ResourceDoesNotExist):
        return None
    if not isinstance(payload, dict):
        raise RuntimeError("Supervisor metadata is malformed")
    return payload


def _wait_endpoint_absent(
    client: Any,
    name: str,
    *,
    timeout_s: int,
    sleep: Callable[[float], None],
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            client.serving_endpoints.get(name)
        except (NotFound, ResourceDoesNotExist):
            return
        sleep(2)
    raise TimeoutError(f"historical serving endpoint {name!r} remained after deletion")


def _wait_supervisor_absent(
    client: Any,
    supervisor_id: str,
    *,
    timeout_s: int,
    sleep: Callable[[float], None],
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _supervisor_by_id(client, supervisor_id) is None:
            return
        sleep(2)
    raise TimeoutError(f"historical Supervisor {supervisor_id!r} remained after deletion")


def _delete_endpoint_exact(
    client: Any,
    *,
    name: str,
    endpoint_id: str,
    creator: str,
    assert_single_writer: Callable[[], None],
    timeout_s: int,
    sleep: Callable[[float], None],
) -> None:
    try:
        details = client.serving_endpoints.get(name)
    except (NotFound, ResourceDoesNotExist):
        return
    if (
        _text(getattr(details, "id", None)),
        _text(getattr(details, "creator", None)),
    ) != (endpoint_id, creator):
        raise RuntimeError("historical endpoint changed before exact deletion")
    assert_single_writer()
    try:
        boundary = client.serving_endpoints.get(name)
    except (NotFound, ResourceDoesNotExist):
        return
    if (
        _text(getattr(boundary, "id", None)),
        _text(getattr(boundary, "creator", None)),
    ) != (endpoint_id, creator):
        raise RuntimeError("historical endpoint changed at exact deletion boundary")
    try:
        client.serving_endpoints.delete(name)
    except Exception:  # noqa: BLE001 - distinguish committed mutation from failure
        try:
            client.serving_endpoints.get(name)
        except (NotFound, ResourceDoesNotExist):
            return
        raise
    _wait_endpoint_absent(client, name, timeout_s=timeout_s, sleep=sleep)


def _supervisor_exact(
    client: Any,
    proof: SupervisorCleanupProof,
) -> dict[str, Any] | None:
    details = _supervisor_by_id(client, proof.supervisor_id)
    if details is None:
        return None
    if (
        _item_text(details, "supervisor_agent_id"),
        _item_text(details, "endpoint_name"),
        _item_text(details, "creator"),
    ) != (proof.supervisor_id, proof.endpoint, proof.creator):
        raise RuntimeError("historical Supervisor changed from its cleanup proof")
    return details


def _endpoint_exact_or_absent(
    client: Any,
    proof: SupervisorCleanupProof,
) -> bool:
    try:
        details = client.serving_endpoints.get(proof.endpoint)
    except (NotFound, ResourceDoesNotExist):
        return False
    if (
        _item_text(details, "id"),
        _item_text(details, "creator"),
    ) != (proof.endpoint_id, proof.creator):
        raise RuntimeError("historical Supervisor endpoint changed from its cleanup proof")
    return True


def _assert_supervisor_resources_absent(
    client: Any,
    proof: SupervisorCleanupProof,
) -> None:
    if _supervisor_by_id(client, proof.supervisor_id) is not None:
        raise RuntimeError("historical Supervisor remained before cleanup-journal clear")
    try:
        client.serving_endpoints.get(proof.endpoint)
    except (NotFound, ResourceDoesNotExist):
        return
    raise RuntimeError("historical Supervisor endpoint remained before cleanup-journal clear")


def _cleanup_supervisor_proof(
    client: Any,
    proof: SupervisorCleanupProof,
    *,
    assert_single_writer: Callable[[], None],
    query_principals: QueryGroupPrincipals,
    cleanup_journal: CleanupJournal,
    timeout_s: int,
    sleep: Callable[[float], None],
    stage: bool,
) -> None:
    """Converge one journaled exact Supervisor tuple across process crashes."""

    supervisor_live = _supervisor_exact(client, proof) is not None
    endpoint_live = _endpoint_exact_or_absent(client, proof)
    if stage:
        pending = cleanup_journal.read()
        if not supervisor_live and pending != proof:
            raise RuntimeError(
                "historical Supervisor disappeared before its cleanup proof was staged"
            )
        cleanup_journal.stage(proof)
    if endpoint_live:
        _retire_live_endpoint_query_groups(
            client,
            endpoint_name=proof.endpoint,
            endpoint_id=proof.endpoint_id,
            endpoint_creator=proof.creator,
            principals=(
                (
                    query_principals.app_application_id,
                    query_principals.app_scim_id,
                ),
                (
                    query_principals.proxy_application_id,
                    query_principals.proxy_scim_id,
                ),
            ),
            assert_single_writer=assert_single_writer,
        )
    if supervisor_live:
        assert_single_writer()
        if _supervisor_exact(client, proof) is None:
            raise RuntimeError("historical Supervisor disappeared at deletion boundary")
        _endpoint_exact_or_absent(client, proof)
        try:
            client.api_client.do(
                "DELETE",
                f"/api/2.1/supervisor-agents/{quote(proof.supervisor_id, safe='')}",
            )
        except Exception:  # noqa: BLE001 - prove whether a timed-out delete committed
            if _supervisor_by_id(client, proof.supervisor_id) is not None:
                raise
        _wait_supervisor_absent(
            client,
            proof.supervisor_id,
            timeout_s=timeout_s,
            sleep=sleep,
        )
    if _endpoint_exact_or_absent(client, proof):
        _delete_endpoint_exact(
            client,
            name=proof.endpoint,
            endpoint_id=proof.endpoint_id,
            creator=proof.creator,
            assert_single_writer=assert_single_writer,
            timeout_s=timeout_s,
            sleep=sleep,
        )
    _assert_supervisor_resources_absent(client, proof)
    cleanup_journal.clear(
        proof,
        assert_resources_absent=lambda: _assert_supervisor_resources_absent(
            client,
            proof,
        ),
    )


def cleanup_runtime_endpoints(
    client: Any,
    inventory: RuntimeEndpointInventory,
    *,
    assert_single_writer: Callable[[], None],
    query_principals: QueryGroupPrincipals,
    timeout_s: int,
    inventory_again: Callable[[], RuntimeEndpointInventory],
    cleanup_journal: CleanupJournal,
    sleep: Callable[[float], None] = time.sleep,
) -> RuntimeEndpointInventory:
    """Retire only unpreserved exact tuples, then return a fresh attested set."""

    if timeout_s <= 0:
        raise ValueError("historical endpoint cleanup timeout must be positive")
    query_principals.validate()
    if inventory_again() != inventory:
        raise RuntimeError("historical endpoint inventory changed before cleanup")
    if inventory.pending_supervisor_cleanup is not None:
        _cleanup_supervisor_proof(
            client,
            inventory.pending_supervisor_cleanup,
            assert_single_writer=assert_single_writer,
            query_principals=query_principals,
            cleanup_journal=cleanup_journal,
            timeout_s=timeout_s,
            sleep=sleep,
            stage=True,
        )
        inventory = inventory_again()
        if inventory.pending_supervisor_cleanup is not None:
            raise RuntimeError(
                "historical Supervisor cleanup journal remained after exact recovery"
            )
    for gateway in inventory.gateways:
        if gateway.preserved:
            continue
        fresh_gateway = next(
            (item for item in inventory_again().gateways if item.name == gateway.name),
            None,
        )
        if fresh_gateway != gateway:
            raise RuntimeError("historical Gateway changed before deletion")
        gateway_principals = (
            (
                query_principals.app_application_id,
                query_principals.app_scim_id,
            ),
            (
                query_principals.verifier_application_id,
                query_principals.verifier_scim_id,
            ),
        )
        _retire_live_endpoint_query_groups(
            client,
            endpoint_name=gateway.name,
            endpoint_id=gateway.endpoint_id,
            endpoint_creator=gateway.creator,
            principals=gateway_principals,
            assert_single_writer=assert_single_writer,
        )
        _delete_endpoint_exact(
            client,
            name=gateway.name,
            endpoint_id=gateway.endpoint_id,
            creator=gateway.creator,
            assert_single_writer=assert_single_writer,
            timeout_s=timeout_s,
            sleep=sleep,
        )
    for supervisor in inventory.supervisors:
        if supervisor.preserved:
            continue
        fresh_supervisor = next(
            (
                item
                for item in inventory_again().supervisors
                if item.supervisor_id == supervisor.supervisor_id
            ),
            None,
        )
        if fresh_supervisor != supervisor:
            raise RuntimeError("historical Supervisor changed before deletion")
        proof = cleanup_journal.proof_for(
            supervisor,
            runtime_application_id=inventory.runtime_application_id,
        )
        _cleanup_supervisor_proof(
            client,
            assert_single_writer=assert_single_writer,
            query_principals=query_principals,
            cleanup_journal=cleanup_journal,
            timeout_s=timeout_s,
            sleep=sleep,
            proof=proof,
            stage=True,
        )
    return inventory_again()
