from __future__ import annotations

from types import SimpleNamespace

import pytest

import tools.databricks.audit_global_m2m_access as audit


def _workspace(*, principal: str = "deployer@example.com", admin: bool = True) -> object:
    groups = [SimpleNamespace(display="admins")] if admin else []
    return SimpleNamespace(
        current_user=SimpleNamespace(
            me=lambda: SimpleNamespace(user_name=principal, groups=groups)
        )
    )


def test_main_audits_serving_and_genie_globally(monkeypatch) -> None:
    workspace = _workspace()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "audit_global_serving_endpoint_access",
        lambda client, **kwargs: calls.append(("serving", (client, kwargs))),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_genie_access",
        lambda client, **kwargs: calls.append(("genie", (client, kwargs))),
    )

    assert (
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "supervisor-endpoint",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
                "--genie-space-id",
                "space-id",
            ]
        )
        == 0
    )

    assert calls == [
        (
            "serving",
            (
                workspace,
                {
                    "reviewed_endpoint_names": (
                        "supervisor-endpoint",
                        "gateway-endpoint",
                    ),
                    "service_principal": "runtime-id",
                    "expected_permission_level": "CAN_MANAGE",
                },
            ),
        ),
        (
            "genie",
            (
                workspace,
                {
                    "reviewed_genie_space_id": "space-id",
                    "application_id": "runtime-id",
                },
            ),
        ),
    ]


def test_main_can_audit_verifier_without_genie(monkeypatch) -> None:
    workspace = _workspace()
    serving: list[dict[str, object]] = []
    no_genie: list[dict[str, object]] = []
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        audit,
        "audit_global_serving_endpoint_access",
        lambda _client, **kwargs: serving.append(kwargs),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_genie_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    monkeypatch.setattr(
        audit,
        "audit_global_no_genie_access",
        lambda _client, **kwargs: no_genie.append(kwargs),
    )

    assert (
        audit.main(
            [
                "--application-id",
                "verifier-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_QUERY",
                "--forbid-all-genie",
            ]
        )
        == 0
    )
    assert serving == [
        {
            "reviewed_endpoint_names": ("gateway-endpoint",),
            "service_principal": "verifier-id",
            "expected_permission_level": "CAN_QUERY",
        }
    ]
    assert no_genie == [{"application_id": "verifier-id"}]


def test_workspace_admin_inventory_principal_returns_verified_identity() -> None:
    assert (
        audit.workspace_admin_inventory_principal(_workspace())
        == "deployer@example.com"
    )


@pytest.mark.parametrize(
    ("workspace", "message"),
    [
        (_workspace(principal=""), "could not identify"),
        (_workspace(admin=False), "workspace-admin"),
    ],
)
def test_workspace_admin_inventory_principal_rejects_incomplete_authority(
    workspace: object,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        audit.workspace_admin_inventory_principal(workspace)


@pytest.mark.parametrize(
    ("workspace", "message"),
    [
        (_workspace(principal="other@example.com"), "unexpected principal"),
        (_workspace(admin=False), "workspace-admin"),
    ],
)
def test_main_rejects_incomplete_inventory_authority(
    monkeypatch: pytest.MonkeyPatch,
    workspace: object,
    message: str,
) -> None:
    monkeypatch.setattr(audit, "WorkspaceClient", lambda: workspace)

    with pytest.raises(RuntimeError, match=message):
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
                "--forbid-all-genie",
            ]
        )


def test_main_requires_explicit_genie_access_policy() -> None:
    with pytest.raises(SystemExit):
        audit.main(
            [
                "--application-id",
                "runtime-id",
                "--expected-inventory-principal",
                "deployer@example.com",
                "--serving-endpoint",
                "gateway-endpoint",
                "--expected-serving-permission",
                "CAN_MANAGE",
            ]
        )
