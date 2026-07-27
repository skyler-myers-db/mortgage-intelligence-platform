"""Workspace-local capability groups for atomically revocable agent-proxy access."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any, Literal

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema
from tools.databricks.m2m_access_policy import resolve_effective_groups

ManagedAgentProxyResourceKind = Literal["supervisor", "genie"]

MANAGED_AGENT_PROXY_GROUP_PREFIX = "mip-agent-proxy-cap-"
MANAGED_AGENT_PROXY_GROUP_EXTERNAL_ID_PREFIX = "mip:agent-proxy:"
_KIND_TOKEN: dict[ManagedAgentProxyResourceKind, str] = {
    "supervisor": "s",
    "genie": "g",
}
_PATCH_SCHEMA = PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP
_NAME_RE = re.compile(
    rf"^{re.escape(MANAGED_AGENT_PROXY_GROUP_PREFIX)}"
    r"(?P<kind>[sg])-(?P<resource>[0-9a-f]{20})-(?P<application>[0-9a-f]{20})$"
)
_EXTERNAL_ID_RE = re.compile(
    rf"^{re.escape(MANAGED_AGENT_PROXY_GROUP_EXTERNAL_ID_PREFIX)}"
    r"(?P<kind>[sg]):(?P<resource>[0-9a-f]{16}):(?P<application>[0-9a-f]{16})$"
)
_MAX_INVENTORY = 1000
_PROJECTION_DEADLINE_SECONDS = 120.0
_PROJECTION_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class ManagedAgentProxyGroup:
    """Immutable contract for one resource-bound agent-proxy capability group."""

    id: str
    name: str
    external_id: str


@dataclass(frozen=True)
class ManagedAgentProxyGroupState:
    contract: ManagedAgentProxyGroup
    member_ids: tuple[str, ...]


def _digest(value: str, *, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _kind_token(resource_kind: ManagedAgentProxyResourceKind) -> str:
    try:
        return _KIND_TOKEN[resource_kind]
    except KeyError as exc:
        raise ValueError("agent-proxy resource kind must be supervisor or genie") from exc


def managed_agent_proxy_group_name(
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
) -> str:
    """Return the deterministic workspace-group name for one capability."""

    resource = resource_id.strip()
    application = application_id.strip()
    if not resource or not application:
        raise ValueError("agent-proxy resource and application IDs are required")
    return (
        f"{MANAGED_AGENT_PROXY_GROUP_PREFIX}{_kind_token(resource_kind)}-"
        f"{_digest(resource, length=20)}-{_digest(application, length=20)}"
    )


def managed_agent_proxy_group_external_id(
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
) -> str:
    """Return the deterministic external ID for one capability group."""

    resource = resource_id.strip()
    application = application_id.strip()
    if not resource or not application:
        raise ValueError("agent-proxy resource and application IDs are required")
    return (
        f"{MANAGED_AGENT_PROXY_GROUP_EXTERNAL_ID_PREFIX}{_kind_token(resource_kind)}:"
        f"{_digest(resource, length=16)}:{_digest(application, length=16)}"
    )


def assert_managed_agent_proxy_group_binding_contract(
    *,
    name: str,
    external_id: str,
) -> None:
    """Validate one reserved group without requiring its unhashed resource IDs."""

    name_match = _NAME_RE.fullmatch(name)
    external_match = _EXTERNAL_ID_RE.fullmatch(external_id)
    if (
        name_match is None
        or external_match is None
        or external_match.group("kind") != name_match.group("kind")
        or not name_match.group("resource").startswith(
            external_match.group("resource")
        )
        or not name_match.group("application").startswith(
            external_match.group("application")
        )
    ):
        raise RuntimeError("managed agent-proxy group immutable contract drifted")


def _text(value: object, *names: str) -> str:
    for name in names:
        candidate = (
            value.get(name)
            if isinstance(value, dict)
            else getattr(value, name, None)
        )
        text = str(getattr(candidate, "value", candidate) or "").strip()
        if text:
            return text
    return ""


def _resource_type(group: object) -> str:
    meta = group.get("meta") if isinstance(group, dict) else getattr(group, "meta", None)
    return _text(meta or {}, "resource_type", "resourceType")


def _hydrated_group(client: Any, *, group_id: str) -> object:
    group = client.groups.get(group_id)
    if _text(group, "id") != group_id:
        raise RuntimeError("managed agent-proxy group immutable ID drifted")
    if _resource_type(group) != "WorkspaceGroup":
        raise RuntimeError("managed agent-proxy group is not workspace-local")
    return group


def _member_ids(group: object) -> set[str]:
    raw_members = (
        group.get("members")
        if isinstance(group, dict)
        else getattr(group, "members", None)
    ) or []
    if not isinstance(raw_members, list | tuple):
        raise RuntimeError("managed agent-proxy group member inventory is malformed")
    members: set[str] = set()
    for member in raw_members:
        member_id = _text(member, "value")
        if not member_id or member_id in members:
            raise RuntimeError("managed agent-proxy group has ambiguous membership")
        members.add(member_id)
    return members


def _wait_member_ids(
    client: Any,
    *,
    group_id: str,
    expected_member_ids: set[str],
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> object:
    deadline = clock() + _PROJECTION_DEADLINE_SECONDS
    while True:
        group = _hydrated_group(client, group_id=group_id)
        if _member_ids(group) == expected_member_ids:
            return group
        if clock() >= deadline:
            raise RuntimeError(
                "managed agent-proxy group membership did not converge"
            )
        sleep(_PROJECTION_POLL_SECONDS)


def _find_group(
    client: Any,
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
) -> object | None:
    name = managed_agent_proxy_group_name(
        resource_kind=resource_kind,
        resource_id=resource_id,
        application_id=application_id,
    )
    matches = [
        group
        for group in client.groups.list(filter=f"displayName eq '{name}'")
        if _text(group, "display_name", "displayName") == name
    ]
    if len(matches) > 1:
        raise RuntimeError(f"managed agent-proxy group {name!r} is duplicated")
    if not matches:
        return None
    group_id = _text(matches[0], "id")
    if not group_id:
        raise RuntimeError(f"managed agent-proxy group {name!r} has no immutable ID")
    return _hydrated_group(client, group_id=group_id)


def _assert_contract(
    group: object,
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
) -> ManagedAgentProxyGroup:
    contract = ManagedAgentProxyGroup(
        id=_text(group, "id"),
        name=_text(group, "display_name", "displayName"),
        external_id=_text(group, "external_id", "externalId"),
    )
    if (
        not contract.id
        or contract.name
        != managed_agent_proxy_group_name(
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        )
        or contract.external_id
        != managed_agent_proxy_group_external_id(
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        )
    ):
        raise RuntimeError("managed agent-proxy group contract drifted")
    return contract


def inspect_managed_agent_proxy_group(
    client: Any,
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
    missing_ok: bool = False,
) -> ManagedAgentProxyGroupState | None:
    """Rehydrate one exact resource-bound group and its member inventory."""

    try:
        group = _find_group(
            client,
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        )
    except (NotFound, ResourceDoesNotExist) as exc:
        if missing_ok:
            return None
        raise RuntimeError("managed agent-proxy group is missing") from exc
    if group is None:
        if missing_ok:
            return None
        raise RuntimeError("managed agent-proxy group is missing")
    return ManagedAgentProxyGroupState(
        contract=_assert_contract(
            group,
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        ),
        member_ids=tuple(sorted(_member_ids(group))),
    )


def ensure_managed_agent_proxy_group(
    client: Any,
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
    service_principal_id: str,
    assert_single_writer: Callable[[], None],
) -> ManagedAgentProxyGroupState:
    """Create or bind a capability group without broadening its membership."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("agent-proxy service-principal SCIM ID is required")
    group = _find_group(
        client,
        resource_kind=resource_kind,
        resource_id=resource_id,
        application_id=application_id,
    )
    if group is None:
        assert_single_writer()
        group = client.groups.create(
            display_name=managed_agent_proxy_group_name(
                resource_kind=resource_kind,
                resource_id=resource_id,
                application_id=application_id,
            ),
            external_id=managed_agent_proxy_group_external_id(
                resource_kind=resource_kind,
                resource_id=resource_id,
                application_id=application_id,
            ),
        )
        group_id = _text(group, "id")
        if not group_id:
            raise RuntimeError("created managed agent-proxy group has no immutable ID")
        group = _hydrated_group(client, group_id=group_id)
    state = ManagedAgentProxyGroupState(
        contract=_assert_contract(
            group,
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        ),
        member_ids=tuple(sorted(_member_ids(group))),
    )
    if set(state.member_ids).difference({principal_id}):
        raise RuntimeError("managed agent-proxy group contains an unrelated member")
    return state


