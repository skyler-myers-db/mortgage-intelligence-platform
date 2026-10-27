from __future__ import annotations

from types import SimpleNamespace

import pytest
from databricks.sdk.errors import ResourceDoesNotExist

from tools.databricks import retired_serving_query_groups as retired
from tools.databricks import serving_query_group_access as group_access
from tools.databricks import supervisor_retirement_ack as ack
from tools.databricks.serving_query_group_access import (
    managed_query_group_name,
)
from tools.databricks.serving_query_group_provenance import (
    MissingClaimedGroupProvenanceError,
    intent_external_id,
)

APP = "mip-app"
ENDPOINT = "old-gateway"
ENDPOINT_ID = "old-gateway-id"
CREATOR = "legacy-owner"
VERIFIER = "verifier-client"
VERIFIER_SCIM = "verifier-scim-id"
NONCE = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _signed_group_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    def require_claimed(
        workspace: object,
        *,
        app_name: str,
        endpoint_id: str,
        application_id: str,
        service_principal_id: str,
        group_name: str,
    ) -> dict[str, str]:
        assert app_name == APP
        key = (endpoint_id, application_id, service_principal_id)
        record = workspace.groups.claims.get(key)
        if record is None:
            raise MissingClaimedGroupProvenanceError(
                "managed serving-query group has no signed immutable-ID provenance"
            )
        assert group_name == managed_query_group_name(
            endpoint_id=endpoint_id,
            application_id=application_id,
        )
        return record

    monkeypatch.setattr(
        group_access.group_provenance,
        "require_claimed",
        require_claimed,
    )


def _permission_entry(
    *,
    principal: str | None = None,
    group: str | None = None,
) -> object:
    return SimpleNamespace(
        service_principal_name=principal,
        group_name=group,
        all_permissions=[SimpleNamespace(permission_level="CAN_QUERY", inherited=False)],
    )


class _Groups:
    def __init__(
        self,
        *,
        managed: bool,
        member_id: str | None,
        application_id: str = VERIFIER,
        scim_id: str = VERIFIER_SCIM,
    ) -> None:
        self.deleted: list[str] = []
        self.patches: list[str] = []
        self.by_id: dict[str, object] = {}
        self.claims: dict[tuple[str, str, str], dict[str, str]] = {}
        if managed:
            external_id = intent_external_id(
                endpoint_id=ENDPOINT_ID,
                application_id=application_id,
                creation_nonce=NONCE,
            )
            group_id = f"{application_id}-group-id"
            self.by_id[group_id] = SimpleNamespace(
                id=group_id,
                display_name=managed_query_group_name(
                    endpoint_id=ENDPOINT_ID,
                    application_id=application_id,
                ),
                external_id=external_id,
                members=(
                    [] if member_id is None else [SimpleNamespace(value=member_id)]
                ),
                meta=SimpleNamespace(resource_type="WorkspaceGroup"),
            )
            self.claims[(ENDPOINT_ID, application_id, scim_id)] = {
                "group_id": group_id,
                "external_id": external_id,
            }

    def list(self, **kwargs: object) -> list[object]:
        groups = list(self.by_id.values())
        filter_value = str(kwargs.get("filter") or "")
        if not filter_value:
            return groups
        name = filter_value.removeprefix("displayName eq '").removesuffix("'")
        return [group for group in groups if group.display_name == name]

    def get(self, group_id: str) -> object:
        if group_id not in self.by_id:
            raise ResourceDoesNotExist("deleted")
        return self.by_id[group_id]

    def patch(self, *, id: str, **_kwargs: object) -> None:
        self.patches.append(id)
        self.by_id[id].members = []

    def delete(self, group_id: str) -> None:
        self.deleted.append(group_id)
        del self.by_id[group_id]


def _workspace(
    mode: str,
    *,
    member_id: str | None = VERIFIER_SCIM,
) -> object:
    managed = mode in {"managed", "mixed"}
    group_name = managed_query_group_name(
        endpoint_id=ENDPOINT_ID,
        application_id=VERIFIER,
    )
    entries = []
    if mode in {"direct", "mixed"}:
        entries.append(_permission_entry(principal=VERIFIER))
    if managed:
        entries.append(_permission_entry(group=group_name))
    groups = _Groups(managed=managed, member_id=member_id)
    endpoints = SimpleNamespace(
        get=lambda _name: SimpleNamespace(id=ENDPOINT_ID, creator=CREATOR),
        get_permissions=lambda _id: SimpleNamespace(access_control_list=entries),
        delete=lambda _name: pytest.fail("live nondeletable endpoint was deleted"),
    )
    return SimpleNamespace(serving_endpoints=endpoints, groups=groups)


