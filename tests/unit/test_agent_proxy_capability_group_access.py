from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tools.databricks import agent_proxy_acl_support as acl_support
from tools.databricks import agent_proxy_capability_group_access as access

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _disable_real_projection_sleeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "_PROJECTION_POLL_SECONDS", 0.0)


class _Groups:
    def __init__(self, groups: tuple[object, ...] = ()) -> None:
        self.by_id = {
            str(cast(SimpleNamespace, group).id): group for group in groups
        }
        self.patch_calls: list[dict[str, object]] = []
        self.create_calls = 0

    def list(
        self,
        *,
        filter: str | None = None,
        attributes: str | None = None,
    ) -> list[object]:
        del attributes
        groups = list(self.by_id.values())
        if filter is None:
            return groups
        name = filter.removeprefix("displayName eq '").removesuffix("'")
        return [
            group
            for group in groups
            if cast(SimpleNamespace, group).display_name == name
        ]

    def get(self, group_id: str) -> object:
        return self.by_id[group_id]

    def create(self, *, display_name: str, external_id: str) -> object:
        self.create_calls += 1
        group = _group(
            group_id=f"group-{len(self.by_id) + 1}",
            name=display_name,
            external_id=external_id,
        )
        self.by_id[str(group.id)] = group
        return group

    def patch(self, **kwargs: object) -> None:
        self.patch_calls.append(kwargs)
        group = self.by_id[str(kwargs["id"])]
        operations = list(kwargs["operations"])  # type: ignore[arg-type]
        assert len(operations) == 1
        operation = cast(Any, operations[0])
        op = str(getattr(operation.op, "value", operation.op)).lower()
        if op == "add":
            value = operation.value
            members = value["members"]  # type: ignore[index]
            group.members.extend(  # type: ignore[attr-defined]
                SimpleNamespace(value=str(member["value"])) for member in members
            )
            return
        assert op == "remove"
        group.members = []  # type: ignore[attr-defined]


def _group(
    *,
    group_id: str,
    name: str,
    external_id: str,
    members: tuple[str, ...] = (),
) -> object:
    return SimpleNamespace(
        id=group_id,
        display_name=name,
        external_id=external_id,
        members=[SimpleNamespace(value=member) for member in members],
        meta=SimpleNamespace(resource_type="WorkspaceGroup"),
    )


def _managed_group(
    *,
    resource_kind: access.ManagedAgentProxyResourceKind = "supervisor",
    resource_id: str = "resource-id",
    application_id: str = "proxy-client",
    members: tuple[str, ...] = (),
) -> object:
    return _group(
        group_id=f"{resource_kind}-{resource_id}",
        name=access.managed_agent_proxy_group_name(
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        ),
        external_id=access.managed_agent_proxy_group_external_id(
            resource_kind=resource_kind,
            resource_id=resource_id,
            application_id=application_id,
        ),
        members=members,
    )


def test_resource_groups_have_distinct_bounded_deterministic_contracts() -> None:
    supervisor = access.managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="resource-id",
        application_id="proxy-client",
    )
    genie = access.managed_agent_proxy_group_name(
        resource_kind="genie",
        resource_id="resource-id",
        application_id="proxy-client",
    )
    other = access.managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="other-id",
        application_id="proxy-client",
    )

    assert supervisor != genie != other
    assert len(supervisor) <= 63
    assert len(
        access.managed_agent_proxy_group_external_id(
            resource_kind="supervisor",
            resource_id="resource-id",
            application_id="proxy-client",
        )
    ) <= 64


def test_normal_capability_lifecycle_never_writes_direct_proxy_acl() -> None:
    convergence = (
        REPO / "tools/databricks/agent_proxy_capability_convergence.py"
    ).read_text(encoding="utf-8")
    denial = (
        REPO / "tools/databricks/agent_proxy_capability_denial.py"
    ).read_text(encoding="utf-8")

    assert '"service_principal_name"' not in convergence
    assert '"service_principal_name"' not in denial
    assert "NO_PERMISSIONS" not in convergence + denial
    assert '"PUT"' not in convergence + denial


def test_membership_add_remove_is_exact_and_idempotent() -> None:
    groups = _Groups()
    workspace = SimpleNamespace(groups=groups)

    assert access.set_managed_agent_proxy_membership(
        workspace,
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=True,
        assert_single_writer=lambda: None,
    )
    assert not access.set_managed_agent_proxy_membership(
        workspace,
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=True,
        assert_single_writer=lambda: None,
    )
    state = access.inspect_managed_agent_proxy_group(
        workspace,
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
    )
    assert state is not None
    assert state.member_ids == ("proxy-scim",)

    assert access.set_managed_agent_proxy_membership(
        workspace,
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=False,
        assert_single_writer=lambda: None,
    )
    assert not access.set_managed_agent_proxy_membership(
        workspace,
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=False,
        assert_single_writer=lambda: None,
    )
    assert len(groups.patch_calls) == 2


