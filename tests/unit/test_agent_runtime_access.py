from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import agent_runtime_access as access


def test_current_runtime_identity_requires_exact_application_id() -> None:
    workspace = SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(
                user_name="runtime-client",
                display_name=access.AGENT_RUNTIME_DISPLAY_NAME,
            )
        )
    )

    access.assert_current_runtime_identity(workspace, application_id="runtime-client")

    with pytest.raises(RuntimeError, match="not the configured agent-runtime"):
        access.assert_current_runtime_identity(workspace, application_id="human-client")


def test_runtime_creator_rejects_human_owner() -> None:
    with pytest.raises(RuntimeError, match="is not dedicated agent runtime"):
        access.assert_runtime_creator(
            "skyler@entrada.ai",
            application_id="runtime-client",
            resource="managed Supervisor",
        )


def test_genie_can_run_grant_is_direct_and_read_back() -> None:
    calls: list[tuple[str, str, object | None]] = []

    class _Api:
        def do(
            self,
            method: str,
            path: str,
            *,
            body: object | None = None,
        ) -> object:
            calls.append((method, path, body))
            if method == "GET":
                return {
                    "access_control_list": [
                        {
                            "service_principal_name": "runtime-client",
                            "all_permissions": [
                                {"permission_level": "CAN_RUN", "inherited": False}
                            ],
                        }
                    ]
                }
            return {}

    access.grant_and_verify_genie_can_run(
        SimpleNamespace(api_client=_Api()),
        genie_space_id="space-123",
        application_id="runtime-client",
        effective_group_names=set(),
    )

    assert calls == [
        (
            "PATCH",
            "/api/2.0/permissions/genie/space-123",
            {
                "access_control_list": [
                    {
                        "service_principal_name": "runtime-client",
                        "permission_level": "CAN_RUN",
                    }
                ]
            },
        ),
        ("GET", "/api/2.0/permissions/genie/space-123", None),
    ]


def test_genie_postflight_rejects_missing_direct_grant() -> None:
    api = SimpleNamespace(do=lambda *_args, **_kwargs: {"access_control_list": []})
    with pytest.raises(RuntimeError, match="exact effective CAN_RUN postflight failed"):
        access.grant_and_verify_genie_can_run(
            SimpleNamespace(api_client=api),
            genie_space_id="space-123",
            application_id="runtime-client",
            effective_group_names=set(),
        )


def test_genie_postflight_rejects_inherited_can_manage() -> None:
    permissions = {
        "access_control_list": [
            {
                "service_principal_name": "runtime-client",
                "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": False},
                    {"permission_level": "CAN_MANAGE", "inherited": True},
                ],
            }
        ]
    }
    api = SimpleNamespace(do=lambda *_args, **_kwargs: permissions)

    with pytest.raises(RuntimeError, match="inherited broader access"):
        access.grant_and_verify_genie_can_run(
            SimpleNamespace(api_client=api),
            genie_space_id="space-123",
            application_id="runtime-client",
            effective_group_names=set(),
        )


def test_genie_postflight_rejects_effective_group_permission() -> None:
    permissions = {
        "access_control_list": [
            {
                "service_principal_name": "runtime-client",
                "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": False},
                ],
            },
            {
                "group_name": "runtime-broad-access",
                "all_permissions": [
                    {"permission_level": "CAN_MANAGE", "inherited": False},
                ],
            },
        ]
    }
    api = SimpleNamespace(do=lambda *_args, **_kwargs: permissions)

    with pytest.raises(RuntimeError, match="through group access"):
        access.grant_and_verify_genie_can_run(
            SimpleNamespace(api_client=api),
            genie_space_id="space-123",
            application_id="runtime-client",
            effective_group_names={"runtime-broad-access"},
        )


def test_global_genie_audit_rejects_access_to_other_space() -> None:
    class _Genie:
        def __init__(self) -> None:
            self.tokens: list[str | None] = []

        def list_spaces(self, *, page_token: str | None = None) -> object:
            self.tokens.append(page_token)
            if page_token is None:
                return SimpleNamespace(
                    spaces=[SimpleNamespace(space_id="reviewed-space", title="Module 0")],
                    next_page_token="page-2",
                )
            assert page_token == "page-2"
            return SimpleNamespace(
                spaces=[SimpleNamespace(space_id="other-space", title="Other")],
                next_page_token=None,
            )

    target_acl = {
        "access_control_list": [
            {
                "service_principal_name": "runtime-client",
                "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": False}
                ],
            }
        ]
    }
    unrelated_acl = {
        "access_control_list": [
            {
                "service_principal_name": "runtime-client",
                "all_permissions": [
                    {"permission_level": "CAN_RUN", "inherited": False}
                ],
            }
        ]
    }

    class _Api:
        def do(self, method: str, path: str) -> object:
            assert method == "GET"
            return target_acl if path.endswith("/reviewed-space") else unrelated_acl

    genie = _Genie()
    with pytest.raises(RuntimeError, match="unrelated Genie space 'other-space'"):
        access.audit_global_genie_access(
            SimpleNamespace(genie=genie, api_client=_Api()),
            reviewed_genie_space_id="reviewed-space",
            application_id="runtime-client",
            effective_group_names=set(),
        )
    assert genie.tokens == [None, "page-2"]


def test_global_genie_audit_accepts_only_reviewed_space() -> None:
    class _Genie:
        def list_spaces(self, *, page_token: str | None = None) -> object:
            assert page_token is None
            return SimpleNamespace(
                spaces=[
                    SimpleNamespace(space_id="reviewed-space", title="Module 0"),
                    SimpleNamespace(space_id="other-space", title="Other"),
                ],
                next_page_token=None,
            )

    class _Api:
        def do(self, method: str, path: str) -> object:
            assert method == "GET"
            if path.endswith("/reviewed-space"):
                return {
                    "access_control_list": [
                        {
                            "service_principal_name": "runtime-client",
                            "all_permissions": [
                                {"permission_level": "CAN_RUN", "inherited": False}
                            ],
                        }
                    ]
                }
            return {"access_control_list": []}

    access.audit_global_genie_access(
        SimpleNamespace(genie=_Genie(), api_client=_Api()),
        reviewed_genie_space_id="reviewed-space",
        application_id="runtime-client",
        effective_group_names=set(),
    )


def test_global_genie_audit_rejects_hidden_parent_without_managed_groups() -> None:
    genie = SimpleNamespace(
        list_spaces=lambda **_kwargs: SimpleNamespace(
            spaces=[
                SimpleNamespace(space_id="reviewed-space", title="Module 0"),
                SimpleNamespace(space_id="other-space", title="Other"),
            ],
            next_page_token=None,
        )
    )

    def _permissions(method: str, path: str) -> object:
        assert method == "GET"
        if path.endswith("/reviewed-space"):
            return {
                "access_control_list": [
                    {
                        "service_principal_name": "runtime-client",
                        "all_permissions": [
                            {"permission_level": "CAN_RUN", "inherited": False}
                        ],
                    }
                ]
            }
        return {
            "access_control_list": [
                {
                    "group_name": "hidden-account-parent",
                    "all_permissions": [
                        {"permission_level": "CAN_RUN", "inherited": False}
                    ],
                }
            ]
        }

    with pytest.raises(
        RuntimeError,
        match=r"through group\(s\): hidden-account-parent",
    ):
        access.audit_global_genie_access(
            SimpleNamespace(
                genie=genie,
                api_client=SimpleNamespace(do=_permissions),
            ),
            reviewed_genie_space_id="reviewed-space",
            application_id="runtime-client",
            effective_group_names={"hidden-account-parent"},
        )
