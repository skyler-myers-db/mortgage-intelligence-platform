from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from tools.databricks import agent_proxy_access


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
    permissions = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
        ]
    }
    agent_proxy_access._assert_agent_acl(
        permissions,
        application_id="proxy-client",
        effective_group_names=set(),
        expect_query=True,
    )
    agent_proxy_access._assert_agent_acl(
        {"access_control_list": []},
        application_id="proxy-client",
        effective_group_names=set(),
        expect_query=False,
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
    with pytest.raises(RuntimeError):
        agent_proxy_access._assert_agent_acl(
            permissions,
            application_id="proxy-client",
            effective_group_names={"proxy-group"},
            expect_query=True,
        )


def test_supervisor_acl_rejects_duplicate_proxy_entries_regardless_of_level() -> None:
    permissions = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
            _permission_entry(principal="proxy-client", level="CAN_MANAGE"),
        ]
    }

    with pytest.raises(RuntimeError, match="duplicate entries"):
        agent_proxy_access._assert_agent_acl(
            permissions,
            application_id="proxy-client",
            effective_group_names=set(),
            expect_query=True,
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
            assert method == "PATCH"
            assert body is not None
            request = cast(list[object], body["access_control_list"])[0]
            assert isinstance(request, dict)
            level = str(request["permission_level"])
            patches.append((agent_id, level))
            permissions[agent_id] = {
                "access_control_list": (
                    []
                    if level == "NO_PERMISSIONS"
                    else [_permission_entry(principal="proxy-client", level=level)]
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
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "_reviewed_bindings",
        lambda *_args, **_kwargs: (("agent-target", "target-endpoint", "target-endpoint-id"),),
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
    )

    assert patches == [
        ("agent-old", "NO_PERMISSIONS"),
        ("agent-target", "CAN_QUERY"),
    ]
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
    )

    assert endpoint_calls[0]["reviewed_endpoint_names"] == {
        "green-endpoint",
        "blue-endpoint",
    }
    assert endpoint_calls[0]["legacy_pinned_endpoint_names"] == {"blue-endpoint"}


def test_exact_supervisor_acl_is_idempotent_and_audit_only_is_read_only() -> None:
    permissions = {
        "access_control_list": [
            _permission_entry(principal="proxy-client", level="CAN_QUERY"),
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

    workspace = SimpleNamespace(api_client=_Api())
    for audit_only in (False, True):
        agent_proxy_access._converge_supervisor_agent_acls(
            workspace,
            agents={"agent-target": "Target"},
            reviewed_ids={"agent-target"},
            application_id="proxy-client",
            effective_group_names=set(),
            audit_only=audit_only,
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


def test_genie_convergence_revokes_stale_direct_and_keeps_exact_target_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    permissions = {
        "genie-target": {
            "access_control_list": [
                _permission_entry(principal="proxy-client", level="CAN_RUN"),
            ]
        },
        "genie-stale": {
            "access_control_list": [
                _permission_entry(principal="proxy-client", level="CAN_RUN"),
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
            body: dict[str, object] | None = None,
        ) -> object:
            space_id = path.rsplit("/", 1)[-1]
            if method == "GET":
                return permissions[space_id]
            assert body is not None
            request = cast(list[object], body["access_control_list"])[0]
            assert isinstance(request, dict)
            level = str(request["permission_level"])
            patches.append((space_id, level))
            permissions[space_id] = {
                "access_control_list": (
                    []
                    if level == "NO_PERMISSIONS"
                    else [_permission_entry(principal="proxy-client", level=level)]
                )
            }
            return {}

    monkeypatch.setattr(
        agent_proxy_access,
        "_genie_spaces",
        lambda _workspace: {"genie-target": "Target", "genie-stale": "Stale"},
    )
    audits: list[object] = []
    monkeypatch.setattr(
        agent_proxy_access,
        "audit_global_genie_access",
        lambda *_args, **kwargs: audits.append(kwargs),
    )

    agent_proxy_access._converge_genie_acl(
        SimpleNamespace(api_client=_Api()),
        genie_space_id="genie-target",
        application_id="proxy-client",
        service_principal_id="proxy-scim",
        effective_group_names=set(),
        audit_only=False,
    )

    assert patches == [("genie-stale", "NO_PERMISSIONS")]
    assert len(audits) == 1


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
        agent_proxy_access,
        "_genie_spaces",
        lambda _workspace: {"genie-target": "Target"},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "audit_global_genie_access",
        lambda *_args, **_kwargs: pytest.fail("external audit accepted duplicate principal"),
    )

    with pytest.raises(RuntimeError, match="duplicate entries"):
        agent_proxy_access._converge_genie_acl(
            SimpleNamespace(api_client=SimpleNamespace(do=lambda *_args, **_kwargs: permissions)),
            genie_space_id="genie-target",
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            effective_group_names=set(),
            audit_only=True,
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
        agent_proxy_access,
        "_genie_spaces",
        lambda _workspace: {"genie-target": "Target"},
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "audit_global_no_genie_access",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="duplicate entries"):
        agent_proxy_access._revoke_all_genie_acls(
            SimpleNamespace(api_client=_Api()),
            application_id="proxy-client",
            service_principal_id="proxy-scim",
            effective_group_names=set(),
        )

    assert len(patches) == 1


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

    with pytest.raises(RuntimeError, match="global denial is unproven"):
        agent_proxy_access.revoke_and_audit_agent_proxy_access(
            workspace,
            application_id="proxy-client",
            expected_inventory_principal="admin@example.com",
        )

    assert calls == [
        "serving",
        "supervisor",
        "genie",
        "serving",
        "supervisor",
        "genie",
    ]


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

    agent_proxy_access.revoke_and_audit_agent_proxy_access(
        workspace,
        application_id="proxy-client",
        expected_inventory_principal="admin@example.com",
    )

    assert calls == [
        ("serving", None),
        ("supervisor", None),
        ("genie", None),
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
        )

    assert calls == ["serving", "supervisor", "genie"]