def _endpoint_identity(_workspace: object, _name: str) -> tuple[str, str]:
    return ENDPOINT_ID, CREATOR


@pytest.mark.parametrize(
    ("mode", "expected_leases", "expected_patches"),
    [
        ("none", 0, 0),
        ("managed", 1, 1),
    ],
)
def test_live_verifier_retirement_handles_revocable_modes(
    mode: str,
    expected_leases: int,
    expected_patches: int,
) -> None:
    workspace = _workspace(mode, member_id=None if mode == "none" else VERIFIER_SCIM)
    leases: list[str] = []

    assert (
        retired.revoke_live_managed_query_access(
            workspace,
            app_name=APP,
            endpoint_name=ENDPOINT,
            endpoint_id=ENDPOINT_ID,
            endpoint_creator=CREATOR,
            application_id=VERIFIER,
            scim_id=VERIFIER_SCIM,
            identity_label="verifier",
            assert_single_writer=lambda: leases.append("lease"),
            endpoint_identity=_endpoint_identity,
        )
        == mode
    )

    assert len(leases) == expected_leases
    assert len(workspace.groups.patches) == expected_patches
    assert workspace.groups.deleted == []
    assert workspace.serving_endpoints.get(ENDPOINT).id == ENDPOINT_ID
    if mode == "managed":
        assert workspace.groups.get(f"{VERIFIER}-group-id").members == []


@pytest.mark.parametrize("mode", ["direct", "mixed"])
def test_live_verifier_retirement_rejects_nonatomic_modes_without_mutation(
    mode: str,
) -> None:
    workspace = _workspace(mode)
    leases: list[str] = []

    with pytest.raises(RuntimeError, match="cannot be atomically retired"):
        retired.revoke_live_managed_query_access(
            workspace,
            app_name=APP,
            endpoint_name=ENDPOINT,
            endpoint_id=ENDPOINT_ID,
            endpoint_creator=CREATOR,
            application_id=VERIFIER,
            scim_id=VERIFIER_SCIM,
            identity_label="verifier",
            assert_single_writer=lambda: leases.append("lease"),
            endpoint_identity=_endpoint_identity,
        )

    assert leases == []
    assert workspace.groups.patches == []


def test_live_verifier_retirement_rejects_unrelated_member_before_lease() -> None:
    workspace = _workspace("managed", member_id="unrelated-scim-id")
    leases: list[str] = []

    with pytest.raises(RuntimeError, match="outside its immutable contract"):
        retired.revoke_live_managed_query_access(
            workspace,
            app_name=APP,
            endpoint_name=ENDPOINT,
            endpoint_id=ENDPOINT_ID,
            endpoint_creator=CREATOR,
            application_id=VERIFIER,
            scim_id=VERIFIER_SCIM,
            identity_label="verifier",
            assert_single_writer=lambda: leases.append("lease"),
            endpoint_identity=_endpoint_identity,
        )

    assert leases == []
    assert workspace.groups.patches == []


def test_live_verifier_retirement_rechecks_endpoint_identity_before_mutation() -> None:
    workspace = _workspace("managed")
    reads = 0

    def drifting_identity(_workspace: object, _name: str) -> tuple[str, str]:
        nonlocal reads
        reads += 1
        return (ENDPOINT_ID, CREATOR) if reads == 1 else ("replacement-id", CREATOR)

    with pytest.raises(RuntimeError, match="identity drifted"):
        retired.revoke_live_managed_query_access(
            workspace,
            app_name=APP,
            endpoint_name=ENDPOINT,
            endpoint_id=ENDPOINT_ID,
            endpoint_creator=CREATOR,
            application_id=VERIFIER,
            scim_id=VERIFIER_SCIM,
            identity_label="verifier",
            assert_single_writer=lambda: None,
            endpoint_identity=drifting_identity,
        )

    assert workspace.groups.patches == []


def test_live_verifier_retirement_lost_lease_blocks_membership_mutation() -> None:
    workspace = _workspace("managed")

    with pytest.raises(RuntimeError, match="lease lost"):
        retired.revoke_live_managed_query_access(
            workspace,
            app_name=APP,
            endpoint_name=ENDPOINT,
            endpoint_id=ENDPOINT_ID,
            endpoint_creator=CREATOR,
            application_id=VERIFIER,
            scim_id=VERIFIER_SCIM,
            identity_label="verifier",
            assert_single_writer=lambda: (_ for _ in ()).throw(RuntimeError("lease lost")),
            endpoint_identity=_endpoint_identity,
        )

    assert workspace.groups.patches == []


