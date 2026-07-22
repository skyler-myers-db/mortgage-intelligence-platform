"""Admission-gated orchestration for one-use Lakebase role creation."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from databricks.sdk.errors import NotFound
from tools.databricks.lakebase_oauth_role_account_inventory import (
    assert_no_workspace_app_binding,
)
from tools.databricks.lakebase_oauth_role_account_principal import (
    assert_no_account_workspace_assignments,
    prove_exact_principal_absent_window,
    retire_bootstrap_account_principal,
)
from tools.databricks.lakebase_oauth_role_bootstrap_admission import (
    BootstrapAdmissionOutcome,
    BootstrapAdmissionProof,
    DatabaseCredentialLease,
    assert_singleton_bootstrap_secret_planes,
    capture_cached_m2m_access_token_expiry,
    finalize_bootstrap_admission_proof,
    mint_database_credential_lease,
    open_retained_bootstrap_backend,
    prove_bootstrap_secret_planes_empty,
    prove_destroyed_m2m_credential_rejected,
    prove_old_database_token_reuse_rejected,
    structured_database_auth_connect,
)
from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
    assert_bootstrap_lock_held,
)
from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    BootstrapBackendIdentity,
    assert_exact_bootstrap_backend_inventory,
    capture_bootstrap_backend_identity,
)

_BOOTSTRAP_SECRET_LIFETIME = "600s"  # nosec B105
_EXPIRY_SKEW = timedelta(seconds=120)
_HEARTBEAT_SECONDS = 15.0


def _close(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def assert_zero_bootstrap_backends(
    cursor: Any,
    *,
    application_id: str,
) -> None:
    """Require the new provider role to have no database sessions before auth."""

    cursor.execute(
        "SELECT oid, rolname FROM pg_roles WHERE rolname = %s ORDER BY oid",
        (application_id,),
    )
    roles = list(cursor.fetchall())
    if len(roles) != 1 or roles[0][1] != application_id:
        raise RuntimeError("temporary Lakebase bootstrap role identity is ambiguous")
    role_oid = int(roles[0][0])
    cursor.execute(
        """
        SELECT pid, usesysid, usename, application_name
        FROM pg_stat_activity
        WHERE usesysid = %s OR usename = %s
        ORDER BY pid
        """,
        (role_oid, application_id),
    )
    if list(cursor.fetchall()):
        raise RuntimeError("temporary Lakebase bootstrap role has a pre-admission backend")


def _converge_singleton_secret(
    workspace_client: Any,
    account_client: Any,
    *,
    service_principal_id: str,
    expected_secret_id: str,
    attempts: int = 15,
) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            observed = assert_singleton_bootstrap_secret_planes(
                workspace_client,
                account_client,
                service_principal_id=service_principal_id,
            )
            if observed != expected_secret_id:
                raise RuntimeError("temporary Lakebase bootstrap credential id changed")
            return
        except Exception as exc:  # noqa: BLE001 - eventual control-plane propagation
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"temporary Lakebase singleton credential did not converge{detail}")


def _authenticate_exact_m2m(
    client: Any,
    *,
    application_id: str,
    principal_label: str,
) -> None:
    if str(getattr(getattr(client, "config", None), "auth_type", "") or "") != "oauth-m2m":
        raise RuntimeError(f"{principal_label} did not use fresh OAuth-M2M authentication")
    identity = client.current_user.me()
    application = str(getattr(identity, "application_id", "") or "").strip()
    user_name = str(getattr(identity, "user_name", "") or "").strip()
    observed = tuple(value for value in (application, user_name) if value)
    if not observed or any(value != application_id for value in observed):
        raise RuntimeError(f"{principal_label} authenticated as the wrong identity")


def _revoke_exact_secret_both_planes(
    workspace_client: Any,
    account_client: Any,
    *,
    service_principal_id: str,
    secret_id: str,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> tuple[str, ...]:
    errors: list[str] = []
    for plane, api in (
        ("workspace", workspace_client.service_principal_secrets_proxy),
        ("account", account_client.service_principal_secrets),
    ):
        assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
        try:
            api.delete(service_principal_id, secret_id)
        except NotFound:
            # Both APIs address the same immutable secret. One plane may observe
            # the first plane's completed deletion before its own DELETE arrives.
            pass
        except Exception as exc:  # noqa: BLE001 - preserve both exact attempts
            errors.append(f"{plane}: {type(exc).__name__}")
    return tuple(errors)


def _assert_principal_absent_once(
    workspace_client: Any,
    account_client: Any,
    *,
    service_principal_id: str,
    application_id: str,
) -> None:
    for plane, api in (
        ("workspace", workspace_client.service_principals),
        ("account", account_client.service_principals),
    ):
        try:
            api.get(service_principal_id)
        except NotFound:
            continue
        raise RuntimeError(f"temporary Lakebase bootstrap principal reappeared on {plane} plane")
    assert_no_account_workspace_assignments(
        account_client,
        principal_id=service_principal_id,
    )
    assert_no_workspace_app_binding(
        workspace_client,
        application_ids={application_id},
    )


def _capture_same_backend(
    cursor: Any,
    *,
    lease: DatabaseCredentialLease,
    expected: BootstrapBackendIdentity,
) -> None:
    actual = capture_bootstrap_backend_identity(
        cursor,
        application_id=lease.database_user,
        database_name=lease.database_name,
        application_name=lease.application_name,
    )
    if actual != expected:
        raise RuntimeError("temporary Lakebase retained bootstrap backend identity changed")


def _admission_heartbeat(
    workspace_client: Any,
    account_client: Any,
    deployer_cursor: Any,
    retained_cursor: Any,
    *,
    lease: DatabaseCredentialLease,
    retained_backend: BootstrapBackendIdentity,
    service_principal_id: str,
    application_id: str,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
) -> None:
    assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
    _assert_principal_absent_once(
        workspace_client,
        account_client,
        service_principal_id=service_principal_id,
        application_id=application_id,
    )
    _capture_same_backend(retained_cursor, lease=lease, expected=retained_backend)
    assert_exact_bootstrap_backend_inventory(deployer_cursor, backend=retained_backend)


def _wait_through_bootstrap_auth_expiry(
    workspace_client: Any,
    account_client: Any,
    deployer_cursor: Any,
    retained_cursor: Any,
    *,
    lease: DatabaseCredentialLease,
    m2m_access_token_expires_at: datetime,
    retained_backend: BootstrapBackendIdentity,
    service_principal_id: str,
    application_id: str,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
    now_factory: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> None:
    if (
        m2m_access_token_expires_at.tzinfo is None
        or m2m_access_token_expires_at.utcoffset() is None
        or m2m_access_token_expires_at.utcoffset().total_seconds() != 0
    ):
        raise RuntimeError("temporary Lakebase M2M access-token expiry is not UTC")
    deadline = max(lease.expires_at, m2m_access_token_expires_at) + _EXPIRY_SKEW
    while True:
        _admission_heartbeat(
            workspace_client,
            account_client,
            deployer_cursor,
            retained_cursor,
            lease=lease,
            retained_backend=retained_backend,
            service_principal_id=service_principal_id,
            application_id=application_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        now = now_factory()
        if now.tzinfo is None or now.utcoffset() is None or now.utcoffset().total_seconds() != 0:
            raise RuntimeError("temporary Lakebase admission heartbeat clock is not UTC")
        remaining = (deadline - now.astimezone(UTC)).total_seconds()
        if remaining <= 0:
            return
        sleep(min(_HEARTBEAT_SECONDS, remaining))


def execute_admitted_provider_bootstrap(
    workspace_client: Any,
    account_client: Any,
    deployer_cursor: Any,
    *,
    workspace_client_factory: Callable[..., Any],
    connect: Callable[..., Any],
    workspace_host: str,
    instance_name: str,
    database_name: str,
    bootstrap_application_id: str,
    bootstrap_scim_id: str,
    bootstrap_reservation_name: str,
    bootstrap_external_id: str,
    control_application_id: str,
    control_client_secret: str,
    expected_executor: str,
    bootstrap_lock_cursor: Any,
    bootstrap_lock_key: Any,
    presecret_contract: Callable[[], None],
    positive_control: Callable[[], None],
    preinvoke_contract: Callable[[Any], None],
    mark_provider_invocation: Callable[[], None],
    invoke_provider: Callable[[Any], None],
    validate_provider_result: Callable[[Any], None],
    transaction_diagnostics: list[str],
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> BootstrapAdmissionProof:
    """Admit one retained backend, then and only then invoke the provider."""

    if (
        not control_application_id
        or not control_client_secret
        or control_application_id == bootstrap_application_id
    ):
        raise RuntimeError("fresh OAuth-M2M Lakebase control identity is invalid")
    assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
    presecret_contract()
    assert_zero_bootstrap_backends(
        deployer_cursor,
        application_id=bootstrap_application_id,
    )
    prove_bootstrap_secret_planes_empty(
        workspace_client,
        account_client,
        service_principal_id=bootstrap_scim_id,
    )

    assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
    created_secret = workspace_client.service_principal_secrets_proxy.create(
        bootstrap_scim_id,
        lifetime=_BOOTSTRAP_SECRET_LIFETIME,
    )
    secret_id = str(getattr(created_secret, "id", "") or "").strip()
    secret_value = str(getattr(created_secret, "secret", "") or "")
    if not secret_id or not secret_value:
        raise RuntimeError("temporary Lakebase bootstrap credential response is incomplete")
    _converge_singleton_secret(
        workspace_client,
        account_client,
        service_principal_id=bootstrap_scim_id,
        expected_secret_id=secret_id,
    )

    bootstrap_client = workspace_client_factory(
        host=workspace_host,
        client_id=bootstrap_application_id,
        client_secret=secret_value,
        auth_type="oauth-m2m",
    )
    _authenticate_exact_m2m(
        bootstrap_client,
        application_id=bootstrap_application_id,
        principal_label="temporary Lakebase bootstrap",
    )
    m2m_access_token_expires_at = capture_cached_m2m_access_token_expiry(
        bootstrap_client,
        now=now_factory(),
    )
    lease = mint_database_credential_lease(
        bootstrap_client,
        instance_name=instance_name,
        database_name=database_name,
        database_user=bootstrap_application_id,
    )
    retained_connection: Any | None = None
    retained_cursor: Any | None = None
    try:
        retained_connection = open_retained_bootstrap_backend(connect, lease=lease)
        if getattr(retained_connection, "autocommit", None) is not True:
            raise RuntimeError("temporary Lakebase retained bootstrap backend is not autocommit")
        retained_cursor = retained_connection.cursor()
        retained_backend = capture_bootstrap_backend_identity(
            retained_cursor,
            application_id=bootstrap_application_id,
            database_name=database_name,
            application_name=lease.application_name,
        )
        assert_exact_bootstrap_backend_inventory(deployer_cursor, backend=retained_backend)

        revocation_diagnostics = _revoke_exact_secret_both_planes(
            workspace_client,
            account_client,
            service_principal_id=bootstrap_scim_id,
            secret_id=secret_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        try:
            secret_absence = prove_bootstrap_secret_planes_empty(
                workspace_client,
                account_client,
                service_principal_id=bootstrap_scim_id,
            )
        except Exception as exc:
            if revocation_diagnostics:
                raise RuntimeError(
                    "temporary Lakebase bootstrap credential revocation remained ambiguous: "
                    f"{revocation_diagnostics!r}"
                ) from exc
            raise
        retire_bootstrap_account_principal(
            account_client,
            workspace_client,
            principal_id=bootstrap_scim_id,
            application_id=bootstrap_application_id,
            bootstrap_reservation_name=bootstrap_reservation_name,
            ownership_marker=bootstrap_external_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            allow_unlocked_recovery_for_tests=False,
        )
        prove_exact_principal_absent_window(
            account_client,
            workspace_client,
            principal_id=bootstrap_scim_id,
            application_id=bootstrap_application_id,
            bootstrap_reservation_name=bootstrap_reservation_name,
            ownership_marker=bootstrap_external_id,
            expected_workspace_active=True,
        )
        principal_absence = 3

        def fresh_destroyed_credential_read() -> Any:
            destroyed_client = workspace_client_factory(
                host=workspace_host,
                client_id=bootstrap_application_id,
                client_secret=secret_value,
                auth_type="oauth-m2m",
            )
            observed = destroyed_client.database.get_database_instance(instance_name)
            if str(getattr(observed, "name", "") or "").strip() != instance_name:
                raise RuntimeError("temporary Lakebase destroyed M2M read changed instances")
            return observed

        def fresh_m2m_lakebase_control() -> None:
            control_client = workspace_client_factory(
                host=workspace_host,
                client_id=control_application_id,
                client_secret=control_client_secret,
                auth_type="oauth-m2m",
            )
            _authenticate_exact_m2m(
                control_client,
                application_id=control_application_id,
                principal_label="Lakebase bootstrap positive control",
            )
            observed = control_client.database.get_database_instance(instance_name)
            if str(getattr(observed, "name", "") or "").strip() != instance_name:
                raise RuntimeError("Lakebase OAuth-M2M positive control changed instances")

        # Secret deletion does not revoke a bearer already cached by the SDK,
        # and a database password can remain valid for its full lease. Always
        # cross both captured expiries; no early 401 or 28P01 observation may
        # optimize this boundary away.
        _wait_through_bootstrap_auth_expiry(
            workspace_client,
            account_client,
            deployer_cursor,
            retained_cursor,
            lease=lease,
            m2m_access_token_expires_at=m2m_access_token_expires_at,
            retained_backend=retained_backend,
            service_principal_id=bootstrap_scim_id,
            application_id=bootstrap_application_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
            now_factory=now_factory,
            sleep=sleep,
        )
        m2m_proof = prove_destroyed_m2m_credential_rejected(
            fresh_destroyed_credential_read,
            positive_control=fresh_m2m_lakebase_control,
            sleep=sleep,
        )
        old_token_proof = prove_old_database_token_reuse_rejected(
            connect,
            lease=lease,
            deployer_cursor=deployer_cursor,
            retained_backend=retained_backend,
            expected_executor=expected_executor,
            positive_control=positive_control,
            auth_probe_connect=structured_database_auth_connect,
        )
        if old_token_proof.outcome is not BootstrapAdmissionOutcome.ADMITTED:
            raise RuntimeError(
                "temporary Lakebase database credential remained reusable after expiry"
            )
        proof = finalize_bootstrap_admission_proof(
            lease=lease,
            retained_backend=retained_backend,
            secret_plane_absence_observations=secret_absence,
            principal_absence_observations=principal_absence,
            m2m_secret_proof=m2m_proof,
            old_token_proof=old_token_proof,
        )
        if not proof.ready_for_provider_invocation:
            raise RuntimeError("temporary Lakebase bootstrap admission did not converge")

        _admission_heartbeat(
            workspace_client,
            account_client,
            deployer_cursor,
            retained_cursor,
            lease=lease,
            retained_backend=retained_backend,
            service_principal_id=bootstrap_scim_id,
            application_id=bootstrap_application_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        preinvoke_contract(retained_cursor)
        retained_connection.autocommit = False
        if getattr(retained_connection, "autocommit", None) is not False:
            raise RuntimeError("temporary Lakebase provider transaction did not start explicitly")
        _capture_same_backend(retained_cursor, lease=lease, expected=retained_backend)
        _admission_heartbeat(
            workspace_client,
            account_client,
            deployer_cursor,
            retained_cursor,
            lease=lease,
            retained_backend=retained_backend,
            service_principal_id=bootstrap_scim_id,
            application_id=bootstrap_application_id,
            bootstrap_lock_cursor=bootstrap_lock_cursor,
            bootstrap_lock_key=bootstrap_lock_key,
        )
        preinvoke_contract(retained_cursor)
        try:
            mark_provider_invocation()
            invoke_provider(retained_cursor)
            validate_provider_result(retained_cursor)
            assert_bootstrap_lock_held(bootstrap_lock_cursor, lock_key=bootstrap_lock_key)
            retained_connection.commit()
        except BaseException:
            try:
                retained_connection.rollback()
            except Exception as rollback_error:  # noqa: BLE001 - preserve for quarantine
                transaction_diagnostics.append(
                    "bootstrap transaction rollback: " f"{type(rollback_error).__name__}"
                )
            raise
        return proof
    finally:
        if retained_cursor is not None:
            _close(retained_cursor)
        if retained_connection is not None:
            _close(retained_connection)
