from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.databricks import converge_verifier_gateway_access as access

APPLICATION_ID = "verifier-application-id"
SCIM_ID = "verifier-scim-id"


class _ServicePrincipals:
    def __init__(self, principals: tuple[object, ...]) -> None:
        self.principals = principals
        self.calls: list[tuple[str, str]] = []

    def list(self, *, filter: str, attributes: str) -> list[object]:
        self.calls.append((filter, attributes))
        return list(self.principals)


def _workspace(
    *,
    application_id: str = APPLICATION_ID,
    scim_id: str = SCIM_ID,
) -> Any:
    return SimpleNamespace(
        service_principals=_ServicePrincipals(
            (
                SimpleNamespace(
                    id=scim_id,
                    application_id=application_id,
                ),
            )
        )
    )


def test_capture_binds_admin_inventory_and_writes_only_exact_scim_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    observed_admin: list[tuple[object, str]] = []
    monkeypatch.setattr(
        access,
        "assert_workspace_admin_inventory_identity",
        lambda client, *, expected_principal: observed_admin.append((client, expected_principal)),
    )
    out_env = tmp_path / "verifier.env"

    assert (
        access.capture(
            workspace,
            application_id=APPLICATION_ID,
            expected_inventory_principal="admin@example.com",
            out_env=out_env,
        )
        == SCIM_ID
    )

    assert observed_admin == [(workspace, "admin@example.com")]
    assert workspace.service_principals.calls == [
        (
            f'applicationId eq "{APPLICATION_ID}"',
            "id,applicationId",
        )
    ]
    assert out_env.read_text(encoding="utf-8") == f"MIP_VERIFIER_SCIM_ID={SCIM_ID}\n"
    assert out_env.stat().st_mode & 0o777 == 0o600


def test_capture_rejects_ambiguous_application_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    workspace.service_principals.principals = (
        SimpleNamespace(id=SCIM_ID, application_id=APPLICATION_ID),
        SimpleNamespace(id="other-scim-id", application_id=APPLICATION_ID),
    )
    monkeypatch.setattr(
        access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="exactly one"):
        access.capture(
            workspace,
            application_id=APPLICATION_ID,
            expected_inventory_principal="admin@example.com",
            out_env=tmp_path / "verifier.env",
        )


def test_revoke_managed_passes_the_pinned_scim_id_and_exact_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    observed: list[dict[str, object]] = []

    def fence() -> None:
        return None

    monkeypatch.setattr(
        access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        access,
        "revoke_direct_permissions",
        lambda client, **kwargs: observed.append({"client": client, **kwargs}) or True,
    )

    assert access.revoke_managed(
        workspace,
        endpoint="green-gateway",
        application_id=APPLICATION_ID,
        expected_scim_id=SCIM_ID,
        expected_inventory_principal="admin@example.com",
        assert_single_writer=fence,
    )

    assert observed == [
        {
            "client": workspace,
            "endpoint_name": "green-gateway",
            "service_principal": APPLICATION_ID,
            "service_principal_id": SCIM_ID,
            "missing_ok": False,
            "assert_single_writer": fence,
        }
    ]


def test_revoke_managed_refuses_scim_drift_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(scim_id="replacement-scim-id")
    revoked: list[object] = []
    monkeypatch.setattr(
        access,
        "assert_workspace_admin_inventory_identity",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        access,
        "revoke_direct_permissions",
        lambda *_args, **_kwargs: revoked.append(object()),
    )

    with pytest.raises(RuntimeError, match="drifted"):
        access.revoke_managed(
            workspace,
            endpoint="green-gateway",
            application_id=APPLICATION_ID,
            expected_scim_id=SCIM_ID,
            expected_inventory_principal="admin@example.com",
            assert_single_writer=lambda: None,
        )

    assert revoked == []


def test_cli_routes_capture_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace()
    observed: list[tuple[object, str, str, Path]] = []
    monkeypatch.setattr(access, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        access,
        "capture",
        lambda client, *, application_id, expected_inventory_principal, out_env: (
            observed.append((client, application_id, expected_inventory_principal, out_env))
            or SCIM_ID
        ),
    )
    out_env = tmp_path / "verifier.env"

    assert (
        access.main(
            [
                "capture",
                "--application-id",
                APPLICATION_ID,
                "--expected-inventory-principal",
                "admin@example.com",
                "--out-env",
                str(out_env),
            ]
        )
        == 0
    )
    assert observed == [(workspace, APPLICATION_ID, "admin@example.com", out_env)]


def test_cli_routes_exact_managed_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace()
    observed: list[tuple[object, str, str, str, str]] = []
    monkeypatch.setattr(access, "WorkspaceClient", lambda: workspace)
    monkeypatch.setattr(
        access,
        "held_assertion_from_env",
        lambda *_args, **_kwargs: (lambda: None),
    )
    monkeypatch.setattr(
        access,
        "revoke_managed",
        lambda client,
        *,
        endpoint,
        application_id,
        expected_scim_id,
        expected_inventory_principal,
        assert_single_writer: observed.append(
            (
                client,
                endpoint,
                application_id,
                expected_scim_id,
                expected_inventory_principal,
            )
        )
        or True,
    )

    assert (
        access.main(
            [
                "revoke-managed",
                "--endpoint",
                "green-gateway",
                "--application-id",
                APPLICATION_ID,
                "--expected-scim-id",
                SCIM_ID,
                "--expected-inventory-principal",
                "admin@example.com",
            ]
        )
        == 0
    )
    assert observed == [
        (
            workspace,
            "green-gateway",
            APPLICATION_ID,
            SCIM_ID,
            "admin@example.com",
        )
    ]
