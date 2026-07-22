"""Immutable admission proof for a privileged provider-owned bootstrap role."""

from __future__ import annotations

import re
import ssl
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from databricks.sdk.errors import PermissionDenied, Unauthenticated
from tools.databricks.lakebase_oauth_role_bootstrap import read_profile
from tools.databricks.lakebase_oauth_role_bootstrap_contract import (
    assert_bootstrap_admission_identity,
)
from tools.databricks.lakebase_oauth_role_bootstrap_lock import (
    assert_bootstrap_lock_held,
)
from tools.databricks.lakebase_oauth_role_bootstrap_sessions import (
    BootstrapBackendIdentity,
    assert_exact_bootstrap_backend_inventory,
    capture_bootstrap_backend_identity,
    drain_captured_bootstrap_backend,
)

_APPLICATION_NAME_PREFIX = "mip-bootstrap-admission-"
_REUSE_APPLICATION_NAME_PREFIX = "mip-bootstrap-reuse-"
_AUTH_REJECTION_CODES = frozenset(
    {
        "invalid_client",
        "invalid_grant",
        "unauthenticated",
        "unauthorized_client",
    }
)
_M2M_REJECTION_INTERVAL_SECONDS = 5.0
_OAUTH_ERROR_CODE = re.compile(r"^(?P<code>[a-z][a-z0-9_]*)\s*:")
_MAX_BOOTSTRAP_AUTH_TTL = timedelta(minutes=65)


class BootstrapAdmissionOutcome(str, Enum):
    ADMITTED = "admitted"
    TOKEN_EXPIRY_REQUIRED = "token_expiry_required"


@dataclass(frozen=True)
class M2MSecretRejectionProof:
    rejection_observations: int

    def __post_init__(self) -> None:
        if self.rejection_observations != 3:
            raise ValueError("temporary Lakebase M2M rejection proof is incomplete")


@dataclass(frozen=True)
class DatabaseCredentialLease:
    """One minted database token plus bounded, non-secret lease metadata."""

    host: str
    database_name: str
    database_user: str
    application_name: str
    request_id: str
    expires_at: datetime
    token: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not all(
            (
                self.host,
                self.database_name,
                self.database_user,
                self.application_name,
                self.request_id,
                self.token,
            )
        ):
            raise ValueError("temporary Lakebase database credential lease is incomplete")
        try:
            uuid.UUID(self.request_id)
        except ValueError as exc:
            raise ValueError(
                "temporary Lakebase database credential request id is invalid"
            ) from exc
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("temporary Lakebase database credential expiry is not UTC")
        if self.expires_at.utcoffset().total_seconds() != 0:
            raise ValueError("temporary Lakebase database credential expiry is not UTC")
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(UTC))


@dataclass(frozen=True)
class OldTokenReuseProof:
    outcome: BootstrapAdmissionOutcome
    rejection_observations: int
    reuse_backend: BootstrapBackendIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, BootstrapAdmissionOutcome):
            raise ValueError("temporary Lakebase old-token outcome is invalid")
        if self.outcome is BootstrapAdmissionOutcome.ADMITTED:
            if self.rejection_observations != 3 or self.reuse_backend is not None:
                raise ValueError("temporary Lakebase old-token rejection proof is incomplete")
        elif self.outcome is BootstrapAdmissionOutcome.TOKEN_EXPIRY_REQUIRED and (
            self.reuse_backend is None or not 0 <= self.rejection_observations < 3
        ):
            raise ValueError("temporary Lakebase old-token reuse proof is incomplete")


