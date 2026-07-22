"""Credential and account-plane cleanup for exact bootstrap identities."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from databricks.sdk.errors import NotFound
from tools.databricks.lakebase_oauth_role_account_principal import (
    _exact_account_principal,
    assert_account_workspace_assignment_boundary,
    prove_exact_principal_absent_window,
    retire_bootstrap_account_principal,
    revoke_exact_account_principal_secrets,
)
from tools.databricks.lakebase_oauth_role_bootstrap_credentials import (
    disable_and_revoke_bootstrap_credentials,
)
from tools.databricks.lakebase_oauth_role_bootstrap_principal import (
    assert_bootstrap_principal_contract,
)


class CredentialCleanupError(RuntimeError):
    pass


class AccountPrincipalCleanupError(RuntimeError):
    pass


def prove_deleted_bootstrap_principal_absent(
    client: Any,
    account_client: Any,
    *,
    principal_id: str,
    application_id: str,
    deadline_seconds: float = 180.0,
) -> None:
    """Reconcile an ambiguous delete through continuous direct-ID absence."""

    try:
        canonical_application_id = str(UUID(application_id))
    except ValueError as exc:
        raise RuntimeError("temporary Lakebase deleted principal proof is incomplete") from exc
    if not principal_id.isdigit() or canonical_application_id != application_id:
        raise RuntimeError("temporary Lakebase deleted principal proof is incomplete")
    prove_exact_principal_absent_window(
        account_client,
        client,
        principal_id=principal_id,
        application_id=application_id,
        deadline_seconds=deadline_seconds,
    )


def revoke_credentials_and_retire_account_principal(
    client: Any,
    account_client: Any,
    *,
    principal_id: str,
    application_id: str,
    bootstrap_reservation_name: str,
    ownership_marker: str,
    allow_workspace_absence: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
) -> None:
    """Revoke secrets, then exact-delete the signed account principal."""

    expected_absent_identity: tuple[str, str] | None = None
    workspace_visible: bool | None = None
    account_principal = _exact_account_principal(
        account_client,
        principal_id=principal_id,
        application_id=application_id,
        display_name=None,
        bootstrap_reservation_name=bootstrap_reservation_name,
        ownership_marker=ownership_marker,
    )
    if account_principal is not None:
        try:
            workspace_visible = assert_account_workspace_assignment_boundary(
                account_client,
                client,
                principal_id=principal_id,
                application_id=application_id,
                display_name=str(getattr(account_principal, "display_name", "") or ""),
                expected_workspace_active=True,
            )
            revoke_exact_account_principal_secrets(
                account_client,
                principal_id=principal_id,
                bootstrap_lock_cursor=bootstrap_lock_cursor,
                bootstrap_lock_key=bootstrap_lock_key,
                allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
            )
        except Exception as exc:
            raise CredentialCleanupError(str(exc)) from exc
    if allow_workspace_absence:
        try:
            principal = client.service_principals.get(principal_id)
        except NotFound:
            expected_absent_identity = (principal_id, application_id)
            workspace_visible = False
        else:
            workspace_visible = True
            resolved_id, resolved_application_id = assert_bootstrap_principal_contract(
                client,
                principal,
                display_name=bootstrap_reservation_name,
                external_id=ownership_marker,
                account_client=account_client,
            )
            if (resolved_id, resolved_application_id) != (principal_id, application_id):
                raise CredentialCleanupError(
                    "label-derived bootstrap principal immutable identity drifted"
                )
    try:
        disable_and_revoke_bootstrap_credentials(
            client,
            service_principal_id=principal_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery=allow_unlocked_recovery_for_tests,
            expected_absent_identity=expected_absent_identity,
            allow_secret_proxy_not_found=workspace_visible is False,
        )
    except Exception as exc:
        raise CredentialCleanupError(str(exc)) from exc
    try:
        retire_bootstrap_account_principal(
            account_client,
            client,
            principal_id=principal_id,
            application_id=application_id,
            bootstrap_reservation_name=bootstrap_reservation_name,
            ownership_marker=ownership_marker,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        )
    except Exception as exc:
        raise AccountPrincipalCleanupError(str(exc)) from exc
