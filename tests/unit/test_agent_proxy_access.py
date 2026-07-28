from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from tools.databricks import agent_proxy_access, deployment_lease_authority
from tools.databricks import agent_proxy_acl_support as acl_support
from tools.databricks import agent_proxy_capability_convergence as capability_convergence
from tools.databricks import agent_proxy_capability_denial as capability_denial
from tools.databricks.agent_proxy_capability_group_access import (
    ManagedAgentProxyGroup,
    ManagedAgentProxyGroupState,
    managed_agent_proxy_group_name,
)
from tools.databricks.legacy_permissions_acl_cleanup import (
    replace_direct_acl_without_principal,
    stopped_deployment_app_assertion,
)


def _permission_entry(
    *,
    principal: str,
    level: str,
    inherited: bool = False,
    group: bool = False,
) -> dict[str, object]:
    return {
        ("group_name" if group else "service_principal_name"): principal,
        "all_permissions": [
            {
                "permission_level": level,
                "inherited": inherited,
            }
        ],
    }


def test_global_supervisor_acl_requires_exact_target_only() -> None:
    group_name = managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="agent-target",
        application_id="proxy-client",
    )
    permissions = {
        "access_control_list": [
            _permission_entry(
                principal=group_name,
                level="CAN_QUERY",
                group=True,
            ),
        ]
    }
    acl_support.assert_managed_capability_acl(
        permissions,
        application_id="proxy-client",
        effective_group_names={group_name},
        managed_group_name=group_name,
        expect_active=True,
        expected_level="CAN_QUERY",
        resource="Supervisor agent-target",
    )
    acl_support.assert_managed_capability_acl(
        permissions,
        application_id="proxy-client",
        effective_group_names=set(),
        managed_group_name=group_name,
        expect_active=False,
        expected_level="CAN_QUERY",
        resource="Supervisor agent-target",
    )


@pytest.mark.parametrize(
    "permissions",
    [
        {
            "access_control_list": [
                _permission_entry(principal="proxy-client", level="CAN_MANAGE"),
            ]
        },
        {
            "access_control_list": [
                _permission_entry(
                    principal="proxy-client",
                    level="CAN_QUERY",
                    inherited=True,
                ),
            ]
        },
        {
            "access_control_list": [
                _permission_entry(principal="proxy-client", level="CAN_QUERY"),
                _permission_entry(
                    principal="proxy-group",
                    level="CAN_QUERY",
                    inherited=True,
                    group=True,
                ),
            ]
        },
    ],
)
def test_supervisor_acl_rejects_broader_or_inherited_access(
    permissions: dict[str, object],
) -> None:
    group_name = managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="agent-target",
        application_id="proxy-client",
    )
    with pytest.raises(RuntimeError):
        acl_support.assert_managed_capability_acl(
            permissions,
            application_id="proxy-client",
            effective_group_names={"proxy-group"},
            managed_group_name=group_name,
            expect_active=True,
            expected_level="CAN_QUERY",
            resource="Supervisor agent-target",
        )


def test_supervisor_acl_rejects_duplicate_proxy_entries_regardless_of_level() -> None:
    permissions = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(principal="proxy-client", level="CAN_MANAGE"),
        ]
    }

    with pytest.raises(RuntimeError, match="duplicate entries"):
        acl_support.assert_managed_capability_acl(
            permissions,
            application_id="proxy-client",
            effective_group_names=set(),
            managed_group_name=managed_agent_proxy_group_name(
                resource_kind="supervisor",
                resource_id="agent-target",
                application_id="proxy-client",
            ),
            expect_active=False,
            expected_level="CAN_QUERY",
            resource="Supervisor agent-target",
        )


def test_supervisor_inventory_follows_all_pages() -> None:
    calls: list[dict[str, object]] = []

    class _Api:
        def do(self, method: str, path: str, *, query: dict[str, object]):
            calls.append(query)
            assert (method, path) == ("GET", "/api/2.1/supervisor-agents")
            if "page_token" not in query:
                return {
                    "supervisor_agents": [
                        {"supervisor_agent_id": "agent-1", "display_name": "One"}
                    ],
                    "next_page_token": "next",
                }
            return {
                "supervisor_agents": [{"supervisor_agent_id": "agent-2", "display_name": "Two"}]
            }

    assert agent_proxy_access._supervisor_agents(SimpleNamespace(api_client=_Api())) == {
        "agent-1": "One",
        "agent-2": "Two",
    }
    assert calls == [{"page_size": 100}, {"page_size": 100, "page_token": "next"}]