def test_create_waits_for_exact_filtered_scim_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EventuallyListedGroups(_Groups):
        created = False
        hidden_reads = 0

        def create(self, *, display_name: str, external_id: str) -> object:
            group = super().create(
                display_name=display_name,
                external_id=external_id,
            )
            self.created = True
            return group

        def list(
            self,
            *,
            filter: str | None = None,
            attributes: str | None = None,
        ) -> list[object]:
            if self.created and filter is not None and self.hidden_reads < 2:
                self.hidden_reads += 1
                return []
            return super().list(filter=filter, attributes=attributes)

    monkeypatch.setattr(access, "_PROJECTION_POLL_SECONDS", 0.0)
    groups = _EventuallyListedGroups()

    assert access.set_managed_agent_proxy_membership(
        SimpleNamespace(groups=groups),
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=True,
        assert_single_writer=lambda: None,
    )

    assert groups.hidden_reads == 2
    assert len(groups.by_id) == 1
    assert len(groups.patch_calls) == 1


def test_precreate_reconciliation_reuses_initially_hidden_existing_group() -> None:
    managed = cast(
        SimpleNamespace,
        _managed_group(resource_id="supervisor-id"),
    )

    class _InitiallyHiddenExistingGroups(_Groups):
        filtered_reads = 0
        full_reads = 0

        def list(
            self,
            *,
            filter: str | None = None,
            attributes: str | None = None,
        ) -> list[object]:
            if filter is not None:
                self.filtered_reads += 1
                return []
            self.full_reads += 1
            if self.full_reads <= 2:
                return []
            return super().list(filter=filter, attributes=attributes)

    groups = _InitiallyHiddenExistingGroups((managed,))
    state = access.ensure_managed_agent_proxy_group(
        SimpleNamespace(groups=groups),
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        assert_single_writer=lambda: None,
    )

    assert state.contract.id == managed.id
    assert groups.create_calls == 0
    assert len(groups.by_id) == 1
    assert groups.filtered_reads == 3
    assert groups.full_reads == 3


def test_discovery_rejects_wrong_immutable_group_id() -> None:
    managed = cast(
        SimpleNamespace,
        _managed_group(resource_id="supervisor-id"),
    )
    groups = _Groups((managed,))

    with pytest.raises(RuntimeError, match="immutable identity drifted"):
        access.wait_for_managed_agent_proxy_group_discovery(
            SimpleNamespace(groups=groups),
            resource_kind="supervisor",
            resource_id="supervisor-id",
            application_id="proxy-client",
            expected_contract=access.ManagedAgentProxyGroup(
                id="replacement-id",
                name=str(managed.display_name),
                external_id=str(managed.external_id),
            ),
        )


def test_discovery_rejects_duplicate_or_drifted_contract() -> None:
    first = cast(
        SimpleNamespace,
        _managed_group(resource_id="supervisor-id"),
    )
    duplicate = _group(
        group_id="duplicate-id",
        name=str(first.display_name),
        external_id=str(first.external_id),
    )
    with pytest.raises(RuntimeError, match="duplicated"):
        access.wait_for_managed_agent_proxy_group_discovery(
            SimpleNamespace(groups=_Groups((first, duplicate))),
            resource_kind="supervisor",
            resource_id="supervisor-id",
            application_id="proxy-client",
        )

    drifted = _group(
        group_id="drifted-id",
        name=str(first.display_name),
        external_id="wrong-external-id",
    )
    with pytest.raises(RuntimeError, match="contract drifted"):
        access.wait_for_managed_agent_proxy_group_discovery(
            SimpleNamespace(groups=_Groups((drifted,))),
            resource_kind="supervisor",
            resource_id="supervisor-id",
            application_id="proxy-client",
        )


def test_discovery_timeout_remains_fail_closed() -> None:
    times = iter((0.0, 0.25, 0.5))

    with pytest.raises(RuntimeError, match="discovery did not converge"):
        access.wait_for_managed_agent_proxy_group_discovery(
            SimpleNamespace(groups=_Groups()),
            resource_kind="supervisor",
            resource_id="supervisor-id",
            application_id="proxy-client",
            sleep=lambda _seconds: None,
            clock=lambda: next(times),
            deadline_seconds=0.5,
        )