def test_nondeletable_gateway_retires_verifier_group_without_deleting_it() -> None:
    workspace = _workspace("managed")
    app_calls: list[str] = []

    retired.delete_pinned_gateway(
        workspace,
        app_name=APP,
        endpoint=ENDPOINT,
        endpoint_id=ENDPOINT_ID,
        creator=CREATOR,
        delete_allowed=False,
        green_endpoint="green-gateway",
        runtime_application_id="runtime-client",
        app_principal="app-client",
        app_principal_id="app-scim-id",
        verifier_application_id=VERIFIER,
        verifier_scim_id=VERIFIER_SCIM,
        timeout_s=1,
        assert_single_writer=lambda: None,
        endpoint_identity=_endpoint_identity,
        revoke_app_access=lambda *_a, **_kw: app_calls.append("app") or "none",
    )

    assert app_calls == ["app"]
    assert workspace.groups.get(f"{VERIFIER}-group-id").members == []
    assert workspace.groups.deleted == []


@pytest.mark.parametrize("mode", ["direct", "mixed"])
def test_nondeletable_gateway_rejects_verifier_before_app_mutation(mode: str) -> None:
    workspace = _workspace(mode)
    app_calls: list[str] = []

    with pytest.raises(RuntimeError, match="verifier access cannot be atomically revoked"):
        retired.delete_pinned_gateway(
            workspace,
            app_name=APP,
            endpoint=ENDPOINT,
            endpoint_id=ENDPOINT_ID,
            creator=CREATOR,
            delete_allowed=False,
            green_endpoint="green-gateway",
            runtime_application_id="runtime-client",
            app_principal="app-client",
            app_principal_id="app-scim-id",
            verifier_application_id=VERIFIER,
            verifier_scim_id=VERIFIER_SCIM,
            timeout_s=1,
            assert_single_writer=lambda: None,
            endpoint_identity=_endpoint_identity,
            revoke_app_access=lambda *_a, **_kw: app_calls.append("app") or "none",
        )

    assert app_calls == []
    assert workspace.groups.patches == []


def test_fresh_gateway_retry_retires_groups_after_committed_endpoint_delete_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_present = True
    delete_calls = 0
    events: list[str] = []

    class _Endpoints:
        def get(self, _name: str) -> object:
            if not endpoint_present:
                raise ResourceDoesNotExist("deleted")
            return SimpleNamespace(id=ENDPOINT_ID, creator="runtime-client")

        def delete(self, name: str) -> None:
            nonlocal endpoint_present, delete_calls
            assert name == ENDPOINT
            delete_calls += 1
            endpoint_present = False
            events.append("delete-endpoint")
            raise TimeoutError("response lost after endpoint deletion committed")

    def identity(_workspace: object, _name: str) -> tuple[str, str]:
        if not endpoint_present:
            raise ResourceDoesNotExist("deleted")
        return ENDPOINT_ID, "runtime-client"

    monkeypatch.setattr(
        retired,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "managed",
    )
    monkeypatch.setattr(
        retired,
        "revoke_live_managed_query_access",
        lambda *_a, **_kw: events.append("empty-verifier-group") or "managed",
    )
    groups = _Groups(managed=True, member_id=None)
    workspace = SimpleNamespace(serving_endpoints=_Endpoints(), groups=groups)
    monkeypatch.setattr(
        "tools.databricks.workspace_group_deletion._POLL_SECONDS",
        0,
    )
    kwargs = {
        "workspace": workspace,
        "app_name": APP,
        "endpoint": ENDPOINT,
        "endpoint_id": ENDPOINT_ID,
        "creator": "runtime-client",
        "delete_allowed": True,
        "green_endpoint": "green-gateway",
        "runtime_application_id": "runtime-client",
        "app_principal": "app-client",
        "app_principal_id": "app-scim-id",
        "verifier_application_id": VERIFIER,
        "verifier_scim_id": VERIFIER_SCIM,
        "timeout_s": 1,
        "assert_single_writer": lambda: events.append("lease"),
        "endpoint_identity": identity,
        "revoke_app_access": (
            lambda *_a, **_kw: events.append("empty-app-group") or "managed"
        ),
    }

    with pytest.raises(TimeoutError, match="committed"):
        retired.delete_pinned_gateway(**kwargs)
    retired.delete_pinned_gateway(**kwargs)

    assert delete_calls == 1
    assert events == [
        "empty-app-group",
        "empty-verifier-group",
        "lease",
        "delete-endpoint",
        "lease",
        "lease",
    ]
    assert groups.deleted == [f"{VERIFIER}-group-id"]