@dataclass(frozen=True)
class BootstrapAdmissionProof:
    """Secret-free result that alone decides whether provider invocation is safe."""

    outcome: BootstrapAdmissionOutcome
    retained_backend: BootstrapBackendIdentity
    credential_expires_at: datetime
    secret_plane_absence_observations: int
    principal_absence_observations: int
    m2m_rejection_observations: int
    old_token_rejection_observations: int

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, BootstrapAdmissionOutcome):
            raise ValueError("temporary Lakebase admission outcome is invalid")
        if (
            self.credential_expires_at.tzinfo is None
            or self.credential_expires_at.utcoffset() is None
            or self.credential_expires_at.utcoffset().total_seconds() != 0
        ):
            raise ValueError("temporary Lakebase admission credential expiry is not UTC")
        object.__setattr__(
            self, "credential_expires_at", self.credential_expires_at.astimezone(UTC)
        )
        if self.secret_plane_absence_observations < 3:
            raise ValueError("temporary Lakebase secret-plane absence proof is incomplete")
        if self.principal_absence_observations < 3:
            raise ValueError("temporary Lakebase principal absence proof is incomplete")
        if self.m2m_rejection_observations != 3:
            raise ValueError("temporary Lakebase M2M rejection proof is incomplete")
        if self.outcome is BootstrapAdmissionOutcome.ADMITTED:
            if self.old_token_rejection_observations != 3:
                raise ValueError("temporary Lakebase old-token rejection proof is incomplete")
        elif not 0 <= self.old_token_rejection_observations < 3:
            raise ValueError("temporary Lakebase token-expiry proof is incomplete")

    @property
    def ready_for_provider_invocation(self) -> bool:
        return self.outcome is BootstrapAdmissionOutcome.ADMITTED


def _parse_bounded_utc_expiry(
    raw: Any,
    *,
    now: datetime,
    minimum_ttl: timedelta,
    maximum_ttl: timedelta,
) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("Lakebase database credential response has no expiration time")
    value = raw.strip()
    try:
        parsed = datetime.fromisoformat(
            value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
        )
    except ValueError as exc:
        raise RuntimeError("Lakebase database credential expiration time is invalid") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise RuntimeError("Lakebase database credential expiration time is not UTC")
    parsed = parsed.astimezone(UTC)
    ttl = parsed - now.astimezone(UTC)
    if ttl < minimum_ttl or ttl > maximum_ttl:
        raise RuntimeError("Lakebase database credential expiration time is outside policy")
    return parsed