def test_legacy_acl_cleanup_preserves_exact_peers_under_signed_fence() -> None:
    initial = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(
                principal="admins",
                level="CAN_MANAGE",
                group=True,
            ),
            {
                "user_name": "reviewer@example.com",
                "all_permissions": [
                    {
                        "permission_level": "CAN_QUERY",
                        "inherited": False,
                    }
                ],
            },
        ]
    }
    current = initial
    put_bodies: list[dict[str, object]] = []
    fence_calls = 0

    class _Api:
        def do(
            self,
            method: str,
            _path: str,
            *,
            body: dict[str, object] | None = None,
        ) -> object:
            nonlocal current
            if method == "GET":
                return current
            assert method == "PUT"
            assert body is not None
            put_bodies.append(body)
            current = {
                "access_control_list": [
                    {
                        **cast(dict[str, object], request),
                        "all_permissions": [
                            {
                                "permission_level": cast(
                                    dict[str, object],
                                    request,
                                )["permission_level"],
                                "inherited": False,
                            }
                        ],
                    }
                    for request in cast(list[object], body["access_control_list"])
                ]
            }
            return current

    def fence() -> None:
        nonlocal fence_calls
        fence_calls += 1

    replace_direct_acl_without_principal(
        SimpleNamespace(api_client=_Api()),
        path="/api/2.0/permissions/supervisor-agents/target",
        permissions=initial,
        application_id="proxy-client",
        assert_single_writer=fence,
        assert_legacy_cleanup_quiesced=lambda: None,
    )

    assert fence_calls == 2
    assert put_bodies == [
        {
            "access_control_list": [
                {
                    "group_name": "admins",
                    "permission_level": "CAN_MANAGE",
                },
                {
                    "user_name": "reviewer@example.com",
                    "permission_level": "CAN_QUERY",
                },
            ]
        }
    ]


def test_legacy_acl_cleanup_rechecks_lease_after_final_read_before_put() -> None:
    initial = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(principal="admins", level="CAN_MANAGE", group=True),
        ]
    }
    methods: list[str] = []
    fence_calls = 0

    class _Api:
        def do(
            self,
            method: str,
            _path: str,
            *,
            body: object | None = None,
        ) -> object:
            methods.append(method)
            if method == "GET":
                return initial
            pytest.fail(f"lease loss must prevent ACL mutation: {body!r}")

    def expire_after_final_read() -> None:
        nonlocal fence_calls
        fence_calls += 1
        if fence_calls == 2:
            raise RuntimeError("deployment lease lost")

    with pytest.raises(RuntimeError, match="deployment lease lost"):
        replace_direct_acl_without_principal(
            SimpleNamespace(api_client=_Api()),
            path="/api/2.0/permissions/supervisor-agents/target",
            permissions=initial,
            application_id="proxy-client",
            assert_single_writer=expire_after_final_read,
            assert_legacy_cleanup_quiesced=lambda: None,
        )

    assert methods == ["GET", "GET"]


def test_legacy_acl_cleanup_rechecks_stopped_app_after_final_read() -> None:
    initial = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(principal="admins", level="CAN_MANAGE", group=True),
        ]
    }
    methods: list[str] = []
    quiescence_checks = 0

    class _Api:
        def do(
            self,
            method: str,
            _path: str,
            *,
            body: object | None = None,
        ) -> object:
            methods.append(method)
            if method == "GET":
                return initial
            pytest.fail(f"App restart must prevent ACL mutation: {body!r}")

    def app_starts_after_final_read() -> None:
        nonlocal quiescence_checks
        quiescence_checks += 1
        if quiescence_checks == 2:
            raise RuntimeError("deployment App must be STOPPED")

    with pytest.raises(RuntimeError, match="must be STOPPED"):
        replace_direct_acl_without_principal(
            SimpleNamespace(api_client=_Api()),
            path="/api/2.0/permissions/supervisor-agents/target",
            permissions=initial,
            application_id="proxy-client",
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=app_starts_after_final_read,
        )

    assert methods == ["GET", "GET"]


