from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.errors import NotFound

from tools.databricks import lakebase_oauth_role_account_inventory as account_inventory
from tools.databricks import lakebase_oauth_role_account_principal as account_principal
from tools.databricks.lakebase_oauth_role_account_inventory import (
    exact_account_principals_by_display_prefix,
    prove_account_application_id_absent,
)
from tools.databricks.lakebase_oauth_role_recovery_identity import (
    prove_deleted_bootstrap_principal_absent,
)

_PRINCIPAL_ID = "78879891843203"
_APPLICATION_ID = "3ca89330-2494-4b09-9587-1604d9432429"


def _not_found() -> NotFound:
    return NotFound("absent")


def test_account_client_uses_dedicated_credentials_and_bounded_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "DATABRICKS_ACCOUNT_HOST": "https://accounts.cloud.databricks.com",
        "DATABRICKS_ACCOUNT_ID": "account-id",
        "DATABRICKS_ACCOUNT_CLIENT_ID": "client-id",
        "DATABRICKS_ACCOUNT_CLIENT_SECRET": "client-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    account_client = MagicMock()
    constructor = MagicMock(return_value=account_client)
    config = SimpleNamespace()
    config_constructor = MagicMock(return_value=config)
    monkeypatch.setattr(account_principal, "AccountClient", constructor)
    monkeypatch.setattr(account_principal, "Config", config_constructor)

    assert account_principal.account_client_from_env() is account_client

    assert constructor.call_args.kwargs == {"config": config}
    assert config_constructor.call_args.kwargs == {
        "host": values["DATABRICKS_ACCOUNT_HOST"],
        "account_id": values["DATABRICKS_ACCOUNT_ID"],
        "client_id": values["DATABRICKS_ACCOUNT_CLIENT_ID"],
        "client_secret": values["DATABRICKS_ACCOUNT_CLIENT_SECRET"],
        "auth_type": "oauth-m2m",
        "http_timeout_seconds": 30,
        "retry_timeout_seconds": 30,
    }


def test_account_prefix_inventory_retries_not_found_without_treating_it_as_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name = "signed.prefix.identity"
    exact = SimpleNamespace(
        id=_PRINCIPAL_ID,
        application_id=_APPLICATION_ID,
        display_name=display_name,
    )
    account_client = MagicMock()
    account_client.service_principals.list.side_effect = [
        _not_found(),
        iter([exact]),
    ]
    account_client.service_principals.get.return_value = exact
    sleep = MagicMock()
    monkeypatch.setattr(account_inventory.time, "sleep", sleep)

    assert exact_account_principals_by_display_prefix(
        account_client,
        display_prefix="signed.prefix.",
        attempts=3,
    ) == [exact]
    assert account_client.service_principals.list.call_count == 2
    account_client.service_principals.get.assert_called_once_with(_PRINCIPAL_ID)
    sleep.assert_called_once_with(1)


def test_account_prefix_inventory_rejects_repeated_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_client = MagicMock()
    account_client.service_principals.list.side_effect = _not_found()
    sleep = MagicMock()
    monkeypatch.setattr(account_inventory.time, "sleep", sleep)

    with pytest.raises(RuntimeError, match="inventory did not stabilize"):
        exact_account_principals_by_display_prefix(
            account_client,
            display_prefix="signed.prefix.",
            attempts=3,
        )

    assert account_client.service_principals.list.call_count == 3
    account_client.service_principals.get.assert_not_called()
    assert sleep.call_count == 2


@patch("tools.databricks.lakebase_oauth_role_account_inventory.time.sleep")
def test_application_id_list_omission_is_not_absence_authority(
    sleep: MagicMock,
) -> None:
    account_client = MagicMock()
    account_client.service_principals.list.return_value = iter(())

    with pytest.raises(RuntimeError, match="requires its immutable SCIM id"):
        prove_account_application_id_absent(
            account_client,
            application_id=_APPLICATION_ID,
        )

    assert account_client.service_principals.list.call_count == 3
    assert sleep.call_count == 2