def test_fresh_supervisor_retry_recovers_agent_absent_endpoint_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_present = True
    endpoint_present = True
    delete_agent_calls = 0
    events: list[str] = []
    old_agent = {
        "supervisor_agent_id": "old-supervisor",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": ENDPOINT,
        "creator": CREATOR,
        "create_time": "old-time",
    }

    def identity(_workspace: object, _name: str) -> tuple[str, str]:
        if not endpoint_present:
            raise ResourceDoesNotExist("deleted")
        return ENDPOINT_ID, CREATOR

    class _Endpoints:
        def delete(self, name: str) -> None:
            nonlocal endpoint_present
            assert name == ENDPOINT
            endpoint_present = False
            events.append("delete-endpoint")

        def get(self, _name: str) -> object:
            if not endpoint_present:
                raise ResourceDoesNotExist("deleted")
            return SimpleNamespace(id=ENDPOINT_ID, creator=CREATOR)

    def delete_agent(_args: list[str]) -> None:
        nonlocal agent_present, delete_agent_calls
        delete_agent_calls += 1
        agent_present = False
        events.append("delete-agent")
        raise TimeoutError("response lost after Supervisor deletion committed")

    monkeypatch.setattr(
        retired,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "managed",
    )
    monkeypatch.setattr(
        retired,
        "revoke_live_managed_query_access",
        lambda *_a, **_kw: events.append("empty-proxy-group") or "managed",
    )
    kwargs = {
        "workspace": SimpleNamespace(serving_endpoints=_Endpoints()),
        "app_name": APP,
        "canonical_name": "Mortgage Growth Agent",
        # A runtime-owned blue Supervisor: the reviewed path, no acknowledgement.
        "runtime_application_id": CREATOR,
        "old_id": "old-supervisor",
        "old_endpoint": ENDPOINT,
        "old_endpoint_id": ENDPOINT_ID,
        "old_creator": CREATOR,
        "old_create_time": "old-time",
        "app_principal": "app-client",
        "app_principal_id": "app-scim-id",
        "proxy_application_id": "proxy-client",
        "proxy_scim_id": "proxy-scim-id",
        "cleanup_enabled": True,
        "timeout_s": 1,
        "assert_single_writer": lambda: events.append("lease"),
        "agent_by_id": lambda _id: old_agent if agent_present else None,
        "endpoint_identity": identity,
        "revoke_app_access": (
            lambda *_a, **_kw: events.append("empty-app-group") or "managed"
        ),
        "delete_agent": delete_agent,
        "retire_query_groups": (
            lambda *_a, **_kw: events.append("delete-orphan-groups")
        ),
    }

    with pytest.raises(TimeoutError, match="committed"):
        retired.retire_pinned_supervisor(**kwargs)
    retired.retire_pinned_supervisor(**kwargs)

    assert delete_agent_calls == 1
    assert events == [
        "empty-app-group",
        "empty-proxy-group",
        "lease",
        "delete-agent",
        "empty-app-group",
        "empty-proxy-group",
        "lease",
        "delete-endpoint",
        "delete-orphan-groups",
    ]


