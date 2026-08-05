"""Atomic SCIM membership boundary for serving-endpoint query access."""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from databricks.sdk.errors import (
    AlreadyExists,
    NotFound,
    ResourceAlreadyExists,
    ResourceConflict,
    ResourceDoesNotExist,
)
from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema
from tools.databricks import serving_query_group_provenance as group_provenance
from tools.databricks.workspace_group_deletion import (
    WORKSPACE_GROUP_DELETION_TIMEOUT_SECONDS,
    delete_workspace_group_and_wait,
)

MANAGED_QUERY_GROUP_PREFIX = "mip-serving-query-"
MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX = "mip:serving-query:"
_PATCH_SCHEMA = PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP
_CREATE_CONFLICTS = (AlreadyExists, ResourceAlreadyExists, ResourceConflict)
_CREATE_CONVERGENCE_TIMEOUT_SECONDS = 120
_CREATE_CONVERGENCE_POLL_SECONDS = 2
_NOT_FOUND = (NotFound, ResourceDoesNotExist)


@dataclass(frozen=True)
class ManagedQueryGroup:
    id: str
    name: str
    external_id: str


@dataclass(frozen=True)
class ManagedQueryGroupState:
    contract: ManagedQueryGroup
    member_ids: tuple[str, ...]


def managed_query_group_name(*, endpoint_id: str, application_id: str) -> str:
    """Return a deterministic group name bound to one endpoint and identity."""

    endpoint = endpoint_id.strip()
    principal = application_id.strip()
    if not endpoint or not principal:
        raise ValueError("endpoint and application IDs are required for managed query access")
    endpoint_digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:20]
    principal_digest = hashlib.sha256(principal.encode("utf-8")).hexdigest()[:20]
    return f"{MANAGED_QUERY_GROUP_PREFIX}{endpoint_digest}-{principal_digest}"


def managed_query_group_external_id(*, endpoint_id: str, application_id: str) -> str:
    """Return the immutable external contract ID for a managed query group."""

    endpoint = endpoint_id.strip()
    principal = application_id.strip()
    if not endpoint or not principal:
        raise ValueError("endpoint and application IDs are required for managed query access")
    digest = hashlib.sha256(f"{endpoint}\0{principal}".encode()).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    external_id = f"{MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX}{encoded}"
    if len(external_id) > 64:
        raise AssertionError("managed serving-query external ID exceeds the SCIM limit")
    return external_id


def _hydrated_group(client: Any, *, group_id: str) -> object:
    group = client.groups.get(group_id)
    if str(getattr(group, "id", "") or "").strip() != group_id:
        raise RuntimeError("managed serving-query group immutable ID drifted")
    meta = group.get("meta") if isinstance(group, dict) else getattr(group, "meta", None)
    raw_resource_type = (
        meta.get("resourceType") if isinstance(meta, dict) else getattr(meta, "resource_type", None)
    )
    resource_type = str(getattr(raw_resource_type, "value", raw_resource_type) or "").strip()
    if resource_type != "WorkspaceGroup":
        raise RuntimeError("managed serving-query group is not workspace-local SCIM")
    return group


def _find_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
) -> object | None:
    name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    matches = [
        group
        for group in client.groups.list(filter=f"displayName eq '{name}'")
        if str(getattr(group, "display_name", "") or "").strip() == name
    ]
    if len(matches) > 1:
        raise RuntimeError(f"managed serving-query group {name!r} is duplicated")
    if not matches:
        return None
    group_id = str(getattr(matches[0], "id", "") or "").strip()
    if not group_id:
        raise RuntimeError(f"managed serving-query group {name!r} has no immutable ID")
    return _hydrated_group(client, group_id=group_id)