def test_legacy_acl_cleanup_requires_exact_stopped_deployment_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_APP_NAME", "mip-app")
    monkeypatch.setenv("MIP_DEPLOYMENT_APP_OBJECT_ID", "app-object")
    monkeypatch.setenv("MIP_DEPLOYMENT_APP_APPLICATION_ID", "app-client")
    monkeypatch.setenv("MIP_DEPLOYMENT_APP_SCIM_ID", "app-scim")
    app = SimpleNamespace(
        id="app-object",
        service_principal_client_id="app-client",
        service_principal_id="app-scim",
        compute_status=SimpleNamespace(state="STOPPED"),
        pending_deployment=None,
    )

    stopped_deployment_app_assertion(
        SimpleNamespace(apps=SimpleNamespace(get=lambda name: app))
    )()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda app: setattr(app.compute_status, "state", "RUNNING"), "must be STOPPED"),
        (
            lambda app: setattr(app, "pending_deployment", SimpleNamespace(id="pending")),
            "must be STOPPED",
        ),
        (
            lambda app: setattr(app, "service_principal_id", "replacement-scim"),
            "identity drifted",
        ),
    ],
)
def test_legacy_acl_cleanup_rejects_unsafe_app_boundary(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    message: str,
) -> None:
    monkeypatch.setenv("MIP_APP_NAME", "mip-app")
    monkeypatch.setenv("MIP_DEPLOYMENT_APP_OBJECT_ID", "app-object")
    monkeypatch.setenv("MIP_DEPLOYMENT_APP_APPLICATION_ID", "app-client")
    monkeypatch.setenv("MIP_DEPLOYMENT_APP_SCIM_ID", "app-scim")
    app = SimpleNamespace(
        id="app-object",
        service_principal_client_id="app-client",
        service_principal_id="app-scim",
        compute_status=SimpleNamespace(state="STOPPED"),
        pending_deployment=None,
    )
    mutate(app)

    with pytest.raises(RuntimeError, match=message):
        stopped_deployment_app_assertion(
            SimpleNamespace(apps=SimpleNamespace(get=lambda name: app))
        )()


def test_legacy_acl_cleanup_rejects_concurrent_peer_before_replacement() -> None:
    initial = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY")
        ]
    }
    concurrent = {
        "access_control_list": [
            *cast(list[object], initial["access_control_list"]),
            {
                "user_name": "concurrent@example.com",
                "all_permissions": [
                    {
                        "permission_level": "CAN_QUERY",
                        "inherited": False,
                    }
                ],
            },
        ]
    }
    calls = 0
    mutations: list[object] = []

    class _Api:
        def do(
            self,
            method: str,
            _path: str,
            *,
            body: object | None = None,
        ) -> object:
            nonlocal calls
            if method == "GET":
                calls += 1
                return concurrent
            mutations.append(body)
            return {}

    with pytest.raises(RuntimeError, match="changed before legacy"):
        replace_direct_acl_without_principal(
            SimpleNamespace(api_client=_Api()),
            path="/api/2.0/permissions/genie/target",
            permissions=initial,
            application_id="proxy-client",
            assert_single_writer=lambda: pytest.fail(
                "fence accepted a changed ACL snapshot"
            ),
            assert_legacy_cleanup_quiesced=lambda: None,
        )

    assert calls == 1
    assert mutations == []