def test_immutable_absence_proof_requires_continuous_window_across_reappearance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_client = MagicMock()
    workspace_client = MagicMock()
    exact = SimpleNamespace(id=_PRINCIPAL_ID, application_id=_APPLICATION_ID)
    account_reads = 0

    def account_get(_principal_id: str) -> SimpleNamespace:
        nonlocal account_reads
        account_reads += 1
        if account_reads == 4:
            return exact
        raise _not_found()

    tick = 0

    def monotonic() -> float:
        nonlocal tick
        tick += 5
        return float(tick)

    account_client.service_principals.get.side_effect = account_get
    workspace_client.service_principals.get.side_effect = _not_found()
    account_client.workspaces.list.side_effect = lambda: iter([])
    workspace_client.apps.list.side_effect = lambda: iter([])
    monkeypatch.setattr(account_principal.time, "monotonic", monotonic)
    monkeypatch.setattr(account_principal.time, "sleep", lambda _seconds: None)

    prove_deleted_bootstrap_principal_absent(
        workspace_client,
        account_client,
        principal_id=_PRINCIPAL_ID,
        application_id=_APPLICATION_ID,
    )

    assert account_reads > 4
    assert workspace_client.service_principals.get.call_count == account_reads
    account_client.service_principals.list.assert_not_called()


def test_immutable_absence_proof_rejects_reused_principal_id() -> None:
    account_client = MagicMock()
    workspace_client = MagicMock()
    account_client.service_principals.get.return_value = SimpleNamespace(
        id=_PRINCIPAL_ID,
        application_id="11111111-1111-4111-8111-111111111111",
    )

    with pytest.raises(RuntimeError, match="immutable identity drifted"):
        prove_deleted_bootstrap_principal_absent(
            workspace_client,
            account_client,
            principal_id=_PRINCIPAL_ID,
            application_id=_APPLICATION_ID,
        )

    workspace_client.service_principals.get.assert_not_called()


def test_exact_retirement_repeats_delete_after_direct_get_reappearance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    display_name = "signed-tombstone"
    account_principal_row = SimpleNamespace(
        id=_PRINCIPAL_ID,
        application_id=_APPLICATION_ID,
        display_name=display_name,
        external_id=None,
        active=True,
        groups=[],
        roles=[],
        entitlements=[],
    )
    workspace_principal_row = SimpleNamespace(**vars(account_principal_row))
    workspace_principal_row.active = False
    account_client = MagicMock()
    workspace_client = MagicMock()
    delete_count = 0
    post_delete_reads = 0

    def account_get(_principal_id: str) -> SimpleNamespace:
        nonlocal post_delete_reads
        if delete_count == 0:
            return account_principal_row
        if delete_count == 1:
            post_delete_reads += 1
            if post_delete_reads == 4:
                return account_principal_row
        raise _not_found()

    def workspace_get(_principal_id: str) -> SimpleNamespace:
        if delete_count == 0:
            return workspace_principal_row
        raise _not_found()

    def delete(_principal_id: str) -> None:
        nonlocal delete_count
        delete_count += 1

    def assignments(_workspace_id: int) -> object:
        if delete_count == 0:
            return iter(
                [
                    SimpleNamespace(
                        error=None,
                        principal=SimpleNamespace(principal_id=_PRINCIPAL_ID),
                    )
                ]
            )
        return iter([])

    tick = 0

    def monotonic() -> float:
        nonlocal tick
        tick += 5
        return float(tick)

    account_client.service_principals.get.side_effect = account_get
    account_client.service_principals.delete.side_effect = delete
    account_client.service_principal_secrets.list.side_effect = lambda _id: iter([])
    account_client.workspaces.list.side_effect = lambda: iter([SimpleNamespace(workspace_id=42)])
    account_client.workspace_assignment.list.side_effect = assignments
    workspace_client.service_principals.get.side_effect = workspace_get
    workspace_client.get_workspace_id.return_value = 42
    workspace_client.apps.list.side_effect = lambda: iter([])
    monkeypatch.setattr(account_principal.time, "monotonic", monotonic)
    monkeypatch.setattr(account_principal.time, "sleep", lambda _seconds: None)

    account_principal.retire_exact_account_principal(
        account_client,
        workspace_client,
        principal_id=_PRINCIPAL_ID,
        application_id=_APPLICATION_ID,
        display_name=display_name,
        allow_unlocked_recovery_for_tests=True,
    )

    assert account_principal._DELETION_DEADLINE_SECONDS == 180.0
    assert account_principal._DELETION_STABILITY_SECONDS == 30.0
    assert delete_count == 2
    assert post_delete_reads == 4
    assert account_client.service_principals.get.call_count > post_delete_reads
    account_client.service_principals.list.assert_not_called()