def test_fresh_supervisor_retry_retires_groups_after_endpoint_delete_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_present = True
    endpoint_present = True
    endpoint_delete_calls = 0
    events: list[str] = []
    old_agent = {
        "supervisor_agent_id": "old-supervisor",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": ENDPOINT,
        "creator": CREATOR,
        "create_time": "old-time",
    }

    def identity(_workspace: object, _name: str) -> tuple[str, str]:
        if not endpoint_present:
            raise ResourceDoesNotExist("deleted")
        return ENDPOINT_ID, CREATOR

    class _Endpoints:
        def get(self, _name: str) -> object:
            if not endpoint_present:
                raise ResourceDoesNotExist("deleted")
            return SimpleNamespace(id=ENDPOINT_ID, creator=CREATOR)

        def delete(self, name: str) -> None:
            nonlocal endpoint_present, endpoint_delete_calls
            assert name == ENDPOINT
            endpoint_delete_calls += 1
            endpoint_present = False
            events.append("delete-endpoint")
            raise TimeoutError("response lost after endpoint deletion committed")

    def delete_agent(_args: list[str]) -> None:
        nonlocal agent_present
        agent_present = False
        events.append("delete-agent")

    monkeypatch.setattr(
        retired,
        "inspect_gateway_query_access_mode",
        lambda *_a, **_kw: "managed",
    )
    monkeypatch.setattr(
        retired,
        "revoke_live_managed_query_access",
        lambda *_a, **_kw: events.append("empty-proxy-group") or "managed",
    )
    groups = _Groups(
        managed=True,
        member_id=None,
        application_id="proxy-client",
        scim_id="proxy-scim-id",
    )
    monkeypatch.setattr(
        "tools.databricks.workspace_group_deletion._POLL_SECONDS",
        0,
    )
    kwargs = {
        "workspace": SimpleNamespace(serving_endpoints=_Endpoints(), groups=groups),
        "app_name": APP,
        "canonical_name": "Mortgage Growth Agent",
        # A runtime-owned blue Supervisor: the reviewed path, no acknowledgement.
        "runtime_application_id": CREATOR,
        "old_id": "old-supervisor",
        "old_endpoint": ENDPOINT,
        "old_endpoint_id": ENDPOINT_ID,
        "old_creator": CREATOR,
        "old_create_time": "old-time",
        "app_principal": "app-client",
        "app_principal_id": "app-scim-id",
        "proxy_application_id": "proxy-client",
        "proxy_scim_id": "proxy-scim-id",
        "cleanup_enabled": True,
        "timeout_s": 1,
        "assert_single_writer": lambda: events.append("lease"),
        "agent_by_id": lambda _id: old_agent if agent_present else None,
        "endpoint_identity": identity,
        "revoke_app_access": (
            lambda *_a, **_kw: events.append("empty-app-group") or "managed"
        ),
        "delete_agent": delete_agent,
    }

    with pytest.raises(TimeoutError, match="committed"):
        retired.retire_pinned_supervisor(**kwargs)
    retired.retire_pinned_supervisor(**kwargs)

    assert endpoint_delete_calls == 1
    assert events == [
        "empty-app-group",
        "empty-proxy-group",
        "lease",
        "delete-agent",
        "lease",
        "delete-endpoint",
        "lease",
        "lease",
    ]
    assert groups.deleted == ["proxy-client-group-id"]


# --- Retirement acknowledgement at the deletion itself -------------------------

HUMAN_CREATOR = "operator@summit.example"
RUNTIME = "runtime-client"


def _supervisor_retirement(
    events: list[str],
    *,
    creator: str,
    agent_present: bool = True,
    endpoint_present: bool = True,
) -> dict[str, object]:
    state = {"agent": agent_present, "endpoint": endpoint_present}
    old_agent = {
        "supervisor_agent_id": "old-supervisor",
        "display_name": "Mortgage Growth Agent",
        "endpoint_name": ENDPOINT,
        "creator": creator,
        "create_time": "old-time",
    }

    def identity(_workspace: object, _name: str) -> tuple[str, str]:
        if not state["endpoint"]:
            raise ResourceDoesNotExist("deleted")
        return ENDPOINT_ID, creator

    def get_endpoint(_name: str) -> object:
        identity(None, _name)
        return SimpleNamespace(id=ENDPOINT_ID, creator=creator)

    def delete_endpoint(_name: str) -> None:
        state["endpoint"] = False
        events.append("delete-endpoint")

    def delete_agent(_args: list[str]) -> None:
        state["agent"] = state["endpoint"] = False
        events.append("delete-agent")

    return {
        "workspace": SimpleNamespace(
            serving_endpoints=SimpleNamespace(get=get_endpoint, delete=delete_endpoint)
        ),
        "app_name": APP,
        "canonical_name": "Mortgage Growth Agent",
        "runtime_application_id": RUNTIME,
        "old_id": "old-supervisor",
        "old_endpoint": ENDPOINT,
        "old_endpoint_id": ENDPOINT_ID,
        "old_creator": creator,
        "old_create_time": "old-time",
        "app_principal": "app-client",
        "app_principal_id": "app-scim-id",
        "proxy_application_id": None,
        "proxy_scim_id": None,
        "cleanup_enabled": False,
        "timeout_s": 1,
        "assert_single_writer": lambda: events.append("lease"),
        "agent_by_id": lambda _id: old_agent if state["agent"] else None,
        "endpoint_identity": identity,
        "revoke_app_access": lambda *_a, **_kw: events.append("revoke-app") or "managed",
        "delete_agent": delete_agent,
    }