def _await_intent_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    expected_external_id: str,
    expected_group_id: str | None,
    assert_single_writer: Callable[[], None],
    timeout_s: int,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> object:
    if timeout_s <= 0:
        raise ValueError("managed serving-query group convergence timeout must be positive")
    deadline = clock() + timeout_s
    while True:
        assert_single_writer()
        try:
            group = _find_group(
                client,
                endpoint_id=endpoint_id,
                application_id=application_id,
            )
        except _NOT_FOUND:
            group = None
        if group is not None:
            _assert_group_contract(
                group,
                endpoint_id=endpoint_id,
                application_id=application_id,
                expected_group_id=expected_group_id,
                expected_external_id=expected_external_id,
            )
            return group
        if clock() >= deadline:
            raise RuntimeError(
                "claimed managed serving-query group did not become visible"
            )
        sleep(_CREATE_CONVERGENCE_POLL_SECONDS)


def _assert_group_contract(
    group: object,
    *,
    endpoint_id: str,
    application_id: str,
    expected_group_id: str | None = None,
    expected_external_id: str | None = None,
) -> ManagedQueryGroup:
    expected_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    expected_external_id = expected_external_id or managed_query_group_external_id(
        endpoint_id=endpoint_id, application_id=application_id
    )
    group_id = str(getattr(group, "id", "") or "").strip()
    name = str(getattr(group, "display_name", "") or "").strip()
    external_id = str(getattr(group, "external_id", "") or "").strip()
    if (
        not group_id
        or (expected_group_id is not None and group_id != expected_group_id)
        or name != expected_name
        or external_id != expected_external_id
    ):
        raise RuntimeError("managed serving-query group contract drifted")
    return ManagedQueryGroup(id=group_id, name=name, external_id=external_id)


def _member_ids(group: object) -> set[str]:
    members: set[str] = set()
    for member in getattr(group, "members", None) or []:
        member_id = str(getattr(member, "value", "") or "").strip()
        if not member_id:
            raise RuntimeError("managed serving-query group contains an unbound member")
        if member_id in members:
            raise RuntimeError("managed serving-query group contains a duplicate member")
        members.add(member_id)
    return members


def _required_writer(
    assertion: Callable[[], None] | None,
) -> Callable[[], None]:
    if assertion is None:
        raise RuntimeError("managed serving-query group mutation requires the deployment lease")
    return assertion


def _group_state(
    group: object,
    *,
    endpoint_id: str,
    application_id: str,
    expected_group_id: str | None = None,
    expected_external_id: str | None = None,
) -> ManagedQueryGroupState:
    return ManagedQueryGroupState(
        contract=_assert_group_contract(
            group,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_group_id=expected_group_id,
            expected_external_id=expected_external_id,
        ),
        member_ids=tuple(sorted(_member_ids(group))),
    )


def inspect_managed_query_group_by_id(
    client: Any,
    *,
    group_id: str,
    endpoint_id: str,
    application_id: str,
    missing_ok: bool = False,
    expected_external_id: str | None = None,
) -> ManagedQueryGroupState | None:
    """Rehydrate an expected managed group through its immutable SCIM ID."""

    immutable_id = group_id.strip()
    if not immutable_id:
        raise ValueError("managed serving-query group immutable ID is required")
    try:
        group = _hydrated_group(client, group_id=immutable_id)
    except (NotFound, ResourceDoesNotExist) as exc:
        if missing_ok:
            return None
        raise RuntimeError("managed serving-query group is missing") from exc
    return _group_state(
        group,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_group_id=immutable_id,
        expected_external_id=expected_external_id,
    )


def inspect_managed_query_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    missing_ok: bool = False,
    expected_group_id: str | None = None,
    expected_external_id: str | None = None,
) -> ManagedQueryGroupState | None:
    """Rehydrate one deterministic group's immutable contract and members."""

    try:
        group = _find_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
    except (NotFound, ResourceDoesNotExist) as exc:
        if missing_ok:
            return None
        raise RuntimeError("managed serving-query group is missing") from exc
    if group is None:
        if missing_ok:
            return None
        raise RuntimeError("managed serving-query group is missing")
    return _group_state(
        group,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_group_id=expected_group_id,
        expected_external_id=expected_external_id,
    )


def inspect_claimed_managed_query_group(
    client: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    missing_ok: bool = False,
) -> ManagedQueryGroupState | None:
    """Resolve authorization only through a signed immutable group claim."""

    name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    record = group_provenance.require_claimed(
        client,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=service_principal_id,
        group_name=name,
    )
    return inspect_managed_query_group_by_id(
        client,
        group_id=str(record["group_id"]),
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=missing_ok,
        expected_external_id=str(record["external_id"]),
    )