def test_capability_group_create_requires_live_lease_at_mutation_boundary() -> None:
    class _NoCreateAfterLeaseLoss(_Groups):
        def create(self, *, display_name: str, external_id: str) -> object:
            pytest.fail(
                f"lease loss must prevent group create: {display_name=} {external_id=}"
            )

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        access.ensure_managed_agent_proxy_group(
            SimpleNamespace(groups=_NoCreateAfterLeaseLoss()),
            resource_kind="supervisor",
            resource_id="supervisor-id",
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
        )


def test_capability_membership_patch_rechecks_lease_after_hydration() -> None:
    managed = _managed_group(members=())
    groups = _Groups((managed,))

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        access.set_managed_agent_proxy_membership(
            SimpleNamespace(groups=groups),
            resource_kind="supervisor",
            resource_id="resource-id",
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            active=True,
            assert_single_writer=lambda: (_ for _ in ()).throw(
                RuntimeError("deployment lease lost")
            ),
        )

    assert groups.patch_calls == []


def test_blue_green_memberships_converge_then_retire_blue_only() -> None:
    groups = _Groups()
    workspace = SimpleNamespace(groups=groups)
    for resource_id in ("blue-supervisor", "green-supervisor"):
        assert access.set_managed_agent_proxy_membership(
            workspace,
            resource_kind="supervisor",
            resource_id=resource_id,
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            active=True,
            assert_single_writer=lambda: None,
        )

    assert access.set_managed_agent_proxy_membership(
        workspace,
        resource_kind="supervisor",
        resource_id="blue-supervisor",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=False,
        assert_single_writer=lambda: None,
    )
    blue = access.inspect_managed_agent_proxy_group(
        workspace,
        resource_kind="supervisor",
        resource_id="blue-supervisor",
        application_id="proxy-client",
    )
    green = access.inspect_managed_agent_proxy_group(
        workspace,
        resource_kind="supervisor",
        resource_id="green-supervisor",
        application_id="proxy-client",
    )

    assert blue is not None and blue.member_ids == ()
    assert green is not None and green.member_ids == ("proxy-scim",)


def test_commit_then_error_membership_retry_is_idempotent() -> None:
    class _CommitThenError(_Groups):
        failed = False

        def patch(self, **kwargs: object) -> None:
            super().patch(**kwargs)
            if not self.failed:
                self.failed = True
                raise RuntimeError("response lost after commit")

    groups = _CommitThenError()
    workspace = SimpleNamespace(groups=groups)

    with pytest.raises(RuntimeError, match="response lost"):
        access.set_managed_agent_proxy_membership(
            workspace,
            resource_kind="genie",
            resource_id="genie-id",
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            active=True,
            assert_single_writer=lambda: None,
        )
    assert not access.set_managed_agent_proxy_membership(
        workspace,
        resource_kind="genie",
        resource_id="genie-id",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        active=True,
        assert_single_writer=lambda: None,
    )
    state = access.inspect_managed_agent_proxy_group(
        workspace,
        resource_kind="genie",
        resource_id="genie-id",
        application_id="proxy-client",
    )

    assert state is not None and state.member_ids == ("proxy-scim",)
    assert len(groups.patch_calls) == 1


def test_unrelated_member_drift_is_preserved_and_never_mutated() -> None:
    managed = _managed_group(members=("proxy-scim", "unrelated-scim"))
    groups = _Groups((managed,))

    with pytest.raises(RuntimeError, match="unrelated member"):
        access.set_managed_agent_proxy_membership(
            SimpleNamespace(groups=groups),
            resource_kind="supervisor",
            resource_id="resource-id",
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            active=False,
            assert_single_writer=lambda: None,
        )

    assert groups.patch_calls == []
    assert {member.value for member in cast(Any, managed).members} == {
        "proxy-scim",
        "unrelated-scim",
    }


def test_reserved_namespace_malformed_group_fails_closed() -> None:
    groups = _Groups(
        (
            _group(
                group_id="malformed",
                name=f"{access.MANAGED_AGENT_PROXY_GROUP_PREFIX}malformed",
                external_id=f"{access.MANAGED_AGENT_PROXY_GROUP_EXTERNAL_ID_PREFIX}bad",
            ),
        )
    )

    with pytest.raises(RuntimeError, match="reserved"):
        access.managed_agent_proxy_groups_for_application(
            SimpleNamespace(groups=groups),
            application_id="proxy-client",
            service_principal_id="proxy-scim",
        )