def test_legacy_acl_cleanup_rejects_ambiguous_direct_entry_without_mutation() -> None:
    malformed = {
        "access_control_list": [
            {
                "service_principal_name": "proxy-client",
                "all_permissions": [
                    {"permission_level": "CAN_QUERY", "inherited": False},
                    {"permission_level": "CAN_MANAGE", "inherited": False},
                ],
            }
        ]
    }
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: pytest.fail(
                "provider call after ambiguous ACL"
            )
        )
    )

    with pytest.raises(RuntimeError, match="incomplete or ambiguous"):
        replace_direct_acl_without_principal(
            workspace,
            path="/api/2.0/permissions/supervisor-agents/target",
            permissions=malformed,
            application_id="proxy-client",
            assert_single_writer=lambda: pytest.fail(
                "fence accepted an ambiguous ACL"
            ),
            assert_legacy_cleanup_quiesced=lambda: pytest.fail(
                "quiescence accepted an ambiguous ACL"
            ),
        )


def test_agent_proxy_access_converges_stale_direct_acl_before_global_postflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permissions = {
        "agent-old": {
            "access_control_list": [
                _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            ]
        },
        "agent-target": {
            "access_control_list": [
                _permission_entry(principal="proxy-client", level="CAN_MANAGE"),
            ]
        },
    }
    patches: list[tuple[str, str]] = []

    class _Api:
        def do(
            self,
            method: str,
            path: str,
            *,
            query: dict[str, object] | None = None,
            body: dict[str, object] | None = None,
        ) -> object:
            if path == "/api/2.1/supervisor-agents":
                assert method == "GET"
                assert query == {"page_size": 100}
                return {
                    "supervisor_agents": [
                        {"supervisor_agent_id": "agent-old", "display_name": "Old"},
                        {
                            "supervisor_agent_id": "agent-target",
                            "display_name": "Target",
                        },
                    ]
                }
            agent_id = path.rsplit("/", 1)[-1]
            if method == "GET":
                return permissions[agent_id]
            assert body is not None
            if method == "PUT":
                requests = cast(list[object], body["access_control_list"])
                patches.append((agent_id, "REMOVED"))
                permissions[agent_id] = {
                    "access_control_list": [
                        _permission_entry(
                            principal=str(cast(dict[str, object], request)[
                                "service_principal_name"
                            ]),
                            level=str(
                                cast(dict[str, object], request)["permission_level"]
                            ),
                        )
                        for request in requests
                    ]
                }
                return {}
            assert method == "PATCH"
            request = cast(list[object], body["access_control_list"])[0]
            assert isinstance(request, dict)
            level = str(request["permission_level"])
            assert level in {"CAN_MANAGE", "CAN_QUERY"}
            patches.append((agent_id, level))
            permissions[agent_id] = {
                "access_control_list": (
                    [_permission_entry(principal="proxy-client", level=level)]
                )
            }
            return {}

    workspace = SimpleNamespace(
        api_client=_Api(),
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(application_id="proxy-client", id="proxy-scim")]
            )
        ),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_wait_exact_capability_projection",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_reviewed_bindings",
        lambda *_args, **_kwargs: (("agent-target", "target-endpoint", "target-endpoint-id"),),
    )
    supervisor_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_proxy_access,
        "_converge_supervisor_agent_acls",
        lambda *_args, **kwargs: supervisor_calls.append(kwargs),
    )
    genie_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_proxy_access,
        "_converge_genie_acl",
        lambda *_args, **kwargs: genie_calls.append(kwargs),
    )
    endpoint_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_proxy_access,
        "converge_exact_direct_can_query",
        lambda *_args, **kwargs: endpoint_calls.append(kwargs),
    )

    agent_proxy_access.grant_and_audit_agent_proxy_access(
        workspace,
        supervisor_id="agent-target",
        supervisor_endpoint="target-endpoint",
        supervisor_endpoint_id="target-endpoint-id",
        genie_space_id="genie-space",
        application_id="proxy-client",
        runtime_application_id="runtime-client",
        expected_inventory_principal="admin@example.com",
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )

    assert patches == []
    assert supervisor_calls[0]["reviewed_ids"] == {"agent-target"}
    assert supervisor_calls[0]["service_principal_id"] == "proxy-scim"
    assert genie_calls[0]["genie_space_id"] == "genie-space"
    assert endpoint_calls[0]["reviewed_endpoint_names"] == {"target-endpoint"}