def assert_managed_query_group_members(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    expected_member_ids: Collection[str],
    missing_ok: bool = False,
    expected_group_id: str | None = None,
    expected_external_id: str | None = None,
) -> ManagedQueryGroupState | None:
    """Verify a deterministic group has exactly the explicitly reviewed members."""

    members = tuple(str(value).strip() for value in expected_member_ids)
    if any(not value for value in members) or len(members) != len(set(members)):
        raise ValueError("expected managed serving-query member IDs must be distinct")
    state = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=missing_ok,
        expected_group_id=expected_group_id,
        expected_external_id=expected_external_id,
    )
    if state is None:
        return None
    if set(state.member_ids) != set(members):
        raise RuntimeError("managed serving-query group membership contract drifted")
    return state


def assert_claimed_managed_query_group_members(
    client: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    expected_member_ids: Collection[str],
    missing_ok: bool = False,
) -> ManagedQueryGroupState | None:
    """Verify membership through the signed immutable-ID authorization proof."""

    members = tuple(str(value).strip() for value in expected_member_ids)
    if any(not value for value in members) or len(members) != len(set(members)):
        raise ValueError("expected managed serving-query member IDs must be distinct")
    state = inspect_claimed_managed_query_group(
        client,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=service_principal_id,
        missing_ok=missing_ok,
    )
    if state is None:
        return None
    if set(state.member_ids) != set(members):
        raise RuntimeError("managed serving-query group membership contract drifted")
    return state


def retire_managed_query_group(
    client: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_endpoint_absent: Callable[[], None],
    assert_single_writer: Callable[[], None],
    timeout_s: int = WORKSPACE_GROUP_DELETION_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Delete an endpoint-orphaned group only from a safe exact member state.

    The supplied callback must prove endpoint absence at the mutation boundary
    and throughout the bounded deletion postflight.
    """

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("exact service-principal SCIM ID is required for group retirement")
    state = inspect_claimed_managed_query_group(
        client,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        missing_ok=True,
    )
    if state is None:
        return False
    members = set(state.member_ids)
    if members not in ({principal_id}, set()):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    assert_single_writer()
    if (
        inspect_managed_query_group_by_id(
            client,
            group_id=state.contract.id,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_external_id=state.contract.external_id,
        )
        != state
        or inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_group_id=state.contract.id,
            expected_external_id=state.contract.external_id,
        )
        != state
    ):
        raise RuntimeError("managed serving-query group changed before deletion")
    delete_workspace_group_and_wait(
        client,
        group_id=state.contract.id,
        expected_state=state,
        inspect_exact_state=lambda: inspect_managed_query_group_by_id(
            client,
            group_id=state.contract.id,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=True,
            expected_external_id=state.contract.external_id,
        ),
        inspect_bound_state=lambda: inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=True,
            expected_group_id=state.contract.id,
            expected_external_id=state.contract.external_id,
        ),
        assert_deletion_context=assert_endpoint_absent,
        assert_single_writer=assert_single_writer,
        resource_label="managed serving-query group",
        timeout_s=timeout_s,
        sleep=sleep,
        clock=clock,
    )
    return True


def ensure_managed_query_group(
    client: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None] | None = None,
    timeout_s: int = _CREATE_CONVERGENCE_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ManagedQueryGroupState:
    """Create or bind the exact endpoint group without activating access."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("service-principal SCIM ID is required")
    writer = _required_writer(assert_single_writer)
    name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    provenance = group_provenance.prepare(
        client,
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        group_name=name,
        assert_single_writer=writer,
    )
    external_id = str(provenance["external_id"])
    claimed_group_id = str(provenance["group_id"]).strip()
    group = _find_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    if group is not None:
        contract = _assert_group_contract(
            group,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_group_id=claimed_group_id or None,
            expected_external_id=external_id,
        )
        if not claimed_group_id:
            provenance = group_provenance.claim(
                client,
                app_name=app_name,
                deployment_lease_id=deployment_lease_id,
                deployment_source_git_sha=deployment_source_git_sha,
                record=provenance,
                group_id=contract.id,
                proof_kind="signed_intent_projection",
                assert_single_writer=writer,
            )
            claimed_group_id = str(provenance["group_id"])
    elif claimed_group_id:
        group = _await_intent_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_external_id=external_id,
            expected_group_id=claimed_group_id,
            assert_single_writer=writer,
            timeout_s=timeout_s,
            sleep=sleep,
            clock=clock,
        )
    else:
        writer()
        try:
            created = client.groups.create(
                display_name=name,
                external_id=external_id,
            )
        except _CREATE_CONFLICTS:
            group = _await_intent_group(
                client,
                endpoint_id=endpoint_id,
                application_id=application_id,
                expected_external_id=external_id,
                expected_group_id=None,
                assert_single_writer=writer,
                timeout_s=timeout_s,
                sleep=sleep,
                clock=clock,
            )
            claimed_group_id = _assert_group_contract(
                group,
                endpoint_id=endpoint_id,
                application_id=application_id,
                expected_external_id=external_id,
            ).id
            proof_kind = "signed_intent_projection"
        else:
            claimed_group_id = _assert_group_contract(
                created,
                endpoint_id=endpoint_id,
                application_id=application_id,
                expected_external_id=external_id,
            ).id
            proof_kind = "create_response"
        provenance = group_provenance.claim(
            client,
            app_name=app_name,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            record=provenance,
            group_id=claimed_group_id,
            proof_kind=proof_kind,
            assert_single_writer=writer,
        )
        if provenance["group_id"] != claimed_group_id:
            raise RuntimeError("serving-query group provenance claim changed immutable ID")
        group = _await_intent_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_external_id=external_id,
            expected_group_id=claimed_group_id,
            assert_single_writer=writer,
            timeout_s=timeout_s,
            sleep=sleep,
            clock=clock,
        )
    contract = _assert_group_contract(
        group,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_group_id=claimed_group_id,
        expected_external_id=external_id,
    )
    members = _member_ids(group)
    if members.difference({principal_id}):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    return ManagedQueryGroupState(
        contract=contract,
        member_ids=tuple(sorted(members)),
    )


