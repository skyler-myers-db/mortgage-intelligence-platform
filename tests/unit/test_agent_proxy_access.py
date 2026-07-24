from __future__ import annotations

from types import SimpleNamespace

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
            request = body["access_control_list"][0]
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
        "grant_and_verify_genie_can_run",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "audit_global_genie_access",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        agent_proxy_access,
        "audit_global_no_serving_endpoint_access",
        lambda *_args, **_kwargs: None,
    )

    agent_proxy_access.grant_and_audit_agent_proxy_access(
        workspace,
        supervisor_id="agent-target",
        genie_space_id="genie-space",
        application_id="proxy-client",
    )

    assert patches == [
        ("agent-old", "NO_PERMISSIONS"),
        ("agent-target", "CAN_QUERY"),
    ]