def mint_database_credential_lease(
    client: Any,
    *,
    instance_name: str,
    database_name: str,
    database_user: str,
    now: datetime | None = None,
    minimum_ttl: timedelta = timedelta(seconds=30),
    maximum_ttl: timedelta = _MAX_BOOTSTRAP_AUTH_TTL,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> DatabaseCredentialLease:
    """Mint exactly one database credential with a bounded UTC expiration."""

    if not all((instance_name, database_name, database_user)):
        raise RuntimeError("temporary Lakebase database credential request is incomplete")
    if minimum_ttl <= timedelta(0) or maximum_ttl < minimum_ttl:
        raise ValueError("temporary Lakebase database credential TTL policy is invalid")
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("temporary Lakebase database credential clock is not UTC")
    unique_id = uuid_factory()
    if not isinstance(unique_id, uuid.UUID):
        raise RuntimeError("temporary Lakebase database credential nonce is invalid")
    request_id = str(unique_id)
    application_name = f"{_APPLICATION_NAME_PREFIX}{unique_id.hex}"
    if len(application_name.encode("utf-8")) > 63:
        raise RuntimeError("temporary Lakebase database application name exceeds PostgreSQL limit")
    instance = client.database.get_database_instance(instance_name)
    host = str(getattr(instance, "read_write_dns", "") or "").strip()
    if not host:
        raise RuntimeError(f"Lakebase instance {instance_name!r} has no read_write_dns")
    credential = client.database.generate_database_credential(
        instance_names=[instance_name],
        request_id=request_id,
    )
    token = str(getattr(credential, "token", "") or "")
    if not token:
        raise RuntimeError("Lakebase database credential response contained no token")
    expires_at = _parse_bounded_utc_expiry(
        getattr(credential, "expiration_time", None),
        now=observed_now,
        minimum_ttl=minimum_ttl,
        maximum_ttl=maximum_ttl,
    )
    return DatabaseCredentialLease(
        host=host,
        database_name=database_name,
        database_user=database_user,
        application_name=application_name,
        request_id=request_id,
        expires_at=expires_at,
        token=token,
    )


def capture_cached_m2m_access_token_expiry(
    client: Any,
    *,
    now: datetime | None = None,
    minimum_ttl: timedelta = timedelta(seconds=30),
    maximum_ttl: timedelta = _MAX_BOOTSTRAP_AUTH_TTL,
) -> datetime:
    """Capture only the bounded UTC expiry of the already-authenticated OAuth token."""

    if minimum_ttl <= timedelta(0) or maximum_ttl < minimum_ttl:
        raise ValueError("temporary Lakebase M2M access-token TTL policy is invalid")
    config = getattr(client, "config", None)
    if config is None or getattr(config, "auth_type", None) != "oauth-m2m":
        raise RuntimeError("temporary Lakebase bootstrap client is not OAuth M2M")
    token = config.oauth_token()
    claims = token.jwt_claims()
    raw_expiry = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(raw_expiry, bool) or not isinstance(raw_expiry, int | float):
        raise RuntimeError("temporary Lakebase M2M access token has no numeric expiry")
    try:
        expires_at = datetime.fromtimestamp(raw_expiry, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise RuntimeError("temporary Lakebase M2M access-token expiry is invalid") from exc
    observed_now = now or datetime.now(UTC)
    if (
        observed_now.tzinfo is None
        or observed_now.utcoffset() is None
        or observed_now.utcoffset().total_seconds() != 0
    ):
        raise ValueError("temporary Lakebase M2M access-token clock is not UTC")
    ttl = expires_at - observed_now.astimezone(UTC)
    if ttl < minimum_ttl or ttl > maximum_ttl:
        raise RuntimeError("temporary Lakebase M2M access-token expiry is outside policy")
    return expires_at


def open_retained_bootstrap_backend(
    connect: Callable[..., Any],
    *,
    lease: DatabaseCredentialLease,
) -> Any:
    """Open the sole retained backend in idle-safe mode for the control-plane fence."""

    return _open_bootstrap_backend(
        connect,
        lease=lease,
        application_name=lease.application_name,
        autocommit=True,
    )


def _open_bootstrap_backend(
    connect: Callable[..., Any],
    *,
    lease: DatabaseCredentialLease,
    application_name: str,
    autocommit: bool,
) -> Any:
    if not application_name or len(application_name.encode("utf-8")) > 63:
        raise RuntimeError("temporary Lakebase database application name is invalid")
    return connect(
        host=lease.host,
        port=5432,
        dbname=lease.database_name,
        user=lease.database_user,
        password=lease.token,
        application_name=application_name,
        sslmode="require",
        connect_timeout=15,
        keepalives=1,
        keepalives_idle=10,
        keepalives_interval=5,
        keepalives_count=3,
        autocommit=autocommit,
    )


def assert_singleton_bootstrap_secret_planes(
    workspace_client: Any,
    account_client: Any,
    *,
    service_principal_id: str,
) -> str:
    """Require one identical immutable secret id on both SCIM control planes."""

    if not service_principal_id:
        raise RuntimeError("temporary Lakebase bootstrap principal id is incomplete")

    def ids(values: Any, *, plane: str) -> list[str]:
        result = [str(getattr(value, "id", "") or "").strip() for value in values]
        if any(not value for value in result):
            raise RuntimeError(f"temporary Lakebase {plane} credential has no immutable id")
        return result

    workspace_ids = ids(
        workspace_client.service_principal_secrets_proxy.list(service_principal_id),
        plane="workspace",
    )
    account_ids = ids(
        account_client.service_principal_secrets.list(service_principal_id),
        plane="account",
    )
    if len(workspace_ids) != 1 or account_ids != workspace_ids:
        raise RuntimeError("temporary Lakebase bootstrap secret-plane inventory drifted")
    return workspace_ids[0]


def prove_bootstrap_secret_planes_empty(
    workspace_client: Any,
    account_client: Any,
    *,
    service_principal_id: str,
    attempts: int = 15,
    required_absence: int = 3,
) -> int:
    """Require both credential inventories to stay empty before principal deletion."""

    if attempts < required_absence or required_absence < 3:
        raise ValueError("temporary Lakebase secret-plane observation count is invalid")
    stable_empty = 0
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            workspace_secrets = list(
                workspace_client.service_principal_secrets_proxy.list(service_principal_id)
            )
            account_secrets = list(
                account_client.service_principal_secrets.list(service_principal_id)
            )
            if workspace_secrets or account_secrets:
                raise RuntimeError("temporary Lakebase bootstrap credentials remain active")
            stable_empty += 1
            last_error = None
            if stable_empty >= required_absence:
                return stable_empty
        except Exception as exc:  # noqa: BLE001 - stable exact LIST observations decide
            stable_empty = 0
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(1)
    detail = f"; last_error={type(last_error).__name__}" if last_error else ""
    raise RuntimeError(f"temporary Lakebase secret-plane absence did not converge{detail}")


def prove_bootstrap_principal_absent(
    workspace_client: Any,
    account_client: Any,
    *,
    service_principal_id: str,
    application_id: str,
    attempts: int = 90,
) -> int:
    """Use immutable direct GETs with a deadline large enough for account propagation."""

    # The delegated proof requires a continuous 30-second absence window and
    # polls at two-second intervals. A deadline equal to that window cannot
    # complete because the first clean observation starts the window, so the
    # smallest valid count is 16 (32 seconds).
    if attempts < 16:
        raise ValueError("temporary Lakebase principal absence observation count is invalid")
    from tools.databricks.lakebase_oauth_role_recovery_identity import (
        prove_deleted_bootstrap_principal_absent,
    )

    prove_deleted_bootstrap_principal_absent(
        workspace_client,
        account_client,
        principal_id=service_principal_id,
        application_id=application_id,
        # The direct helper polls both planes every two seconds. Preserve this
        # wrapper's observation-count contract as a real wall-clock deadline.
        deadline_seconds=float(attempts) * 2.0,
    )
    return 3


def _reviewed_m2m_auth_rejection(exc: Exception) -> bool:
    if isinstance(exc, Unauthenticated):
        return True
    error_code = str(getattr(exc, "error_code", "") or "").strip().lower()
    if isinstance(exc, PermissionDenied) and error_code in _AUTH_REJECTION_CODES:
        return True
    if type(exc) is ValueError:
        match = _OAUTH_ERROR_CODE.match(str(exc).strip().lower())
        return bool(match and match.group("code") in _AUTH_REJECTION_CODES)
    return False


def prove_destroyed_m2m_credential_rejected(
    fresh_destroyed_credential_read: Callable[[], Any],
    *,
    positive_control: Callable[[], None],
    required_rejections: int = 3,
    observation_interval_seconds: float = _M2M_REJECTION_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> M2MSecretRejectionProof:
    """Require three spaced, fresh, same-path OAuth credential rejections."""

    if required_rejections != 3:
        raise ValueError("temporary Lakebase M2M rejection count must be exactly three")
    if not 1.0 <= observation_interval_seconds <= 30.0:
        raise ValueError("temporary Lakebase M2M rejection interval is invalid")
    rejections = 0
    while rejections < required_rejections:
        positive_control()
        try:
            fresh_destroyed_credential_read()
        except Exception as exc:  # noqa: BLE001 - fail-closed classification
            if not _reviewed_m2m_auth_rejection(exc):
                raise RuntimeError(
                    "temporary Lakebase destroyed M2M mint failure is inconclusive"
                ) from exc
            positive_control()
            rejections += 1
            if rejections < required_rejections:
                sleep(observation_interval_seconds)
            continue
        positive_control()
        raise RuntimeError("temporary Lakebase destroyed M2M credential remains reusable")
    return M2MSecretRejectionProof(rejection_observations=rejections)


def _sqlstate_class_28(exc: Exception) -> bool:
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    if not sqlstate:
        from pg8000.dbapi import DatabaseError

        if isinstance(exc, DatabaseError) and len(exc.args) == 1:
            fields = exc.args[0]
            if isinstance(fields, dict):
                sqlstate = str(fields.get("C") or "")
    return len(sqlstate) == 5 and sqlstate.startswith("28") and sqlstate.isalnum()


def structured_database_auth_connect(**kwargs: Any) -> Any:
    """Open a TLS probe whose startup errors retain PostgreSQL SQLSTATE fields."""

    from pg8000.dbapi import connect

    expected_keys = {
        "host",
        "port",
        "dbname",
        "user",
        "password",
        "application_name",
        "sslmode",
        "connect_timeout",
        "autocommit",
        "keepalives",
        "keepalives_idle",
        "keepalives_interval",
        "keepalives_count",
    }
    text_keys = ("host", "dbname", "user", "password", "application_name")
    if (
        set(kwargs) != expected_keys
        or any(not isinstance(kwargs[key], str) or not kwargs[key] for key in text_keys)
        or kwargs["port"] != 5432
        or kwargs["sslmode"] != "require"
        or kwargs["connect_timeout"] != 15
        or kwargs["autocommit"] is not True
        or kwargs["keepalives"] != 1
        or kwargs["keepalives_idle"] != 10
        or kwargs["keepalives_interval"] != 5
        or kwargs["keepalives_count"] != 3
    ):
        raise RuntimeError("temporary Lakebase structured auth probe is misconfigured")
    connection = connect(
        host=str(kwargs["host"]),
        port=int(kwargs["port"]),
        database=str(kwargs["dbname"]),
        user=str(kwargs["user"]),
        password=str(kwargs["password"]),
        application_name=str(kwargs["application_name"]),
        timeout=float(kwargs["connect_timeout"]),
        ssl_context=ssl.create_default_context(),
    )
    connection.autocommit = bool(kwargs["autocommit"])
    return connection


def _close_connection(connection: Any) -> None:
    close = getattr(connection, "close", None)
    if callable(close):
        close()


def prove_old_database_token_reuse_rejected(
    connect: Callable[..., Any],
    *,
    lease: DatabaseCredentialLease,
    deployer_cursor: Any,
    retained_backend: BootstrapBackendIdentity,
    expected_executor: str,
    positive_control: Callable[[], None],
    auth_probe_connect: Callable[..., Any] | None = None,
    required_rejections: int = 3,
    uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> OldTokenReuseProof:
    """Bracket old-token reuse with live controls and require SQLSTATE class 28."""

    if required_rejections != 3:
        raise ValueError("temporary Lakebase old-token rejection count must be exactly three")
    rejections = 0
    while rejections < required_rejections:
        probe_id = uuid_factory()
        if not isinstance(probe_id, uuid.UUID):
            raise RuntimeError("temporary Lakebase old-token reuse nonce is invalid")
        probe_application_name = f"{_REUSE_APPLICATION_NAME_PREFIX}{probe_id.hex}"
        assert_exact_bootstrap_backend_inventory(deployer_cursor, backend=retained_backend)
        positive_control()
        connection: Any | None = None
        connect_error: Exception | None = None
        capture_error: Exception | None = None
        reuse_backend: BootstrapBackendIdentity | None = None
        try:
            try:
                connection = _open_bootstrap_backend(
                    auth_probe_connect or connect,
                    lease=lease,
                    application_name=probe_application_name,
                    autocommit=True,
                )
            except Exception as exc:  # noqa: BLE001 - SQLSTATE decides rejection vs ambiguity
                connect_error = exc
            if connection is not None:
                try:
                    probe_cursor = connection.cursor()
                    try:
                        reuse_backend = capture_bootstrap_backend_identity(
                            probe_cursor,
                            application_id=lease.database_user,
                            database_name=lease.database_name,
                            application_name=probe_application_name,
                        )
                    finally:
                        close_cursor = getattr(probe_cursor, "close", None)
                        if callable(close_cursor):
                            close_cursor()
                except Exception as exc:  # noqa: BLE001 - connected reuse is already proven
                    capture_error = exc
        finally:
            if connection is not None:
                _close_connection(connection)
        positive_control()
        if connect_error is not None:
            if not _sqlstate_class_28(connect_error):
                raise RuntimeError(
                    "temporary Lakebase old-token connection failure is inconclusive"
                ) from connect_error
            rejections += 1
            continue
        if capture_error is not None:
            raise RuntimeError(
                "temporary Lakebase old-token reuse identity capture is inconclusive"
            ) from capture_error
        if reuse_backend is None:
            raise RuntimeError("temporary Lakebase old-token reuse identity was not captured")
        drain_captured_bootstrap_backend(
            deployer_cursor,
            backend=reuse_backend,
            expected_executor=expected_executor,
        )
        assert_exact_bootstrap_backend_inventory(deployer_cursor, backend=retained_backend)
        return OldTokenReuseProof(
            outcome=BootstrapAdmissionOutcome.TOKEN_EXPIRY_REQUIRED,
            rejection_observations=rejections,
            reuse_backend=reuse_backend,
        )
    assert_exact_bootstrap_backend_inventory(deployer_cursor, backend=retained_backend)
    return OldTokenReuseProof(
        outcome=BootstrapAdmissionOutcome.ADMITTED,
        rejection_observations=rejections,
    )


def finalize_bootstrap_admission_proof(
    *,
    lease: DatabaseCredentialLease,
    retained_backend: BootstrapBackendIdentity,
    secret_plane_absence_observations: int,
    principal_absence_observations: int,
    m2m_secret_proof: M2MSecretRejectionProof,
    old_token_proof: OldTokenReuseProof,
) -> BootstrapAdmissionProof:
    """Discard the token-bearing lease and return only immutable proof metadata."""

    return BootstrapAdmissionProof(
        outcome=old_token_proof.outcome,
        retained_backend=retained_backend,
        credential_expires_at=lease.expires_at,
        secret_plane_absence_observations=secret_plane_absence_observations,
        principal_absence_observations=principal_absence_observations,
        m2m_rejection_observations=m2m_secret_proof.rejection_observations,
        old_token_rejection_observations=old_token_proof.rejection_observations,
    )


def fence_bootstrap_role_admission(
    client: Any,
    cursor: Any,
    *,
    instance_name: str,
    database_name: str,
    application_id: str,
    display_name: str,
    target_application_id: str,
    external_id: str,
    service_principal_id: str | None,
    allow_absent_managed_event_triggers: bool,
    bootstrap_lock_cursor: Any | None,
    bootstrap_lock_key: Any | None,
    allow_unlocked_recovery_for_tests: bool,
    signed_tombstone_authority: bool = False,
) -> None:
    """Prove a provider-owned role before credential and session fencing."""

    if not assert_bootstrap_admission_identity(
        client,
        cursor,
        instance_name=instance_name,
        application_id=application_id,
        display_name=display_name,
        external_id=external_id,
        service_principal_id=service_principal_id,
        signed_tombstone_authority=signed_tombstone_authority,
    ):
        raise RuntimeError("temporary Lakebase bootstrap role disappeared before fencing")
    if bootstrap_lock_cursor is not None and bootstrap_lock_key is not None:
        assert_bootstrap_lock_held(
            bootstrap_lock_cursor,
            lock_key=bootstrap_lock_key,
        )
    elif not allow_unlocked_recovery_for_tests:
        raise RuntimeError(
            "temporary Lakebase bootstrap admission fence lacks canonical advisory lock"
        )
    profile = read_profile(cursor, application_id)
    if profile is None:
        raise RuntimeError("temporary Lakebase bootstrap role disappeared before session fencing")