def test_proxy_membership_in_another_application_group_fails_closed() -> None:
    groups = _Groups(
        (
            _managed_group(
                application_id="other-client",
                members=("proxy-scim",),
            ),
        )
    )

    with pytest.raises(RuntimeError, match="another application's"):
        access.managed_agent_proxy_groups_for_application(
            SimpleNamespace(groups=groups),
            application_id="proxy-client",
            service_principal_id="proxy-scim",
        )


def test_projection_wait_retries_until_exact_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "_PROJECTION_POLL_SECONDS", 2.0)
    managed = _managed_group(members=("proxy-scim",))
    groups = _Groups((managed,))
    workspace = SimpleNamespace(groups=groups)
    projections = iter(({}, {}, {str(managed.id): str(managed.display_name)}))
    times = iter((0.0, 0.5, 1.0, 1.5))
    sleeps: list[float] = []
    monkeypatch.setattr(
        access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: next(projections),
    )

    effective = access.wait_for_managed_agent_proxy_group_projection(
        workspace,
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        expected_active_group_names={str(managed.display_name)},
        sleep=sleeps.append,
        clock=lambda: next(times),
        deadline_seconds=2.0,
    )

    assert effective == {str(managed.display_name)}
    assert sleeps == [2.0, 2.0]


def test_projection_wait_retries_until_expected_group_is_in_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "_PROJECTION_POLL_SECONDS", 2.0)
    managed = cast(
        SimpleNamespace,
        _managed_group(members=("proxy-scim",)),
    )

    class _EventuallyInventoriedGroups(_Groups):
        hidden_reads = 0

        def list(
            self,
            *,
            filter: str | None = None,
            attributes: str | None = None,
        ) -> list[object]:
            if filter is None and self.hidden_reads < 2:
                self.hidden_reads += 1
                return []
            return super().list(filter=filter, attributes=attributes)

    groups = _EventuallyInventoriedGroups((managed,))
    sleeps: list[float] = []
    times = iter((0.0, 0.25, 0.5))
    monkeypatch.setattr(
        access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {
            str(managed.id): str(managed.display_name)
        },
    )

    effective = access.wait_for_managed_agent_proxy_group_projection(
        SimpleNamespace(groups=groups),
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        expected_active_group_names={str(managed.display_name)},
        sleep=sleeps.append,
        clock=lambda: next(times),
        deadline_seconds=1.0,
    )

    assert effective == {str(managed.display_name)}
    assert groups.hidden_reads == 2
    assert sleeps == [2.0, 2.0]


def test_projection_wait_fails_closed_when_expected_group_never_appears() -> None:
    times = iter((0.0, 0.25, 0.5))

    with pytest.raises(
        RuntimeError,
        match="expected managed agent-proxy group discovery did not converge",
    ):
        access.wait_for_managed_agent_proxy_group_projection(
            SimpleNamespace(groups=_Groups()),
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            expected_active_group_names={
                access.managed_agent_proxy_group_name(
                    resource_kind="supervisor",
                    resource_id="supervisor-id",
                    application_id="proxy-client",
                )
            },
            sleep=lambda _seconds: None,
            clock=lambda: next(times),
            deadline_seconds=0.5,
        )


def test_projection_wait_rejects_hidden_reserved_effective_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden_name = access.managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
    )
    times = iter((0.0, 0.25, 0.5))
    monkeypatch.setattr(
        access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {"hidden-id": hidden_name},
    )

    with pytest.raises(
        RuntimeError,
        match="effective-group projection did not converge",
    ):
        access.wait_for_managed_agent_proxy_group_projection(
            SimpleNamespace(groups=_Groups()),
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            expected_active_group_names=set(),
            sleep=lambda _seconds: None,
            clock=lambda: next(times),
            deadline_seconds=0.5,
        )


