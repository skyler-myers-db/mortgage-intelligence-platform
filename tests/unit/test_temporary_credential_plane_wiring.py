"""Every governed caller hands the default target-identity probe its deployer client.

The workspace temporary-credential plane mints through the deployer workspace
client, so a default probe that is not given one fails closed. These tests pin
that each caller of the default probe passes the exact deployer client it was
built with, and that a caller-supplied probe still receives only its own
reviewed arguments.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit import test_audit_agent_runtime_foreign_uc_access as audit_tests
from tests.unit import test_converge_campaign_treatment_access as converge_tests
from tests.unit import test_ensure_pipeline_namespace as ensure_tests
from tools.databricks import audit_agent_runtime_foreign_uc_access as auditor
from tools.databricks import converge_campaign_treatment_access as converger
from tools.databricks import ensure_pipeline_namespace as namespace
from tools.databricks import foreign_catalog_binding_manifest as manifest


class _Stop(Exception):
    """Ends a caller right after the probe so only the wiring is asserted."""


def _recorder(calls: list[dict[str, Any]], result: Any) -> Any:
    def probe(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, **kwargs})
        if isinstance(result, BaseException):
            raise result
        return result

    return probe


def test_campaign_treatment_converger_passes_its_deployer_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_group = "Customer-Platform-Governance"
    workspace, _execution, _ = converge_tests._workspace(
        owner=owner_group,
        metastore_owner=owner_group,
        owner_group=owner_group,
    )
    account = converge_tests._account_factory(owner_group=owner_group, target_is_member=False)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(converger, "target_group_membership_probe", _recorder(calls, False))

    assert converge_tests.converge_campaign_treatment_access(
        warehouse_id="warehouse-1",
        catalog="mip",
        principal="app-client",
        mode="quiesce",
        approved_owner_principals={owner_group},
        account_factory=lambda: account,
        workspace=workspace,
    )

    assert len(calls) == 1
    assert calls[0]["deployer_workspace"] is workspace


def test_pipeline_namespace_owner_policy_passes_its_deployer_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_group = SimpleNamespace(id="owner-group-id", display_name="mip-owners")
    workspace, _, _ = ensure_tests._workspace(
        catalog=ensure_tests._catalog(owner="mip-owners"),
        schema=ensure_tests._schema(owner="mip-owners"),
        groups=[owner_group],
    )
    account = SimpleNamespace(
        config=SimpleNamespace(client_id="account-client"),
        users=ensure_tests._DirectoryApi(
            [SimpleNamespace(id="deployer-id", user_name="deployer@example.com", active=True)]
        ),
        service_principals=ensure_tests._DirectoryApi(
            [
                SimpleNamespace(
                    id="runtime-account-id",
                    application_id="runtime-app-client",
                    active=True,
                )
            ]
        ),
        groups=SimpleNamespace(list=lambda **_: [owner_group], get=lambda _id: owner_group),
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(namespace, "target_group_membership_probe", _recorder(calls, True))

    with pytest.raises(RuntimeError, match="member of approved owner group"):
        ensure_tests._ensure(workspace, approved={"mip-owners"}, account_factory=lambda: account)

    assert len(calls) == 1
    assert calls[0]["deployer_workspace"] is workspace


def test_foreign_uc_audit_passes_its_deployer_client_to_the_default_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    probe = _recorder(calls, {audit_tests.WORKSPACE_USERS_GROUP_ID: "users"})
    monkeypatch.setattr(auditor, "_DEFAULT_TARGET_GROUPS_PROBE", probe)
    workspace = audit_tests._workspace()

    audit_tests._audit(workspace, target_groups_probe=probe, assert_single_writer=lambda: None)

    assert len(calls) == 1
    assert calls[0]["deployer_workspace"] is workspace


def test_foreign_uc_audit_custom_probe_gets_no_deployer_argument() -> None:
    calls: list[dict[str, Any]] = []

    audit_tests._audit(
        audit_tests._workspace(),
        target_groups_probe=_recorder(calls, {audit_tests.WORKSPACE_USERS_GROUP_ID: "users"}),
    )

    assert len(calls) == 1
    assert "deployer_workspace" not in calls[0]


def test_foreign_catalog_manifest_passes_its_deployer_client_to_the_default_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.invalid"))
    calls: list[dict[str, Any]] = []
    probe = _recorder(calls, _Stop())
    monkeypatch.setattr(manifest, "_DEFAULT_TARGET_GROUPS_PROBE", probe)
    monkeypatch.setattr(manifest, "stopped_app_identity", lambda *_args: object())
    monkeypatch.setattr(
        manifest,
        "_assert_metastore_owner_inventory_identity",
        lambda *_args, **_kwargs: ("metastore-id", "workspace-id"),
    )
    monkeypatch.setattr(manifest, "account_actor_identity", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        manifest, "account_target_identity", lambda *_args, **_kwargs: ("runtime-scim", "rt")
    )
    monkeypatch.setattr(
        manifest,
        "workspace_target_identity",
        lambda *_args, **_kwargs: SimpleNamespace(scim_id="runtime-scim"),
    )

    with pytest.raises(_Stop):
        manifest.boundary_evidence(
            workspace,
            object(),
            app_name="mip-app",
            application_id="runtime-app-client",
            expected_inventory_principal="deployer@example.com",
            expected_account_id="account-id",
            expected_account_client_id="account-client",
            approved_workspace_ids={"workspace-id"},
            target_groups_probe=probe,
            assert_single_writer=lambda: None,
        )

    assert len(calls) == 1
    assert calls[0]["deployer_workspace"] is workspace
