"""Security-boundary tests for the one-use bootstrap admission fence."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from databricks.sdk.errors import PermissionDenied, Unauthenticated
from pg8000.dbapi import DatabaseError

from tools.databricks import lakebase_oauth_role_bootstrap_admission as admission
from tools.databricks import lakebase_oauth_role_bootstrap_sessions as sessions

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
APPLICATION_ID = "11111111-1111-4111-8111-111111111111"
REQUEST_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
BACKEND_START = datetime(2026, 7, 22, 12, 0, 5, tzinfo=UTC)


def _lease(*, token: str = "database-token") -> admission.DatabaseCredentialLease:
    return admission.DatabaseCredentialLease(
        host="instance.database.cloud.databricks.com",
        database_name="mip_app_state",
        database_user=APPLICATION_ID,
        application_name="mip-bootstrap-admission-22222222222242228222222222222222",
        request_id=str(REQUEST_ID),
        expires_at=NOW + timedelta(hours=1),
        token=token,
    )


def _backend(
    *,
    pid: int = 101,
    application_name: str | None = None,
) -> sessions.BootstrapBackendIdentity:
    return sessions.BootstrapBackendIdentity(
        pid=pid,
        role_oid=202,
        application_id=APPLICATION_ID,
        database_name="mip_app_state",
        application_name=application_name or _lease().application_name,
        backend_start=BACKEND_START,
        backend_type="client backend",
        client_addr="10.1.2.3",
    )


class _ScriptedCursor:
    def __init__(self, results: list[list[tuple[Any, ...]]]) -> None:
        self.results = list(results)
        self.current: list[tuple[Any, ...]] = []
        self.calls: list[tuple[str, Any]] = []

    def execute(self, statement: str, params: Any = None) -> None:
        self.calls.append((statement, params))
        if not self.results:
            raise AssertionError("unexpected cursor execute")
        self.current = self.results.pop(0)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.current

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.current[0] if self.current else None


def test_database_credential_lease_repr_never_contains_token() -> None:
    lease = _lease(token="never-print-this-database-token")

    assert "never-print-this-database-token" not in repr(lease)
    assert lease.token == "never-print-this-database-token"


def test_mint_database_credential_lease_is_exactly_once_and_bounded_utc() -> None:
    database = MagicMock()
    database.get_database_instance.return_value = SimpleNamespace(
        read_write_dns="instance.database.cloud.databricks.com"
    )
    database.generate_database_credential.return_value = SimpleNamespace(
        token="one-shot-token",
        expiration_time="2026-07-22T13:00:00Z",
    )
    client = SimpleNamespace(database=database)

    lease = admission.mint_database_credential_lease(
        client,
        instance_name="instance",
        database_name="mip_app_state",
        database_user=APPLICATION_ID,
        now=NOW,
        uuid_factory=lambda: REQUEST_ID,
    )

    database.generate_database_credential.assert_called_once_with(
        instance_names=["instance"], request_id=str(REQUEST_ID)
    )
    assert lease.expires_at == NOW + timedelta(hours=1)
    assert lease.application_name.endswith(REQUEST_ID.hex)
    assert len(lease.application_name.encode()) <= 63
    assert "one-shot-token" not in repr(lease)


@pytest.mark.parametrize(
    "expiration",
    [
        "2026-07-22T13:00:00-04:00",
        "2026-07-22T12:00:10Z",
        "2026-07-22T13:06:00Z",
        "not-a-time",
        None,
    ],
)
def test_mint_database_credential_lease_rejects_unbounded_or_non_utc_expiry(
    expiration: str | None,
) -> None:
    database = MagicMock()
    database.get_database_instance.return_value = SimpleNamespace(read_write_dns="host")
    database.generate_database_credential.return_value = SimpleNamespace(
        token="one-shot-token",
        expiration_time=expiration,
    )

    with pytest.raises(RuntimeError, match="expiration"):
        admission.mint_database_credential_lease(
            SimpleNamespace(database=database),
            instance_name="instance",
            database_name="database",
            database_user=APPLICATION_ID,
            now=NOW,
            uuid_factory=lambda: REQUEST_ID,
        )

    database.generate_database_credential.assert_called_once()


def test_open_retained_backend_keeps_token_out_of_returned_metadata() -> None:
    calls: list[dict[str, Any]] = []
    connection = object()

    def connect(**kwargs: Any) -> object:
        calls.append(kwargs)
        return connection

    assert admission.open_retained_bootstrap_backend(connect, lease=_lease()) is connection
    assert calls == [
        {
            "host": "instance.database.cloud.databricks.com",
            "port": 5432,
            "dbname": "mip_app_state",
            "user": APPLICATION_ID,
            "password": "database-token",
            "application_name": _lease().application_name,
            "sslmode": "require",
            "connect_timeout": 15,
            "keepalives": 1,
            "keepalives_idle": 10,
            "keepalives_interval": 5,
            "keepalives_count": 3,
            "autocommit": True,
        }
    ]


def test_capture_backend_binds_pid_oid_users_name_and_utc_start() -> None:
    cursor = _ScriptedCursor(
        [
            [
                (
                    101,
                    202,
                    APPLICATION_ID,
                    "mip_app_state",
                    _lease().application_name,
                    BACKEND_START,
                    "client backend",
                    "10.1.2.3",
                    APPLICATION_ID,
                    APPLICATION_ID,
                )
            ]
        ]
    )

    captured = sessions.capture_bootstrap_backend_identity(
        cursor,
        application_id=APPLICATION_ID,
        database_name="mip_app_state",
        application_name=_lease().application_name,
    )

    assert captured == _backend()
    assert "token" not in repr(captured).lower()


def test_capture_backend_rejects_assumed_or_mismatched_identity() -> None:
    cursor = _ScriptedCursor(
        [
            [
                (
                    101,
                    202,
                    APPLICATION_ID,
                    "mip_app_state",
                    _lease().application_name,
                    BACKEND_START,
                    "client backend",
                    "10.1.2.3",
                    "cloud_admin",
                    APPLICATION_ID,
                )
            ]
        ]
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        sessions.capture_bootstrap_backend_identity(
            cursor,
            application_id=APPLICATION_ID,
            database_name="mip_app_state",
            application_name=_lease().application_name,
        )


@pytest.mark.parametrize(
    ("database_name", "backend_type", "client_addr"),
    [
        ("other_database", "client backend", "10.1.2.3"),
        ("mip_app_state", "parallel worker", "10.1.2.3"),
        ("mip_app_state", "client backend", None),
    ],
)
def test_capture_backend_rejects_database_type_or_client_address_drift(
    database_name: str,
    backend_type: str,
    client_addr: str | None,
) -> None:
    cursor = _ScriptedCursor(
        [
            [
                (
                    101,
                    202,
                    APPLICATION_ID,
                    database_name,
                    _lease().application_name,
                    BACKEND_START,
                    backend_type,
                    client_addr,
                    APPLICATION_ID,
                    APPLICATION_ID,
                )
            ]
        ]
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        sessions.capture_bootstrap_backend_identity(
            cursor,
            application_id=APPLICATION_ID,
            database_name="mip_app_state",
            application_name=_lease().application_name,
        )


def test_deployer_inventory_requires_one_exact_backend_and_role() -> None:
    backend = _backend()
    cursor = _ScriptedCursor(
        [
            [
                (
                    backend.pid,
                    backend.role_oid,
                    backend.application_id,
                    backend.database_name,
                    backend.application_name,
                    backend.backend_start,
                    backend.backend_type,
                    backend.client_addr,
                )
            ],
            [(backend.role_oid, backend.application_id)],
        ]
    )

    sessions.assert_exact_bootstrap_backend_inventory(cursor, backend=backend)


def test_deployer_inventory_rejects_a_second_matching_backend() -> None:
    backend = _backend()
    cursor = _ScriptedCursor(
        [
            [
                (
                    backend.pid,
                    backend.role_oid,
                    backend.application_id,
                    backend.database_name,
                    backend.application_name,
                    backend.backend_start,
                    backend.backend_type,
                    backend.client_addr,
                ),
                (
                    303,
                    backend.role_oid,
                    backend.application_id,
                    backend.database_name,
                    backend.application_name,
                    backend.backend_start,
                    backend.backend_type,
                    backend.client_addr,
                ),
            ]
        ]
    )

    with pytest.raises(RuntimeError, match="inventory drifted"):
        sessions.assert_exact_bootstrap_backend_inventory(cursor, backend=backend)


@pytest.mark.parametrize("drift_index", [3, 6, 7])
def test_deployer_inventory_rejects_database_type_or_client_address_drift(
    drift_index: int,
) -> None:
    backend = _backend()
    row = [
        backend.pid,
        backend.role_oid,
        backend.application_id,
        backend.database_name,
        backend.application_name,
        backend.backend_start,
        backend.backend_type,
        backend.client_addr,
    ]
    row[drift_index] = "drifted"
    cursor = _ScriptedCursor([[tuple(row)]])

    with pytest.raises(RuntimeError, match="inventory drifted"):
        sessions.assert_exact_bootstrap_backend_inventory(cursor, backend=backend)


def test_captured_reuse_backend_is_terminated_then_stably_absent(monkeypatch: Any) -> None:
    backend = _backend(pid=303)
    cursor = _ScriptedCursor(
        [
            [("deployer", "deployer")],
            [
                (
                    backend.pid,
                    backend.role_oid,
                    backend.application_id,
                    backend.database_name,
                    backend.application_name,
                    backend.backend_start,
                    backend.backend_type,
                    backend.client_addr,
                )
            ],
            [(True,)],
            [("deployer", "deployer")],
            [],
            [("deployer", "deployer")],
            [],
            [("deployer", "deployer")],
            [],
        ]
    )
    monkeypatch.setattr(sessions.time, "sleep", lambda _seconds: None)

    sessions.drain_captured_bootstrap_backend(
        cursor,
        backend=backend,
        expected_executor="deployer",
        attempts=4,
    )

    terminate_calls = [call for call in cursor.calls if "pg_terminate_backend" in call[0]]
    assert terminate_calls == [("SELECT pg_terminate_backend(%s)", (backend.pid,))]


def test_captured_reuse_backend_drain_rejects_pid_identity_drift() -> None:
    backend = _backend(pid=303)
    cursor = _ScriptedCursor(
        [
            [("deployer", "deployer")],
            [
                (
                    backend.pid,
                    999,
                    "different-role",
                    backend.database_name,
                    backend.application_name,
                    backend.backend_start,
                    backend.backend_type,
                    backend.client_addr,
                )
            ],
        ]
    )

    with pytest.raises(RuntimeError, match="identity drifted"):
        sessions.drain_captured_bootstrap_backend(
            cursor,
            backend=backend,
            expected_executor="deployer",
            attempts=3,
        )


def test_secret_planes_must_each_contain_the_same_singleton_id() -> None:
    workspace = SimpleNamespace(
        service_principal_secrets_proxy=SimpleNamespace(
            list=lambda _principal_id: [SimpleNamespace(id="secret-id")]
        )
    )
    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            list=lambda _principal_id: [SimpleNamespace(id="secret-id")]
        )
    )

    assert (
        admission.assert_singleton_bootstrap_secret_planes(
            workspace, account, service_principal_id="12345"
        )
        == "secret-id"
    )


@pytest.mark.parametrize(
    ("workspace_ids", "account_ids"),
    [([], ["secret-id"]), (["secret-id"], []), (["a"], ["b"]), (["a", "b"], ["a", "b"])],
)
def test_secret_plane_singleton_rejects_omission_divergence_and_duplicates(
    workspace_ids: list[str],
    account_ids: list[str],
) -> None:
    workspace = SimpleNamespace(
        service_principal_secrets_proxy=SimpleNamespace(
            list=lambda _principal_id: [SimpleNamespace(id=value) for value in workspace_ids]
        )
    )
    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            list=lambda _principal_id: [SimpleNamespace(id=value) for value in account_ids]
        )
    )

    with pytest.raises(RuntimeError, match="inventory drifted"):
        admission.assert_singleton_bootstrap_secret_planes(
            workspace, account, service_principal_id="12345"
        )


def test_both_secret_planes_require_three_stable_empty_observations(monkeypatch: Any) -> None:
    workspace_list = MagicMock(side_effect=[[], [], []])
    account_list = MagicMock(side_effect=[[], [], []])
    monkeypatch.setattr(admission.time, "sleep", lambda _seconds: None)

    observations = admission.prove_bootstrap_secret_planes_empty(
        SimpleNamespace(service_principal_secrets_proxy=SimpleNamespace(list=workspace_list)),
        SimpleNamespace(service_principal_secrets=SimpleNamespace(list=account_list)),
        service_principal_id="12345",
        attempts=3,
    )

    assert observations == 3
    assert workspace_list.call_count == account_list.call_count == 3


def test_secret_plane_error_is_not_treated_as_empty(monkeypatch: Any) -> None:
    monkeypatch.setattr(admission.time, "sleep", lambda _seconds: None)
    workspace_list = MagicMock(side_effect=OSError("network down"))

    with pytest.raises(RuntimeError, match="did not converge; last_error=OSError"):
        admission.prove_bootstrap_secret_planes_empty(
            SimpleNamespace(service_principal_secrets_proxy=SimpleNamespace(list=workspace_list)),
            SimpleNamespace(
                service_principal_secrets=SimpleNamespace(list=MagicMock(return_value=[]))
            ),
            service_principal_id="12345",
            attempts=3,
        )


def test_principal_absence_delegates_to_direct_two_plane_helper_with_long_deadline(
    monkeypatch: Any,
) -> None:
    from tools.databricks import lakebase_oauth_role_recovery_identity as recovery_identity

    helper = MagicMock()
    monkeypatch.setattr(recovery_identity, "prove_deleted_bootstrap_principal_absent", helper)
    workspace = object()
    account = object()

    assert (
        admission.prove_bootstrap_principal_absent(
            workspace,
            account,
            service_principal_id="12345",
            application_id=APPLICATION_ID,
        )
        == 3
    )
    helper.assert_called_once_with(
        workspace,
        account,
        principal_id="12345",
        application_id=APPLICATION_ID,
        deadline_seconds=180.0,
    )


@pytest.mark.parametrize(
    "error",
    [
        Unauthenticated("expired"),
        PermissionDenied("denied", error_code="invalid_client"),
        ValueError("invalid_client: Client authentication failed"),
        ValueError("unauthorized_client: client disabled"),
    ],
)
def test_destroyed_m2m_mint_accepts_only_spaced_control_bracketed_auth_rejections(
    error: Exception,
) -> None:
    controls: list[str] = []
    pauses: list[float] = []

    def mint() -> None:
        raise error

    proof = admission.prove_destroyed_m2m_credential_rejected(
        mint,
        positive_control=lambda: controls.append("healthy"),
        sleep=pauses.append,
    )

    assert proof == admission.M2MSecretRejectionProof(rejection_observations=3)
    assert controls == ["healthy"] * 6
    assert pauses == [5.0, 5.0]


def test_capture_cached_m2m_expiry_uses_only_bounded_jwt_metadata() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    expires_at = datetime(2026, 7, 22, 13, 0, tzinfo=UTC)
    token = SimpleNamespace(jwt_claims=lambda: {"exp": expires_at.timestamp()})
    client = SimpleNamespace(
        config=SimpleNamespace(auth_type="oauth-m2m", oauth_token=lambda: token)
    )

    assert admission.capture_cached_m2m_access_token_expiry(client, now=now) == expires_at


@pytest.mark.parametrize(
    "client",
    [
        SimpleNamespace(config=SimpleNamespace(auth_type="pat")),
        SimpleNamespace(
            config=SimpleNamespace(
                auth_type="oauth-m2m",
                oauth_token=lambda: SimpleNamespace(jwt_claims=lambda: {}),
            )
        ),
    ],
)
def test_capture_cached_m2m_expiry_fails_closed_on_unbounded_metadata(client: Any) -> None:
    with pytest.raises(RuntimeError):
        admission.capture_cached_m2m_access_token_expiry(
            client,
            now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "result",
    [
        None,
        "still-valid-token",
    ],
)
def test_destroyed_m2m_mint_rejects_any_success(result: Any) -> None:
    with pytest.raises(RuntimeError, match="remains reusable"):
        admission.prove_destroyed_m2m_credential_rejected(
            lambda: result,
            positive_control=lambda: None,
            sleep=lambda _seconds: None,
        )


@pytest.mark.parametrize(
    "error",
    [
        OSError("network down"),
        PermissionDenied("scope denied", error_code="PERMISSION_DENIED"),
        ValueError("connection reset"),
        ValueError("server_error: unavailable"),
    ],
)
def test_destroyed_m2m_mint_does_not_confuse_generic_failure_with_revocation(
    error: Exception,
) -> None:
    def mint() -> None:
        raise error

    with pytest.raises(RuntimeError, match="inconclusive"):
        admission.prove_destroyed_m2m_credential_rejected(
            mint,
            positive_control=lambda: None,
            sleep=lambda _seconds: None,
        )


def test_destroyed_m2m_rejection_never_outlives_a_failed_positive_control() -> None:
    calls = 0

    def control() -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("workspace control unavailable")

    with pytest.raises(OSError, match="control unavailable"):
        admission.prove_destroyed_m2m_credential_rejected(
            lambda: (_ for _ in ()).throw(Unauthenticated("expired")),
            positive_control=control,
            sleep=lambda _seconds: None,
        )


@pytest.mark.parametrize("interval", [0.0, 0.5, 31.0])
def test_destroyed_m2m_rejection_requires_a_bounded_stability_interval(
    interval: float,
) -> None:
    with pytest.raises(ValueError, match="interval"):
        admission.prove_destroyed_m2m_credential_rejected(
            lambda: None,
            positive_control=lambda: None,
            observation_interval_seconds=interval,
        )


class _SqlStateError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("sanitized connection failure")
        self.sqlstate = sqlstate


def test_structured_pg8000_class_28_is_accepted_as_auth_rejection(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(admission, "assert_exact_bootstrap_backend_inventory", MagicMock())

    def connect(**_kwargs: Any) -> Any:
        raise DatabaseError({"S": "ERROR", "C": "28P01", "M": "sanitized"})

    proof = admission.prove_old_database_token_reuse_rejected(
        lambda **_kwargs: None,
        auth_probe_connect=connect,
        lease=_lease(),
        deployer_cursor=object(),
        retained_backend=_backend(),
        expected_executor="deployer",
        positive_control=lambda: None,
        uuid_factory=iter(
            [
                uuid.UUID("30000000-0000-4000-8000-000000000001"),
                uuid.UUID("30000000-0000-4000-8000-000000000002"),
                uuid.UUID("30000000-0000-4000-8000-000000000003"),
            ]
        ).__next__,
    )

    assert proof.outcome is admission.BootstrapAdmissionOutcome.ADMITTED
    assert proof.rejection_observations == 3


def test_structured_pg8000_non_auth_failure_remains_inconclusive(monkeypatch: Any) -> None:
    monkeypatch.setattr(admission, "assert_exact_bootstrap_backend_inventory", MagicMock())

    def connect(**_kwargs: Any) -> Any:
        raise DatabaseError({"S": "ERROR", "C": "08006", "M": "sanitized"})

    with pytest.raises(RuntimeError, match="inconclusive"):
        admission.prove_old_database_token_reuse_rejected(
            lambda **_kwargs: None,
            auth_probe_connect=connect,
            lease=_lease(),
            deployer_cursor=object(),
            retained_backend=_backend(),
            expected_executor="deployer",
            positive_control=lambda: None,
            uuid_factory=lambda: REQUEST_ID,
        )


def test_structured_database_auth_connect_requires_exact_probe_contract(
    monkeypatch: Any,
) -> None:
    connect = MagicMock(return_value=SimpleNamespace(autocommit=False))
    monkeypatch.setattr("pg8000.dbapi.connect", connect)
    kwargs = {
        "host": "instance.database.cloud.databricks.com",
        "port": 5432,
        "dbname": "mip_app_state",
        "user": APPLICATION_ID,
        "password": "database-token",
        "application_name": _lease().application_name,
        "sslmode": "require",
        "connect_timeout": 15,
        "keepalives": 1,
        "keepalives_idle": 10,
        "keepalives_interval": 5,
        "keepalives_count": 3,
        "autocommit": True,
    }

    connection = admission.structured_database_auth_connect(**kwargs)

    assert connection.autocommit is True
    connect.assert_called_once()
    call = connect.call_args.kwargs
    assert call["port"] == 5432
    assert call["timeout"] == 15.0
    assert isinstance(call["ssl_context"], __import__("ssl").SSLContext)
    assert "database-token" not in repr(connection)

    for key, invalid in (
        ("host", ""),
        ("port", 5433),
        ("sslmode", "disable"),
        ("connect_timeout", 30),
        ("keepalives_count", 4),
        ("autocommit", False),
    ):
        mutated = {**kwargs, key: invalid}
        with pytest.raises(RuntimeError, match="misconfigured"):
            admission.structured_database_auth_connect(**mutated)
    with pytest.raises(RuntimeError, match="misconfigured"):
        admission.structured_database_auth_connect(**{**kwargs, "extra": True})


def test_old_token_reuse_requires_three_positive_control_bracketed_class_28_rejections(
    monkeypatch: Any,
) -> None:
    controls: list[str] = []
    inventory = MagicMock()
    names: list[str] = []
    monkeypatch.setattr(admission, "assert_exact_bootstrap_backend_inventory", inventory)

    def connect(**kwargs: Any) -> Any:
        names.append(kwargs["application_name"])
        raise _SqlStateError("28P01")

    proof = admission.prove_old_database_token_reuse_rejected(
        connect,
        lease=_lease(),
        deployer_cursor=object(),
        retained_backend=_backend(),
        expected_executor="deployer",
        positive_control=lambda: controls.append("ok"),
        uuid_factory=iter(
            [
                uuid.UUID("30000000-0000-4000-8000-000000000001"),
                uuid.UUID("30000000-0000-4000-8000-000000000002"),
                uuid.UUID("30000000-0000-4000-8000-000000000003"),
            ]
        ).__next__,
    )

    assert proof == admission.OldTokenReuseProof(
        outcome=admission.BootstrapAdmissionOutcome.ADMITTED,
        rejection_observations=3,
    )
    assert len(controls) == 6
    assert len(set(names)) == 3
    assert all(name.startswith("mip-bootstrap-reuse-") for name in names)
    assert inventory.call_count == 4


@pytest.mark.parametrize("error", [OSError("network down"), _SqlStateError("08006")])
def test_old_token_generic_or_non_auth_failure_is_inconclusive(
    monkeypatch: Any,
    error: Exception,
) -> None:
    controls: list[str] = []
    monkeypatch.setattr(admission, "assert_exact_bootstrap_backend_inventory", MagicMock())

    def connect(**_kwargs: Any) -> Any:
        raise error

    with pytest.raises(RuntimeError, match="inconclusive"):
        admission.prove_old_database_token_reuse_rejected(
            connect,
            lease=_lease(),
            deployer_cursor=object(),
            retained_backend=_backend(),
            expected_executor="deployer",
            positive_control=lambda: controls.append("ok"),
            uuid_factory=lambda: REQUEST_ID,
        )

    assert controls == ["ok", "ok"]


def test_successful_old_token_reuse_is_closed_captured_drained_and_requires_expiry(
    monkeypatch: Any,
) -> None:
    probe_backend = _backend(
        pid=303,
        application_name="mip-bootstrap-reuse-22222222222242228222222222222222",
    )
    probe_cursor = SimpleNamespace(close=MagicMock())
    connection = SimpleNamespace(cursor=lambda: probe_cursor, close=MagicMock())
    capture = MagicMock(return_value=probe_backend)
    drain = MagicMock()
    inventory = MagicMock()
    monkeypatch.setattr(admission, "capture_bootstrap_backend_identity", capture)
    monkeypatch.setattr(admission, "drain_captured_bootstrap_backend", drain)
    monkeypatch.setattr(admission, "assert_exact_bootstrap_backend_inventory", inventory)
    controls: list[str] = []

    proof = admission.prove_old_database_token_reuse_rejected(
        lambda **_kwargs: connection,
        lease=_lease(),
        deployer_cursor="deployer-cursor",
        retained_backend=_backend(),
        expected_executor="deployer",
        positive_control=lambda: controls.append("ok"),
        uuid_factory=lambda: REQUEST_ID,
    )

    assert proof.outcome is admission.BootstrapAdmissionOutcome.TOKEN_EXPIRY_REQUIRED
    assert proof.reuse_backend == probe_backend
    assert proof.rejection_observations == 0
    connection.close.assert_called_once_with()
    probe_cursor.close.assert_called_once_with()
    capture.assert_called_once_with(
        probe_cursor,
        application_id=APPLICATION_ID,
        database_name="mip_app_state",
        application_name="mip-bootstrap-reuse-22222222222242228222222222222222",
    )
    drain.assert_called_once_with(
        "deployer-cursor", backend=probe_backend, expected_executor="deployer"
    )
    assert controls == ["ok", "ok"]
    assert inventory.call_count == 2


def test_connected_old_token_capture_failure_is_never_counted_as_auth_rejection(
    monkeypatch: Any,
) -> None:
    probe_cursor = SimpleNamespace(close=MagicMock())
    connection = SimpleNamespace(cursor=lambda: probe_cursor, close=MagicMock())
    monkeypatch.setattr(admission, "assert_exact_bootstrap_backend_inventory", MagicMock())
    monkeypatch.setattr(
        admission,
        "capture_bootstrap_backend_identity",
        MagicMock(side_effect=_SqlStateError("28P01")),
    )
    controls: list[str] = []

    with pytest.raises(RuntimeError, match="identity capture is inconclusive"):
        admission.prove_old_database_token_reuse_rejected(
            lambda **_kwargs: connection,
            lease=_lease(),
            deployer_cursor=object(),
            retained_backend=_backend(),
            expected_executor="deployer",
            positive_control=lambda: controls.append("ok"),
            uuid_factory=lambda: REQUEST_ID,
        )

    assert controls == ["ok", "ok"]
    connection.close.assert_called_once_with()
    probe_cursor.close.assert_called_once_with()


def test_final_admission_proof_is_secret_free_and_only_admitted_result_is_ready() -> None:
    admitted = admission.finalize_bootstrap_admission_proof(
        lease=_lease(token="never-print-final-token"),
        retained_backend=_backend(),
        secret_plane_absence_observations=3,
        principal_absence_observations=3,
        m2m_secret_proof=admission.M2MSecretRejectionProof(rejection_observations=3),
        old_token_proof=admission.OldTokenReuseProof(
            outcome=admission.BootstrapAdmissionOutcome.ADMITTED,
            rejection_observations=3,
        ),
    )
    expiry_required = admission.finalize_bootstrap_admission_proof(
        lease=_lease(token="never-print-final-token"),
        retained_backend=_backend(),
        secret_plane_absence_observations=3,
        principal_absence_observations=3,
        m2m_secret_proof=admission.M2MSecretRejectionProof(rejection_observations=3),
        old_token_proof=admission.OldTokenReuseProof(
            outcome=admission.BootstrapAdmissionOutcome.TOKEN_EXPIRY_REQUIRED,
            rejection_observations=1,
            reuse_backend=_backend(pid=303),
        ),
    )

    assert admitted.ready_for_provider_invocation is True
    assert expiry_required.ready_for_provider_invocation is False
    assert "never-print-final-token" not in repr(admitted)
    assert "token" not in admitted.__dict__
    assert set(admitted.__dict__) == {
        "outcome",
        "retained_backend",
        "credential_expires_at",
        "secret_plane_absence_observations",
        "principal_absence_observations",
        "m2m_rejection_observations",
        "old_token_rejection_observations",
    }


def test_admission_module_contains_no_provider_invocation() -> None:
    source = inspect.getsource(admission)

    assert "databricks_create_role" not in source
    assert "create_target_role" not in source