def test_agent_proxy_preserves_only_observed_legacy_signed_blue_serving_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(application_id="proxy-client", id="proxy-scim")]
            )
        )
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_wait_exact_capability_projection",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_reviewed_bindings",
        lambda *_args, **_kwargs: (
            ("green-agent", "green-endpoint", "green-endpoint-id"),
            ("blue-agent", "blue-endpoint", "blue-endpoint-id"),
        ),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "inspect_exact_query_access_mode",
        lambda *_args, **kwargs: (
            "direct" if kwargs["endpoint_name"] == "blue-endpoint" else "managed"
        ),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_supervisor_agents",
        lambda _workspace: {"green-agent": "Green", "blue-agent": "Blue"},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_converge_supervisor_agent_acls",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_converge_genie_acl",
        lambda *_args, **_kwargs: None,
    )
    endpoint_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        agent_proxy_access,
        "converge_exact_direct_can_query",
        lambda *_args, **kwargs: endpoint_calls.append(kwargs),
    )

    agent_proxy_access.grant_and_audit_agent_proxy_access(
        workspace,
        supervisor_id="green-agent",
        supervisor_endpoint="green-endpoint",
        supervisor_endpoint_id="green-endpoint-id",
        genie_space_id="genie-space",
        application_id="proxy-client",
        runtime_application_id="runtime-client",
        expected_inventory_principal="admin@example.com",
        preserved_supervisor_bindings=(
            ("blue-agent", "blue-endpoint", "blue-endpoint-id"),
        ),
        legacy_pinned_supervisor_endpoints=("blue-endpoint",),
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )

    assert endpoint_calls[0]["reviewed_endpoint_names"] == {
        "green-endpoint",
        "blue-endpoint",
    }
    assert endpoint_calls[0]["legacy_pinned_endpoint_names"] == {"blue-endpoint"}


def test_exact_supervisor_audit_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_name = managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="agent-target",
        application_id="proxy-client",
    )
    permissions = {
        "access_control_list": [
            _permission_entry(
                principal=group_name,
                level="CAN_QUERY",
                group=True,
            ),
        ]
    }
    patches: list[object] = []

    class _Api:
        def do(
            self,
            method: str,
            _path: str,
            *,
            body: object | None = None,
        ) -> object:
            if method == "PATCH":
                patches.append(body)
            return permissions

    monkeypatch.setattr(
        capability_convergence,
        "wait_exact_capability_projection",
        lambda *_args, **_kwargs: {group_name},
    )
    monkeypatch.setattr(
        capability_convergence,
        "wait_for_managed_agent_proxy_group_discovery",
        lambda *_args, **_kwargs: ManagedAgentProxyGroupState(
            contract=ManagedAgentProxyGroup(
                id="group-id",
                name=group_name,
                external_id="external-id",
            ),
            member_ids=("proxy-scim",),
        ),
    )
    capability_convergence.converge_supervisor_agent_acls(
        SimpleNamespace(api_client=_Api()),
        agents={"agent-target": "Target"},
        reviewed_ids={"agent-target"},
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        audit_only=True,
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )

    assert patches == []


def test_supervisor_endpoint_binding_rejects_drift_before_acl_mutation() -> None:
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "supervisor_agent_id": "agent-target",
                "endpoint_name": "other-endpoint",
            }
        ),
        serving_endpoints=SimpleNamespace(
            get=lambda _name: pytest.fail("endpoint read after binding drift")
        ),
    )

    with pytest.raises(RuntimeError, match="binding drifted"):
        agent_proxy_access._supervisor_binding(
            workspace,
            supervisor_id="agent-target",
            supervisor_endpoint="target-endpoint",
            supervisor_endpoint_id="target-endpoint-id",
            runtime_application_id="runtime-client",
        )


def test_supervisor_endpoint_binding_requires_exact_runtime_agent_endpoint() -> None:
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "supervisor_agent_id": "agent-target",
                "endpoint_name": "target-endpoint",
                "creator": "runtime-client",
            }
        ),
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                name="target-endpoint",
                id="endpoint-id",
                task="agent/v1/responses",
                creator="runtime-client",
            )
        ),
    )

    assert agent_proxy_access._supervisor_binding(
        workspace,
        supervisor_id="agent-target",
        supervisor_endpoint="target-endpoint",
        supervisor_endpoint_id="endpoint-id",
        runtime_application_id="runtime-client",
    ) == ("agent-target", "target-endpoint", "endpoint-id")