def recover_existing_managed_query_group(
    client: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    expected_intent: dict[str, Any],
    assert_single_writer: Callable[[], None] | None = None,
) -> ManagedQueryGroupState | None:
    """Claim an existing exact intent group without creating any resource."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("service-principal SCIM ID is required")
    writer = _required_writer(assert_single_writer)
    name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    expected_external_id = str(expected_intent.get("external_id", "")).strip()
    expected_group_id = str(expected_intent.get("group_id", "")).strip()
    if not expected_external_id.startswith(group_provenance.INTENT_EXTERNAL_ID_PREFIX):
        raise RuntimeError("signed serving-query group intent marker is invalid")
    state = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=True,
        expected_group_id=expected_group_id or None,
        expected_external_id=expected_external_id,
    )
    if state is None:
        return None
    if set(state.member_ids).difference({principal_id}):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    contract = state.contract
    admitted = group_provenance.admit_existing(
        client,
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        group_name=name,
        expected_record=expected_intent,
        assert_single_writer=writer,
    )
    if admitted is None:
        raise RuntimeError("serving-query group provenance disappeared during recovery")
    claimed_group_id = str(admitted["group_id"]).strip()
    if claimed_group_id and claimed_group_id != contract.id:
        raise RuntimeError("serving-query group provenance claims another immutable ID")
    current = inspect_managed_query_group_by_id(
        client,
        group_id=contract.id,
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=True,
        expected_external_id=contract.external_id,
    )
    projected = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=True,
        expected_group_id=contract.id,
        expected_external_id=contract.external_id,
    )
    if current is None and projected is None:
        raise RuntimeError("managed serving-query group disappeared during recovery")
    if current is None or projected != current:
        raise RuntimeError("managed serving-query group changed before recovery claim")
    if set(current.member_ids).difference({principal_id}):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    if not claimed_group_id:
        admitted = group_provenance.claim(
            client,
            app_name=app_name,
            deployment_lease_id=deployment_lease_id,
            deployment_source_git_sha=deployment_source_git_sha,
            record=admitted,
            group_id=contract.id,
            proof_kind="signed_intent_projection",
            assert_single_writer=writer,
        )
        if admitted["group_id"] != contract.id:
            raise RuntimeError("serving-query group provenance claim changed immutable ID")
    final = inspect_managed_query_group_by_id(
        client,
        group_id=contract.id,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_external_id=contract.external_id,
    )
    if final is None or final != inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_group_id=contract.id,
        expected_external_id=contract.external_id,
    ):
        raise RuntimeError("managed serving-query group changed after recovery claim")
    if set(final.member_ids).difference({principal_id}):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    return final


def ensure_managed_query_membership(
    client: Any,
    *,
    app_name: str,
    deployment_lease_id: str,
    deployment_source_git_sha: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None] | None = None,
) -> ManagedQueryGroup:
    """Create the endpoint-bound group and atomically add its sole member."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("service-principal SCIM ID is required")
    writer = _required_writer(assert_single_writer)
    state = ensure_managed_query_group(
        client,
        app_name=app_name,
        deployment_lease_id=deployment_lease_id,
        deployment_source_git_sha=deployment_source_git_sha,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        assert_single_writer=writer,
    )
    contract = state.contract
    members = set(state.member_ids)
    if principal_id not in members:
        writer()
        client.groups.patch(
            id=contract.id,
            operations=[
                Patch(
                    op=PatchOp.ADD,
                    value={"members": [{"value": principal_id}]},
                )
            ],
            schemas=[_PATCH_SCHEMA],
        )
    postflight = assert_managed_query_group_members(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        expected_member_ids=(principal_id,),
        expected_group_id=contract.id,
        expected_external_id=contract.external_id,
    )
    assert postflight is not None
    return postflight.contract