def test_zero_projection_requires_three_clean_observations_after_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = cast(
        SimpleNamespace,
        _managed_group(members=("proxy-scim",)),
    )
    states = (
        (),
        (),
        (
            access.ManagedAgentProxyGroupState(
                contract=access.ManagedAgentProxyGroup(
                    id=str(managed.id),
                    name=str(managed.display_name),
                    external_id=str(managed.external_id),
                ),
                member_ids=("proxy-scim",),
            ),
        ),
        (),
        (),
        (),
    )
    state_iter = iter(states)
    inventory_reads = 0
    effective_reads = 0

    def inventory(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        nonlocal inventory_reads
        inventory_reads += 1
        return next(state_iter)

    def effective(*_args: object, **_kwargs: object) -> dict[str, str]:
        nonlocal effective_reads
        effective_reads += 1
        return {}

    monkeypatch.setattr(
        access,
        "managed_agent_proxy_groups_for_application",
        inventory,
    )
    monkeypatch.setattr(access, "resolve_effective_groups", effective)
    sleeps: list[float] = []
    times = iter((0.0, 0.1, 0.2, 0.3, 0.4, 0.5))

    assert access.wait_for_managed_agent_proxy_group_projection(
        SimpleNamespace(),
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        expected_active_group_names=set(),
        sleep=sleeps.append,
        clock=lambda: next(times),
        deadline_seconds=1.0,
    ) == set()
    assert inventory_reads == 6
    assert effective_reads == 5
    assert sleeps == [0.0] * 5


def test_exact_projection_cannot_drop_a_required_group_from_stale_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = access.managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="supervisor-id",
        application_id="proxy-client",
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        acl_support,
        "managed_agent_proxy_groups_for_application",
        lambda *_args, **_kwargs: (),
    )

    def wait(*_args: object, **kwargs: object) -> set[str]:
        observed.update(kwargs)
        return {required}

    monkeypatch.setattr(
        acl_support,
        "wait_for_managed_agent_proxy_group_projection",
        wait,
    )

    assert acl_support.wait_exact_capability_projection(
        SimpleNamespace(),
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        required_active_group_names={required},
    ) == {required}
    assert observed["expected_active_group_names"] == {required}


def test_scim_member_postflight_retries_eventual_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access, "_PROJECTION_POLL_SECONDS", 2.0)
    empty = _managed_group()
    active = _managed_group(members=("proxy-scim",))
    responses = iter((empty, empty, active))
    workspace = SimpleNamespace(
        groups=SimpleNamespace(get=lambda _group_id: next(responses))
    )
    times = iter((0.0, 0.5, 1.0, 1.5))
    sleeps: list[float] = []

    observed = access._wait_member_ids(
        workspace,
        group_id=str(active.id),
        expected_member_ids={"proxy-scim"},
        sleep=sleeps.append,
        clock=lambda: next(times),
    )

    assert observed is active
    assert sleeps == [2.0, 2.0]


def test_legacy_direct_cleanup_is_allowed_only_before_managed_group_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permissions = {
        "access_control_list": [
            {
                "service_principal_name": "proxy-client",
                "all_permissions": [
                    {
                        "permission_level": "CAN_QUERY",
                        "inherited": False,
                    }
                ],
            }
        ]
    }
    mutations: list[str] = []
    monkeypatch.setattr(
        acl_support,
        "replace_direct_acl_without_principal",
        lambda *_args, **_kwargs: mutations.append("legacy-put"),
    )
    empty_workspace = SimpleNamespace(groups=_Groups())
    acl_support.migrate_legacy_direct_acl_if_unmanaged(
        empty_workspace,
        path="/api/2.0/permissions/supervisor-agents/resource-id",
        permissions=permissions,
        resource_kind="supervisor",
        resource_id="resource-id",
        application_id="proxy-client",
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )
    assert mutations == ["legacy-put"]

    managed_workspace = SimpleNamespace(
        groups=_Groups((_managed_group(),)),
    )
    with pytest.raises(RuntimeError, match="unexpected direct"):
        acl_support.migrate_legacy_direct_acl_if_unmanaged(
            managed_workspace,
            path="/api/2.0/permissions/supervisor-agents/resource-id",
            permissions=permissions,
            resource_kind="supervisor",
            resource_id="resource-id",
            application_id="proxy-client",
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=lambda: None,
        )
    assert mutations == ["legacy-put"]


@pytest.mark.parametrize("level", ["CAN_MANAGE", "CAN_EDIT"])
def test_dormant_managed_group_rejects_broader_acl(level: str) -> None:
    group_name = access.managed_agent_proxy_group_name(
        resource_kind="genie",
        resource_id="genie-id",
        application_id="proxy-client",
    )
    permissions: dict[str, Any] = {
        "access_control_list": [
            {
                "group_name": group_name,
                "all_permissions": [
                    {
                        "permission_level": level,
                        "inherited": False,
                    }
                ],
            }
        ]
    }

    with pytest.raises(RuntimeError, match="managed-group ACL postflight"):
        acl_support.assert_managed_capability_acl(
            permissions,
            application_id="proxy-client",
            effective_group_names=set(),
            managed_group_name=group_name,
            expect_active=False,
            expected_level="CAN_RUN",
            resource="Genie genie-id",
        )