@pytest.mark.parametrize(
    ("supervisor_creator", "endpoint_id", "message"),
    (
        ("other-runtime", "endpoint-id", "Supervisor Agent creator"),
        ("runtime-client", "replacement-id", "immutable identity drifted"),
    ),
)
def test_supervisor_binding_rejects_creator_or_immutable_endpoint_drift(
    supervisor_creator: str,
    endpoint_id: str,
    message: str,
) -> None:
    workspace = SimpleNamespace(
        api_client=SimpleNamespace(
            do=lambda *_args, **_kwargs: {
                "supervisor_agent_id": "agent-target",
                "endpoint_name": "target-endpoint",
                "creator": supervisor_creator,
            }
        ),
        serving_endpoints=SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                name="target-endpoint",
                id=endpoint_id,
                task="agent/v1/responses",
                creator="runtime-client",
            )
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        agent_proxy_access._supervisor_binding(
            workspace,
            supervisor_id="agent-target",
            supervisor_endpoint="target-endpoint",
            supervisor_endpoint_id="endpoint-id",
            runtime_application_id="runtime-client",
        )


def test_genie_audit_accepts_exact_managed_target_and_empty_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_group = managed_agent_proxy_group_name(
        resource_kind="genie",
        resource_id="genie-target",
        application_id="proxy-client",
    )
    stale_group = managed_agent_proxy_group_name(
        resource_kind="genie",
        resource_id="genie-stale",
        application_id="proxy-client",
    )
    permissions = {
        "genie-target": {
            "access_control_list": [
                _permission_entry(
                    principal=target_group,
                    level="CAN_RUN",
                    group=True,
                )
            ]
        },
        "genie-stale": {
            "access_control_list": [
                _permission_entry(
                    principal=stale_group,
                    level="CAN_RUN",
                    group=True,
                )
            ]
        },
    }
    monkeypatch.setattr(
        capability_convergence,
        "_genie_spaces",
        lambda _workspace: {"genie-target": "Target", "genie-stale": "Stale"},
    )
    monkeypatch.setattr(
        capability_convergence,
        "wait_exact_capability_projection",
        lambda *_args, **_kwargs: {target_group},
    )

    def state(*_args: object, resource_id: str, **_kwargs: object) -> object:
        group_name = target_group if resource_id == "genie-target" else stale_group
        return ManagedAgentProxyGroupState(
            contract=ManagedAgentProxyGroup(
                id=f"{resource_id}-group",
                name=group_name,
                external_id=f"{resource_id}-external",
            ),
            member_ids=(("proxy-scim",) if resource_id == "genie-target" else ()),
        )

    monkeypatch.setattr(
        capability_convergence,
        "inspect_managed_agent_proxy_group",
        state,
    )
    monkeypatch.setattr(
        capability_convergence,
        "wait_for_managed_agent_proxy_group_discovery",
        state,
    )
    capability_convergence.converge_genie_acl(
        SimpleNamespace(
            api_client=SimpleNamespace(
                do=lambda _method, path: permissions[path.rsplit("/", 1)[-1]]
            )
        ),
        genie_space_id="genie-target",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        audit_only=True,
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )


def test_genie_audit_rejects_duplicate_proxy_entries_regardless_of_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permissions = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(principal="proxy-client", level="CAN_MANAGE"),
        ]
    }
    monkeypatch.setattr(
        capability_convergence,
        "_genie_spaces",
        lambda _workspace: {"genie-target": "Target"},
    )
    monkeypatch.setattr(
        capability_convergence,
        "wait_exact_capability_projection",
        lambda *_args, **_kwargs: set(),
    )
    monkeypatch.setattr(
        capability_convergence,
        "inspect_managed_agent_proxy_group",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        capability_convergence,
        "wait_for_managed_agent_proxy_group_discovery",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="duplicate entries"):
        capability_convergence.converge_genie_acl(
            SimpleNamespace(api_client=SimpleNamespace(do=lambda *_args, **_kwargs: permissions)),
            genie_space_id="genie-target",
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            audit_only=True,
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=lambda: None,
        )