def set_managed_agent_proxy_membership(
    client: Any,
    *,
    resource_kind: ManagedAgentProxyResourceKind,
    resource_id: str,
    application_id: str,
    service_principal_id: str,
    active: bool,
    assert_single_writer: Callable[[], None],
) -> bool:
    """Atomically add or remove the exact proxy identity from one group."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("agent-proxy service-principal SCIM ID is required")
    state = (
        ensure_managed_agent_proxy_group(
            client,
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
            service_principal_id=principal_id,
            assert_single_writer=assert_single_writer,
        )
        if active
        else inspect_managed_agent_proxy_group(
            client,
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
            missing_ok=True,
        )
    )
    if state is None:
        return False
    members = set(state.member_ids)
    if members.difference({principal_id}):
        raise RuntimeError("managed agent-proxy group contains an unrelated member")
    if active and principal_id not in members:
        operation = Patch(
            op=PatchOp.ADD,
            value={"members": [{"value": principal_id}]},
        )
    elif not active and principal_id in members:
        operation = Patch(
            op=PatchOp.REMOVE,
            path=f'members[value eq "{principal_id}"]',
        )
    else:
        return False
    assert_single_writer()
    client.groups.patch(
        id=state.contract.id,
        operations=[operation],
        schemas=[_PATCH_SCHEMA],
    )
    postflight_group = _wait_member_ids(
        client,
        group_id=state.contract.id,
        expected_member_ids=({principal_id} if active else set()),
    )
    _assert_contract(
        postflight_group,
        resource_kind=resource_kind,
        resource_id=resource_id,
        application_id=application_id,
    )
    return True


def managed_agent_proxy_groups_for_application(
    client: Any,
    *,
    application_id: str,
    service_principal_id: str | None = None,
) -> tuple[ManagedAgentProxyGroupState, ...]:
    """Inventory every managed capability group associated with one application."""

    application = application_id.strip()
    if not application:
        raise ValueError("agent-proxy application ID is required")
    name_suffix = f"-{_digest(application, length=20)}"
    external_suffix = f":{_digest(application, length=16)}"
    summaries = tuple(
        client.groups.list(attributes="id,displayName")
    )
    if len(summaries) > _MAX_INVENTORY:
        raise RuntimeError("managed agent-proxy group inventory is unbounded")
    states: list[ManagedAgentProxyGroupState] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for summary in summaries:
        name = _text(summary, "display_name", "displayName")
        if not name.startswith(MANAGED_AGENT_PROXY_GROUP_PREFIX):
            continue
        name_match = _NAME_RE.fullmatch(name)
        if name_match is None:
            raise RuntimeError("reserved managed agent-proxy group name is malformed")
        group_id = _text(summary, "id")
        if not group_id or group_id in seen_ids or name.casefold() in seen_names:
            raise RuntimeError("managed agent-proxy group inventory is ambiguous")
        group = _hydrated_group(client, group_id=group_id)
        external_id = _text(group, "external_id", "externalId")
        assert_managed_agent_proxy_group_binding_contract(
            name=name,
            external_id=external_id,
        )
        members = tuple(sorted(_member_ids(group)))
        seen_ids.add(group_id)
        seen_names.add(name.casefold())
        if not name.endswith(name_suffix):
            if service_principal_id and service_principal_id in members:
                raise RuntimeError(
                    "agent-proxy is a member of another application's capability group"
                )
            continue
        if not external_id.endswith(external_suffix):
            raise RuntimeError("managed agent-proxy group application binding drifted")
        states.append(
            ManagedAgentProxyGroupState(
                contract=ManagedAgentProxyGroup(
                    id=group_id,
                    name=name,
                    external_id=external_id,
                ),
                member_ids=members,
            )
        )
    return tuple(sorted(states, key=lambda state: state.contract.id))


def assert_managed_agent_proxy_members(
    state: ManagedAgentProxyGroupState,
    *,
    expected_member_ids: Collection[str],
) -> None:
    """Assert a reviewed group has the exact bounded member set."""

    expected = tuple(sorted(str(value).strip() for value in expected_member_ids))
    if (
        any(not value for value in expected)
        or len(expected) != len(set(expected))
        or state.member_ids != expected
    ):
        raise RuntimeError("managed agent-proxy group membership contract drifted")


def remove_managed_agent_proxy_membership(
    client: Any,
    *,
    state: ManagedAgentProxyGroupState,
    service_principal_id: str,
    assert_single_writer: Callable[[], None],
) -> bool:
    """Remove one exact member from an already-attested application group."""

    principal_id = service_principal_id.strip()
    if not principal_id:
        raise ValueError("agent-proxy service-principal SCIM ID is required")
    current_group = _hydrated_group(client, group_id=state.contract.id)
    current = ManagedAgentProxyGroupState(
        contract=ManagedAgentProxyGroup(
            id=_text(current_group, "id"),
            name=_text(current_group, "display_name", "displayName"),
            external_id=_text(current_group, "external_id", "externalId"),
        ),
        member_ids=tuple(sorted(_member_ids(current_group))),
    )
    if current != state:
        raise RuntimeError("managed agent-proxy group changed before membership revoke")
    members = set(current.member_ids)
    if members.difference({principal_id}):
        raise RuntimeError("managed agent-proxy group contains an unrelated member")
    if principal_id not in members:
        return False
    assert_single_writer()
    client.groups.patch(
        id=current.contract.id,
        operations=[
            Patch(
                op=PatchOp.REMOVE,
                path=f'members[value eq "{principal_id}"]',
            )
        ],
        schemas=[_PATCH_SCHEMA],
    )
    postflight = _wait_member_ids(
        client,
        group_id=current.contract.id,
        expected_member_ids=set(),
    )
    if (
        _text(postflight, "display_name", "displayName") != current.contract.name
        or _text(postflight, "external_id", "externalId")
        != current.contract.external_id
    ):
        raise RuntimeError("managed agent-proxy group revoke postflight failed")
    return True


def wait_for_managed_agent_proxy_group_projection(
    client: Any,
    *,
    application_id: str,
    service_principal_id: str,
    expected_active_group_names: Collection[str],
    sleep: Callable[[float], object] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = _PROJECTION_DEADLINE_SECONDS,
) -> set[str]:
    """Wait for exact managed capability groups in the effective projection."""

    expected = {
        str(name).strip() for name in expected_active_group_names
        if str(name).strip()
    }
    if (
        len(expected) != len(expected_active_group_names)
        or not service_principal_id.strip()
        or deadline_seconds <= 0
    ):
        raise ValueError("managed agent-proxy projection contract is incomplete")
    states = managed_agent_proxy_groups_for_application(
        client,
        application_id=application_id,
        service_principal_id=service_principal_id,
    )
    managed_names = {state.contract.name for state in states}
    if not expected.issubset(managed_names):
        raise RuntimeError("expected managed agent-proxy group is missing")
    deadline = clock() + deadline_seconds
    while True:
        effective = set(
            resolve_effective_groups(
                client,
                sp_id=service_principal_id,
            ).values()
        )
        if effective.intersection(managed_names) == expected:
            return effective
        if clock() >= deadline:
            raise RuntimeError(
                "managed agent-proxy effective-group projection did not converge"
            )
        sleep(_PROJECTION_POLL_SECONDS)