@pytest.mark.parametrize(
    ("acknowledgement", "reason"),
    [
        (None, "export MIP_RETIRE_HUMAN_SUPERVISOR=old-supervisor"),
        ("1", "must be the Supervisor id itself, not a boolean"),
        ("other-supervisor", "names 'other-supervisor', not this Supervisor"),
    ],
)
def test_retirement_refuses_an_unacknowledged_non_runtime_supervisor_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch, acknowledgement: str | None, reason: str
) -> None:
    # A journal pinned by code that predates pin_journal's check, or resumed in
    # a later run, reaches the deletion without that check in this process.
    if acknowledgement is None:
        monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)
    else:
        monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, acknowledgement)
    events: list[str] = []

    with pytest.raises(ack.UnacknowledgedSupervisorRetirement, match=reason):
        retired.retire_pinned_supervisor(
            **_supervisor_retirement(events, creator=HUMAN_CREATOR)
        )

    assert events == []


def test_retirement_deletes_a_non_runtime_supervisor_the_operator_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, "old-supervisor")
    events: list[str] = []

    retired.retire_pinned_supervisor(**_supervisor_retirement(events, creator=HUMAN_CREATOR))

    assert events == ["revoke-app", "lease", "delete-agent"]


def test_retirement_of_a_runtime_owned_supervisor_needs_no_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)
    events: list[str] = []

    retired.retire_pinned_supervisor(**_supervisor_retirement(events, creator=RUNTIME))

    assert events == ["revoke-app", "lease", "delete-agent"]


@pytest.mark.parametrize(
    ("acknowledgement", "reason"),
    [
        (None, "export MIP_RETIRE_HUMAN_SUPERVISOR=old-supervisor"),
        ("1", "must be the Supervisor id itself, not a boolean"),
        ("other-supervisor", "names 'other-supervisor', not this Supervisor"),
    ],
)
def test_orphan_endpoint_cleanup_refuses_an_unacknowledged_non_runtime_endpoint(
    monkeypatch: pytest.MonkeyPatch, acknowledgement: str | None, reason: str
) -> None:
    # The Supervisor is gone (deleted by an earlier run or out of band), but its
    # serving endpoint, created by the same non-runtime principal, would still
    # be deleted permanently. That needs the same acknowledgement, before the
    # first mutation, even though the pre-stop probe cannot see this case.
    if acknowledgement is None:
        monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)
    else:
        monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, acknowledgement)
    events: list[str] = []

    with pytest.raises(ack.UnacknowledgedSupervisorRetirement) as refused:
        retired.retire_pinned_supervisor(
            **_supervisor_retirement(events, creator=HUMAN_CREATOR, agent_present=False)
        )

    message = str(refused.value)
    assert reason in message
    assert (
        f"Supervisor 'old-supervisor' is already deleted, but its serving endpoint "
        f"{ENDPOINT!r} remains; both were created by {HUMAN_CREATOR!r}"
    ) in message
    assert "permanently delete that orphan serving endpoint" in message
    assert events == []


def test_orphan_endpoint_cleanup_deletes_once_the_operator_names_the_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ack.ACKNOWLEDGEMENT_ENV, "old-supervisor")
    events: list[str] = []

    retired.retire_pinned_supervisor(
        **_supervisor_retirement(events, creator=HUMAN_CREATOR, agent_present=False)
    )

    assert events == ["revoke-app", "lease", "delete-endpoint"]


def test_orphan_endpoint_cleanup_of_a_runtime_owned_endpoint_needs_no_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)
    events: list[str] = []

    retired.retire_pinned_supervisor(
        **_supervisor_retirement(events, creator=RUNTIME, agent_present=False)
    )

    assert events == ["revoke-app", "lease", "delete-endpoint"]


def test_a_retirement_with_nothing_left_to_delete_needs_no_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Supervisor and endpoint are both gone: only the deploy-owned query-group
    # cleanup remains, and nothing a person created is deleted.
    monkeypatch.delenv(ack.ACKNOWLEDGEMENT_ENV, raising=False)
    events: list[str] = []

    retired.retire_pinned_supervisor(
        **_supervisor_retirement(
            events, creator=HUMAN_CREATOR, agent_present=False, endpoint_present=False
        )
    )

    assert events == []