def test_genie_deny_all_rejects_duplicate_proxy_entries_after_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permissions = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(principal="proxy-client", level="CAN_MANAGE"),
        ]
    }
    patches: list[object] = []

    class _Api:
        def do(
            self,
            method: str,
            _path: str,
            *,
            body: object | None = None,
        ) -> object:
            if method == "PATCH":
                patches.append(body)
            return permissions

    monkeypatch.setattr(
        capability_denial,
        "_genie_spaces",
        lambda _workspace: {"genie-target": "Target"},
    )
    monkeypatch.setattr(
        capability_denial,
        "audit_global_no_genie_access",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="duplicate entries"):
        capability_denial.revoke_all_genie_acls(
            SimpleNamespace(api_client=_Api()),
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            effective_group_names=set(),
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=lambda: None,
        )

    assert patches == []


def test_deny_all_attempts_serving_supervisor_and_genie_after_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    workspace = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(application_id="proxy-client", id="proxy-scim")]
            )
        )
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_supervisor_agents",
        lambda _workspace: {"target": "Target"},
    )

    def fail_serving(*_args: object, **_kwargs: object) -> None:
        calls.append("serving")
        raise RuntimeError("inherited endpoint access")

    def fail_supervisor(*_args: object, **_kwargs: object) -> None:
        calls.append("supervisor")
        raise RuntimeError("inherited Supervisor access")

    monkeypatch.setattr(agent_proxy_access, "revoke_all_direct_permissions", fail_serving)
    monkeypatch.setattr(agent_proxy_access, "_revoke_all_supervisor_agent_acls", fail_supervisor)
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_genie_acls",
        lambda *_args, **_kwargs: calls.append("genie"),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_managed_capability_memberships",
        lambda *_args, **_kwargs: calls.append("membership"),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_wait_exact_capability_projection",
        lambda *_args, **_kwargs: set(),
    )

    with pytest.raises(RuntimeError, match="global denial is unproven"):
        agent_proxy_access.revoke_and_audit_agent_proxy_access(
            workspace,
            application_id="proxy-client",
            expected_inventory_principal="admin@example.com",
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=lambda: None,
        )

    assert calls == [
        "serving",
        "supervisor",
        "genie",
        "membership",
        "serving",
        "supervisor",
        "genie",
    ]


def test_managed_denial_retries_stale_empty_inventory_then_revokes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_name = managed_agent_proxy_group_name(
        resource_kind="supervisor",
        resource_id="agent-target",
        application_id="proxy-client",
    )
    active = True
    inventory_reads = 0
    revokes: list[str] = []

    def inventory(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        nonlocal inventory_reads
        inventory_reads += 1
        if inventory_reads == 1:
            return ()
        return (
            ManagedAgentProxyGroupState(
                contract=ManagedAgentProxyGroup(
                    id="group-id",
                    name=group_name,
                    external_id="external-id",
                ),
                member_ids=(("proxy-scim",) if active else ()),
            ),
        )

    def remove(*_args: object, state: object, **_kwargs: object) -> bool:
        nonlocal active
        if not active:
            return False
        revokes.append(cast(ManagedAgentProxyGroupState, state).contract.id)
        active = False
        return True

    monkeypatch.setattr(
        capability_denial,
        "managed_agent_proxy_groups_for_application",
        inventory,
    )
    monkeypatch.setattr(
        capability_denial,
        "remove_managed_agent_proxy_membership",
        remove,
    )
    monkeypatch.setattr(
        capability_denial,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {},
    )
    times = iter((0.0, 0.1, 0.2, 0.3, 0.4))

    capability_denial.revoke_all_managed_capability_memberships(
        SimpleNamespace(),
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        assert_single_writer=lambda: None,
        sleep=lambda _seconds: None,
        clock=lambda: next(times),
        deadline_seconds=1.0,
    )

    assert revokes == ["group-id"]
    assert inventory_reads == 5


def test_deny_all_clears_direct_first_failure_after_effective_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    workspace = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda **_kwargs: iter(
                [SimpleNamespace(application_id="proxy-client", id="proxy-scim")]
            )
        )
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "resolve_effective_groups",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_supervisor_agents",
        lambda _workspace: {"target": "Target"},
    )

    def revoke_serving(*_args: object, **kwargs: object) -> None:
        principal_id = kwargs.get("service_principal_id")
        calls.append(("serving", str(principal_id) if principal_id else None))
        if not principal_id:
            raise RuntimeError("exact managed-group member identity is required")

    monkeypatch.setattr(agent_proxy_access, "revoke_all_direct_permissions", revoke_serving)
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_supervisor_agent_acls",
        lambda *_args, **_kwargs: calls.append(("supervisor", None)),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_genie_acls",
        lambda *_args, **_kwargs: calls.append(("genie", None)),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_managed_capability_memberships",
        lambda *_args, **_kwargs: calls.append(("membership", None)),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_wait_exact_capability_projection",
        lambda *_args, **_kwargs: set(),
    )

    agent_proxy_access.revoke_and_audit_agent_proxy_access(
        workspace,
        application_id="proxy-client",
        expected_inventory_principal="admin@example.com",
        assert_single_writer=lambda: None,
        assert_legacy_cleanup_quiesced=lambda: None,
    )

    assert calls == [
        ("serving", None),
        ("supervisor", None),
        ("genie", None),
        ("membership", None),
        ("serving", "proxy-scim"),
        ("supervisor", None),
        ("genie", None),
    ]


