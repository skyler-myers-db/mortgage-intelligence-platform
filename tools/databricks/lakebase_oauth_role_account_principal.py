"""Exact account-plane retirement for one-use Lakebase SCIM principals."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from typing import Any

from databricks.sdk import AccountClient
from databricks.sdk.config import Config
from databricks.sdk.errors import NotFound
from tools.databricks.lakebase_oauth_role_account_inventory import (
    assert_no_workspace_app_binding,
)
from tools.databricks.lakebase_oauth_role_bootstrap_credentials import (
    assert_workspace_mutation_lease,
)

_DELETION_DEADLINE_SECONDS = 180.0
_DELETION_STABILITY_SECONDS = 30.0
_DELETION_POLL_SECONDS = 2.0
_SDK_HTTP_TIMEOUT_SECONDS = 30
_SDK_RETRY_TIMEOUT_SECONDS = 30


def account_client_from_env() -> AccountClient:
    """Build account auth without inheriting workspace credentials."""

    values = {
        "host": os.environ.get("DATABRICKS_ACCOUNT_HOST", "").strip(),
        "account_id": os.environ.get("DATABRICKS_ACCOUNT_ID", "").strip(),
        "client_id": os.environ.get("DATABRICKS_ACCOUNT_CLIENT_ID", "").strip(),
        "client_secret": os.environ.get("DATABRICKS_ACCOUNT_CLIENT_SECRET", "").strip(),
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise RuntimeError(
            "Lakebase bootstrap account cleanup requires dedicated account OAuth: "
            + ", ".join(missing)
        )
    return AccountClient(
        config=Config(
            host=values["host"],
            account_id=values["account_id"],
            client_id=values["client_id"],
            client_secret=values["client_secret"],
            auth_type="oauth-m2m",
            http_timeout_seconds=_SDK_HTTP_TIMEOUT_SECONDS,
            retry_timeout_seconds=_SDK_RETRY_TIMEOUT_SECONDS,
        )
    )


def _exact_account_principal(
    account_client: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str | None,
    bootstrap_reservation_name: str | None = None,
    ownership_marker: str | None = None,
) -> Any | None:
    try:
        principal = account_client.service_principals.get(principal_id)
    except NotFound:
        return None
    immutable = tuple(
        str(getattr(principal, field, "") or "")
        for field in ("id", "application_id", "display_name", "external_id")
    )
    actual_display_name = immutable[2]
    if (
        immutable[:2] != (principal_id, application_id)
        or immutable[3]
        or any(getattr(principal, field, None) for field in ("groups", "roles", "entitlements"))
    ):
        raise RuntimeError("temporary Lakebase account principal contract drifted")
    if display_name is not None:
        if actual_display_name != display_name:
            raise RuntimeError("temporary Lakebase account principal contract drifted")
    else:
        from tools.databricks.lakebase_oauth_role_scim_marker import (
            assert_bootstrap_principal_display_name,
        )

        if not bootstrap_reservation_name or not ownership_marker:
            raise RuntimeError("temporary Lakebase account principal marker is incomplete")
        assert_bootstrap_principal_display_name(
            actual_display_name,
            expected_name=bootstrap_reservation_name,
            ownership_marker=ownership_marker,
        )
    return principal


def assert_no_account_workspace_assignments(
    account_client: Any,
    *,
    principal_id: str,
) -> None:
    """Prove an exact principal is unassigned in every account workspace."""

    if not principal_id:
        raise RuntimeError("temporary Lakebase account principal id is incomplete")
    for workspace in account_client.workspaces.list():
        workspace_id = getattr(workspace, "workspace_id", None)
        if not isinstance(workspace_id, int):
            raise RuntimeError("temporary Lakebase workspace inventory is incomplete")
        for assignment in account_client.workspace_assignment.list(workspace_id):
            if getattr(assignment, "error", None):
                raise RuntimeError("temporary Lakebase workspace assignment inventory failed")
            principal = getattr(assignment, "principal", None)
            assigned_id = str(getattr(principal, "principal_id", "") or "").strip()
            if not assigned_id:
                raise RuntimeError("temporary Lakebase workspace assignment is incomplete")
            if assigned_id == principal_id:
                raise RuntimeError("temporary Lakebase account principal remains assigned")


def assert_account_workspace_assignment_boundary(
    account_client: Any,
    workspace_client: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str,
    expected_workspace_active: bool,
) -> bool:
    """Allow only the exact current assignment for a workspace-visible marker."""

    expected_identity = (principal_id, application_id, display_name, "")
    try:
        workspace_principal = workspace_client.service_principals.get(principal_id)
    except NotFound:
        workspace_visible = False
    else:
        workspace_visible = True
        workspace_identity = tuple(
            str(getattr(workspace_principal, field, "") or "")
            for field in ("id", "application_id", "display_name", "external_id")
        )
        if (
            workspace_identity != expected_identity
            or getattr(workspace_principal, "active", None) is not expected_workspace_active
            or any(
                getattr(workspace_principal, field, None)
                for field in ("groups", "roles", "entitlements")
            )
        ):
            raise RuntimeError("temporary Lakebase workspace marker contract drifted")

    assigned_workspace_ids: set[int] = set()
    for workspace in account_client.workspaces.list():
        workspace_id = getattr(workspace, "workspace_id", None)
        if not isinstance(workspace_id, int):
            raise RuntimeError("temporary Lakebase workspace inventory is incomplete")
        for assignment in account_client.workspace_assignment.list(workspace_id):
            if getattr(assignment, "error", None):
                raise RuntimeError("temporary Lakebase workspace assignment inventory failed")
            principal = getattr(assignment, "principal", None)
            assigned_id = str(getattr(principal, "principal_id", "") or "").strip()
            if not assigned_id:
                raise RuntimeError("temporary Lakebase workspace assignment is incomplete")
            if assigned_id == principal_id:
                assigned_workspace_ids.add(workspace_id)

    if workspace_visible:
        current_workspace_id = workspace_client.get_workspace_id()
        if not isinstance(current_workspace_id, int):
            raise RuntimeError("temporary Lakebase current workspace id is incomplete")
        if assigned_workspace_ids != {current_workspace_id}:
            raise RuntimeError("temporary Lakebase account principal assignment drifted")
    elif assigned_workspace_ids:
        raise RuntimeError("temporary Lakebase account-only principal remains assigned")
    return workspace_visible


def revoke_exact_account_principal_secrets(
    account_client: Any,
    *,
    principal_id: str,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery_for_tests: bool = False,
    attempts: int = 15,
) -> None:
    """Revoke account OAuth secrets by immutable principal and secret ids."""

    if not principal_id:
        raise RuntimeError("temporary Lakebase account principal id is incomplete")
    stable_empty = 0
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            secrets = list(account_client.service_principal_secrets.list(principal_id))
            for secret in secrets:
                secret_id = str(getattr(secret, "id", "") or "").strip()
                if not secret_id:
                    raise RuntimeError("temporary Lakebase account credential has no immutable id")
                assert_workspace_mutation_lease(
                    bootstrap_lock_cursor,
                    bootstrap_lock_key,
                    allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
                )
                with suppress(Exception):  # a following LIST proves the outcome
                    account_client.service_principal_secrets.delete(
                        principal_id,
                        secret_id,
                    )
            remaining = list(account_client.service_principal_secrets.list(principal_id))
            if remaining:
                raise RuntimeError(
                    "temporary Lakebase account credential quarantine did not converge"
                )
            stable_empty += 1
            last_error = None
            if stable_empty >= 3:
                return
        except Exception as exc:  # noqa: BLE001 - retry account-plane propagation
            stable_empty = 0
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"temporary Lakebase account credential quarantine did not converge{detail}")


def assert_exact_account_principal_has_no_secrets(
    account_client: Any,
    *,
    principal_id: str,
) -> None:
    """Reject a signed tombstone that was ever made credential-bearing."""

    secrets = list(account_client.service_principal_secrets.list(principal_id))
    if secrets:
        raise RuntimeError("temporary Lakebase account tombstone has credentials")


def _assert_account_principal_deletion_boundary(
    account_client: Any,
    workspace_client: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str,
    expected_workspace_active: bool,
) -> None:
    assert_exact_account_principal_has_no_secrets(
        account_client,
        principal_id=principal_id,
    )
    assert_account_workspace_assignment_boundary(
        account_client,
        workspace_client,
        principal_id=principal_id,
        application_id=application_id,
        display_name=display_name,
        expected_workspace_active=expected_workspace_active,
    )
    assert_no_workspace_app_binding(
        workspace_client,
        application_ids={application_id},
    )


def _assert_direct_principal_contract(
    principal: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str | None,
    bootstrap_reservation_name: str | None,
    ownership_marker: str | None,
    expected_active: bool | None,
    plane: str,
) -> None:
    immutable = tuple(
        str(getattr(principal, field, "") or "")
        for field in ("id", "application_id", "display_name", "external_id")
    )
    if (
        immutable[:2] != (principal_id, application_id)
        or immutable[3]
        or any(getattr(principal, field, None) for field in ("groups", "roles", "entitlements"))
    ):
        raise RuntimeError(f"temporary Lakebase {plane} principal immutable identity drifted")
    if display_name is not None:
        if immutable[2] != display_name:
            raise RuntimeError(f"temporary Lakebase {plane} principal immutable identity drifted")
    elif bootstrap_reservation_name is not None or ownership_marker is not None:
        from tools.databricks.lakebase_oauth_role_scim_marker import (
            assert_bootstrap_principal_display_name,
        )

        if not bootstrap_reservation_name or not ownership_marker:
            raise RuntimeError("temporary Lakebase bootstrap principal marker is incomplete")
        assert_bootstrap_principal_display_name(
            immutable[2],
            expected_name=bootstrap_reservation_name,
            ownership_marker=ownership_marker,
        )
    if expected_active is not None and getattr(principal, "active", None) is not expected_active:
        raise RuntimeError(f"temporary Lakebase {plane} principal active state drifted")


def prove_exact_principal_absent_window(
    account_client: Any,
    workspace_client: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str | None = None,
    bootstrap_reservation_name: str | None = None,
    ownership_marker: str | None = None,
    expected_workspace_active: bool | None = None,
    deadline_seconds: float = _DELETION_DEADLINE_SECONDS,
    stability_seconds: float = _DELETION_STABILITY_SECONDS,
    poll_seconds: float = _DELETION_POLL_SECONDS,
) -> None:
    """Prove continuous two-plane direct-ID absence without LIST authority."""

    if (
        not principal_id
        or not application_id
        or deadline_seconds < stability_seconds
        or stability_seconds < 0
        or poll_seconds <= 0
    ):
        raise RuntimeError("temporary Lakebase principal absence proof is incomplete")
    deadline_at = time.monotonic() + deadline_seconds
    stable_since: float | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline_at:
        account_absent = False
        workspace_absent = False
        try:
            account_principal = account_client.service_principals.get(principal_id)
        except NotFound:
            account_absent = True
        else:
            _assert_direct_principal_contract(
                account_principal,
                principal_id=principal_id,
                application_id=application_id,
                display_name=display_name,
                bootstrap_reservation_name=bootstrap_reservation_name,
                ownership_marker=ownership_marker,
                expected_active=None,
                plane="account",
            )
        try:
            workspace_principal = workspace_client.service_principals.get(principal_id)
        except NotFound:
            workspace_absent = True
        else:
            _assert_direct_principal_contract(
                workspace_principal,
                principal_id=principal_id,
                application_id=application_id,
                display_name=display_name,
                bootstrap_reservation_name=bootstrap_reservation_name,
                ownership_marker=ownership_marker,
                expected_active=expected_workspace_active,
                plane="workspace",
            )
        try:
            assert_no_account_workspace_assignments(
                account_client,
                principal_id=principal_id,
            )
            assert_no_workspace_app_binding(
                workspace_client,
                application_ids={application_id},
            )
        except Exception as exc:  # noqa: BLE001 - a later clean window may converge
            last_error = exc
            stable_since = None
        else:
            if account_absent and workspace_absent:
                observed_at = time.monotonic()
                if stable_since is None:
                    stable_since = observed_at
                last_error = None
                if observed_at - stable_since >= stability_seconds:
                    return
            else:
                stable_since = None
                last_error = RuntimeError("temporary Lakebase principal remains present")
        time.sleep(poll_seconds)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"temporary Lakebase principal absence did not stabilize{detail}")


def _retire_account_principal(
    account_client: Any,
    workspace_client: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str | None,
    bootstrap_reservation_name: str | None,
    ownership_marker: str | None,
    expected_workspace_active: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
    deadline_seconds: float,
) -> None:
    if deadline_seconds < _DELETION_STABILITY_SECONDS:
        raise RuntimeError("temporary Lakebase account deletion contract is incomplete")
    deadline_at = time.monotonic() + deadline_seconds
    stable_since: float | None = None
    last_error: Exception | None = None
    while time.monotonic() < deadline_at:
        principal = _exact_account_principal(
            account_client,
            principal_id=principal_id,
            application_id=application_id,
            display_name=display_name,
            bootstrap_reservation_name=bootstrap_reservation_name,
            ownership_marker=ownership_marker,
        )
        if principal is not None:
            stable_since = None
            try:
                _assert_account_principal_deletion_boundary(
                    account_client,
                    workspace_client,
                    principal_id=principal_id,
                    application_id=application_id,
                    display_name=str(getattr(principal, "display_name", "") or ""),
                    expected_workspace_active=expected_workspace_active,
                )
                assert_workspace_mutation_lease(
                    bootstrap_lock_cursor,
                    bootstrap_lock_key,
                    allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
                )
                account_client.service_principals.delete(principal_id)
                last_error = None
            except Exception as exc:  # noqa: BLE001 - direct GET decides the outcome
                last_error = exc
            time.sleep(_DELETION_POLL_SECONDS)
            continue
        try:
            workspace_principal = workspace_client.service_principals.get(principal_id)
        except NotFound:
            workspace_absent = True
        else:
            workspace_absent = False
            _assert_direct_principal_contract(
                workspace_principal,
                principal_id=principal_id,
                application_id=application_id,
                display_name=display_name,
                bootstrap_reservation_name=bootstrap_reservation_name,
                ownership_marker=ownership_marker,
                expected_active=expected_workspace_active,
                plane="workspace",
            )
        try:
            assert_no_account_workspace_assignments(
                account_client,
                principal_id=principal_id,
            )
            assert_no_workspace_app_binding(
                workspace_client,
                application_ids={application_id},
            )
        except Exception as exc:  # noqa: BLE001 - reset the clean window
            stable_since = None
            last_error = exc
        else:
            if workspace_absent:
                observed_at = time.monotonic()
                if stable_since is None:
                    stable_since = observed_at
                last_error = None
                if observed_at - stable_since >= _DELETION_STABILITY_SECONDS:
                    return
            else:
                stable_since = None
                last_error = RuntimeError(
                    "account-deleted bootstrap principal remains in the workspace"
                )
        time.sleep(_DELETION_POLL_SECONDS)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"temporary Lakebase account principal deletion did not converge{detail}")


def retire_exact_account_principal(
    account_client: Any,
    workspace_client: Any,
    *,
    principal_id: str,
    application_id: str,
    display_name: str,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery_for_tests: bool = False,
    deadline_seconds: float = _DELETION_DEADLINE_SECONDS,
) -> None:
    """Delete only a captured immutable account id and prove both-plane absence."""

    if not all((principal_id, application_id, display_name)):
        raise RuntimeError("temporary Lakebase account principal identity is incomplete")
    _retire_account_principal(
        account_client,
        workspace_client,
        principal_id=principal_id,
        application_id=application_id,
        display_name=display_name,
        bootstrap_reservation_name=None,
        ownership_marker=None,
        expected_workspace_active=False,
        bootstrap_lock_cursor=bootstrap_lock_cursor,
        bootstrap_lock_key=bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        deadline_seconds=deadline_seconds,
    )


def retire_bootstrap_account_principal(
    account_client: Any,
    workspace_client: Any,
    *,
    principal_id: str,
    application_id: str,
    bootstrap_reservation_name: str,
    ownership_marker: str,
    bootstrap_lock_cursor: Any | None = None,
    bootstrap_lock_key: Any | None = None,
    allow_unlocked_recovery_for_tests: bool = False,
    deadline_seconds: float = _DELETION_DEADLINE_SECONDS,
) -> None:
    """Retire an exact id only while its account display has a valid bootstrap signature."""

    if not all((principal_id, application_id, bootstrap_reservation_name, ownership_marker)):
        raise RuntimeError("temporary Lakebase account principal identity is incomplete")
    _retire_account_principal(
        account_client,
        workspace_client,
        principal_id=principal_id,
        application_id=application_id,
        display_name=None,
        bootstrap_reservation_name=bootstrap_reservation_name,
        ownership_marker=ownership_marker,
        expected_workspace_active=True,
        bootstrap_lock_cursor=bootstrap_lock_cursor,
        bootstrap_lock_key=bootstrap_lock_key,
        allow_unlocked_recovery_for_tests=allow_unlocked_recovery_for_tests,
        deadline_seconds=deadline_seconds,
    )
