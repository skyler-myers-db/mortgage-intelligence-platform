"""Atomic SCIM membership boundary for serving-endpoint query access."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema

MANAGED_QUERY_GROUP_PREFIX = "mip-serving-query-"
MANAGED_QUERY_GROUP_EXTERNAL_ID_PREFIX = "mip:serving-query:"
_PATCH_SCHEMA = PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP
_MAX_EFFECTIVE_GROUPS = 1000


@dataclass(frozen=True)
class ManagedQueryGroup:
    id: str
    name: str
    external_id: str


@dataclass(frozen=True)
class ManagedQueryGroupState:
    contract: ManagedQueryGroup
    member_ids: tuple[str, ...]


def assert_managed_query_group_administration_isolated(
    client: Any,
    *,
    account_id: str,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    authoritative_effective_groups: Mapping[str, str],
) -> ManagedQueryGroupState | None:
    """Prove the bound identity cannot administer its endpoint-bound group.

    An empty group may be intentionally retained while a nondeletable endpoint
    still names it in an ACL. That group remains associated with the identity
    through its deterministic name and external ID, so it must receive the
    same administration proof as an active one-member group.

    These groups are deliberately workspace-local. Workspace-local groups have
    no account rule set; their credential-side management denial is proved by
    an idempotent Workspace Groups SCIM PATCH in ``identity_boundary_probes``.
    This admin-side check binds the exact resource plane, rejects unrelated
    membership, and proves the target is not a workspace administrator.
    """

    account = account_id.strip()
    principal_id = service_principal_id.strip()
    principal = application_id.strip()
    if not account or not endpoint_id.strip() or not principal or not principal_id:
        raise ValueError(
            "account, endpoint, application, and service-principal IDs are required"
        )
    state = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=principal,
        missing_ok=True,
    )
    if state is None:
        return None
    if state.member_ids not in {(), (principal_id,)}:
        raise RuntimeError(
            "managed serving-query group membership is neither active nor safely retired"
        )
    effective_groups: dict[str, str] = {}
    effective_ids: set[str] = set()
    effective_names: set[str] = set()
    if len(authoritative_effective_groups) > _MAX_EFFECTIVE_GROUPS:
        raise RuntimeError(
            "authoritative managed-group membership snapshot is unbounded"
        )
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


def _assert_group_contract(
    group: object,
    *,
    endpoint_id: str,
    application_id: str,
) -> ManagedQueryGroup:
    expected_name = managed_query_group_name(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    expected_external_id = managed_query_group_external_id(
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    group_id = str(getattr(group, "id", "") or "").strip()
    name = str(getattr(group, "display_name", "") or "").strip()
    external_id = str(getattr(group, "external_id", "") or "").strip()
    if not group_id or name != expected_name or external_id != expected_external_id:
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


def inspect_managed_query_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    missing_ok: bool = False,
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
    contract = _assert_group_contract(
        group,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    return ManagedQueryGroupState(
        contract=contract,
        member_ids=tuple(sorted(_member_ids(group))),
    )


def assert_managed_query_group_members(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    expected_member_ids: Collection[str],
    missing_ok: bool = False,
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
    )
    if state is None:
        return None
    if set(state.member_ids) != set(members):
        raise RuntimeError("managed serving-query group membership contract drifted")
    return state


def retire_managed_query_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None],
) -> bool:
    """Delete an endpoint-orphaned group only from a safe exact member state.

    The caller must separately prove the bound serving endpoint is absent.
    """

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("exact service-principal SCIM ID is required for group retirement")
    state = inspect_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        missing_ok=True,
    )
    if state is None:
        return False
    members = set(state.member_ids)
    if members not in ({principal_id}, set()):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    assert_single_writer()
    client.groups.delete(state.contract.id)
    if (
        inspect_managed_query_group(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            missing_ok=True,
        )
        is not None
    ):
        raise RuntimeError("managed serving-query group retirement did not converge")
    return True


def ensure_managed_query_group(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None] | None = None,
) -> ManagedQueryGroupState:
    """Create or bind the exact endpoint group without activating access."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("service-principal SCIM ID is required")
    group = _find_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    if group is None:
        name = managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        if assert_single_writer is not None:
            assert_single_writer()
        group = client.groups.create(
            display_name=name,
            external_id=managed_query_group_external_id(
                endpoint_id=endpoint_id,
                application_id=application_id,
            ),
        )
        group_id = str(getattr(group, "id", "") or "").strip()
        if not group_id:
            raise RuntimeError("created managed serving-query group has no immutable ID")
        group = _hydrated_group(client, group_id=group_id)
    contract = _assert_group_contract(
        group,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    members = _member_ids(group)
    if members.difference({principal_id}):
        raise RuntimeError("managed serving-query group contains an unrelated member")
    return ManagedQueryGroupState(
        contract=contract,
        member_ids=tuple(sorted(members)),
    )


def ensure_managed_query_membership(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None] | None = None,
) -> ManagedQueryGroup:
    """Create the endpoint-bound group and atomically add its sole member."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("service-principal SCIM ID is required")
    state = ensure_managed_query_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
        service_principal_id=principal_id,
        assert_single_writer=assert_single_writer,
    )
    contract = state.contract
    members = set(state.member_ids)
    if principal_id not in members:
        if assert_single_writer is not None:
            assert_single_writer()
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
    )
    assert postflight is not None
    return postflight.contract


def remove_managed_query_membership(
    client: Any,
    *,
    endpoint_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None] | None = None,
) -> bool:
    """Atomically remove one identity from its endpoint-bound query group."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("exact service-principal SCIM ID is required for managed query revoke")
    group = _find_group(
        client,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    if group is None:
        return False
    contract = _assert_group_contract(
        group,
        endpoint_id=endpoint_id,
        application_id=application_id,
    )
    members = _member_ids(group)
    if principal_id not in members:
        if members:
            raise RuntimeError(
                "managed serving-query group is not empty and does not contain "
                "the exact service principal"
            )
        return False
    if assert_single_writer is not None:
        assert_single_writer()
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
        assert_managed_query_group_members(
            client,
            endpoint_id=endpoint_id,
            application_id=application_id,
            expected_member_ids=(),
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "managed serving-query group did not converge to exactly empty"
        ) from exc
    return True