def test_deny_all_attempts_all_direct_axes_before_proxy_identity_inventory_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    workspace = SimpleNamespace(service_principals=SimpleNamespace(list=lambda **_kwargs: iter(())))
    monkeypatch.setattr(
        agent_proxy_access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "revoke_all_direct_permissions",
        lambda *_args, **_kwargs: calls.append("serving"),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_supervisor_agents",
        lambda _workspace: {"target": "Target"},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_supervisor_agent_acls",
        lambda *_args, **_kwargs: calls.append("supervisor"),
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_revoke_all_genie_acls",
        lambda *_args, **_kwargs: calls.append("genie"),
    )

    with pytest.raises(RuntimeError, match="identity inventory"):
        agent_proxy_access.revoke_and_audit_agent_proxy_access(
            workspace,
            application_id="deleted-proxy",
            expected_inventory_principal="admin@example.com",
            assert_single_writer=lambda: None,
            assert_legacy_cleanup_quiesced=lambda: None,
        )

    assert calls == ["serving", "supervisor", "genie"]


def test_agent_proxy_acl_mutation_refuses_missing_deployment_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MIP_APP_NAME",
        "MIP_APP_DEPLOYMENT_LEASE_ID",
        "MIP_DEPLOYMENT_SOURCE_GIT_SHA",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        deployment_lease_authority.app_deployment_lease,
        "held_assertion",
        lambda *_args, **_kwargs: pytest.fail(
            "lease provider called without the exact environment contract"
        ),
    )

    with pytest.raises(RuntimeError, match="exact signed App deployment lease"):
        agent_proxy_access._deployment_lease_assertion(SimpleNamespace())


def test_agent_proxy_acl_mutation_binds_exact_deployment_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIP_APP_NAME", "mip-app")
    monkeypatch.setenv("MIP_APP_DEPLOYMENT_LEASE_ID", "lease-id")
    monkeypatch.setenv("MIP_DEPLOYMENT_SOURCE_GIT_SHA", "a" * 40)
    calls: list[dict[str, object]] = []
    assertions: list[str] = []

    def held(workspace: object, **kwargs: object):
        calls.append({"workspace": workspace, **kwargs})

        def assert_held() -> None:
            assertions.append("held")

        return assert_held

    monkeypatch.setattr(
        deployment_lease_authority.app_deployment_lease,
        "held_assertion",
        held,
    )
    workspace = SimpleNamespace()

    assertion = agent_proxy_access._deployment_lease_assertion(workspace)
    assertion()

    assert calls == [
        {
            "workspace": workspace,
            "app_name": "mip-app",
            "lease_id": "lease-id",
            "source_git_sha": "a" * 40,
        }
    ]
    assert assertions == ["held", "held"]