def remove_managed_query_membership(
    client: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None] | None = None,
) -> bool:
    """Atomically remove one identity from its endpoint-bound query group."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("exact service-principal SCIM ID is required for managed query revoke")
    writer = _required_writer(assert_single_writer)
    state = inspect_claimed_managed_query_group(
        client,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        missing_ok=True,
    )
    if state is None:
        return False
    contract = state.contract
    members = set(state.member_ids)
    if principal_id not in members:
        if members:
            raise RuntimeError(
                "managed serving-query group is not empty and does not contain "
                "the exact service principal"
            )
        return False
    writer()
    client.groups.patch(
        id=contract.id,
        operations=[
            Patch(
                op=PatchOp.REMOVE,
                path=f'members[value eq "{principal_id}"]',
            )
        ],
        schemas=[_PATCH_SCHEMA],
    )
    try:
        assert_claimed_managed_query_group_members(
            client,
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=application_id,
            service_principal_id=principal_id,
            expected_member_ids=(),
        )
    except RuntimeError as exc:
        raise RuntimeError("managed serving-query group did not converge to exactly empty") from exc
    return True


def quiesce_claimed_managed_query_group(
    client: Any,
    *,
    app_name: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None],
    attempts: int = 3,
) -> None:
    """Fail closed by emptying every member from the signed managed group."""

    if attempts <= 0:
        raise ValueError("managed serving-query quiesce attempts must be positive")
    for _attempt in range(attempts):
        state = inspect_claimed_managed_query_group(
            client,
            app_name=app_name,
            endpoint_id=endpoint_id,
            application_id=application_id,
            service_principal_id=service_principal_id,
        )
        assert state is not None
        if not state.member_ids:
            return
        if any(
            not member_id
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
                   for character in member_id)
            for member_id in state.member_ids
        ):
            raise RuntimeError(
                "managed serving-query group contains an unsafe member identifier"
            )
        assert_single_writer()
        client.groups.patch(
            id=state.contract.id,
            operations=[
                Patch(
                    op=PatchOp.REMOVE,
                    path=f'members[value eq "{member_id}"]',
                )
                for member_id in state.member_ids
            ],
            schemas=[_PATCH_SCHEMA],
        )
    state = assert_claimed_managed_query_group_members(
        client,
        app_name=app_name,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=service_principal_id,
        expected_member_ids=(),
    )
    assert state is not None
