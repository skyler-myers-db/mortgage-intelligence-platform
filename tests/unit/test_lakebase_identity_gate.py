"""Tests for the runtime Lakebase OAuth and replication-denial gate."""

from __future__ import annotations

import ssl
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services import lakebase_identity_gate as gate

_APP_ID = "app-service-principal"
_SCIM_ID = "app-service-principal-scim-id"


class _Denied(RuntimeError):
    sqlstate = "42501"


class _Cursor:
    def __init__(
        self,
        *,
        profile: tuple[bool, ...] = gate.SAFE_OAUTH_PROFILE,
        label: list[tuple[str, str]] | None = None,
        has_membership: bool = False,
        public_schema_privileges: tuple[bool, bool] | None = (False, False),
        settings: tuple[Any, ...] = (-1, None, "********", None),
        database_settings: list[tuple[Any, ...]] | None = None,
        session_user: str = _APP_ID,
        replication_error: BaseException | None = None,
    ) -> None:
        self.profile = profile
        self.label = label or [("databricks_auth", f"id={_SCIM_ID},type=service_principal")]
        self.has_membership = has_membership
        self.public_schema_privileges = public_schema_privileges
        self.settings = settings
        self.database_settings = database_settings or []
        self.session_user = session_user
        self.replication_error = replication_error
        self._one: tuple[Any, ...] | None = None
        self._all: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str) -> None:
        rendered = " ".join(query.split())
        self._one = None
        self._all = []
        if rendered == "IDENTIFY_SYSTEM":
            if self.replication_error is not None:
                raise self.replication_error
            self._one = ("system-id", 1, "0/1", "mip_app_state")
        elif "rolreplication" in rendered:
            self._one = (_APP_ID, self.session_user, _APP_ID, *self.profile)
        elif "rolconnlimit" in rendered:
            self._one = self.settings
        elif "pg_db_role_setting" in rendered:
            self._all = list(self.database_settings)
        elif "pg_shseclabel" in rendered:
            self._all = list(self.label)
        elif "pg_auth_members" in rendered:
            self._one = (1,) if self.has_membership else None
        elif "has_schema_privilege" in rendered:
            self._one = self.public_schema_privileges

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._all


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.closed = False

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _verify(connect: Any) -> None:
    gate.verify_lakebase_oauth_identity(
        host="reviewed.database.example",
        port=5432,
        database="mip_app_state",
        user=_APP_ID,
        password="short-lived-token",
        sslmode="require",
        expected_application_id=_APP_ID,
        expected_service_principal_id=_SCIM_ID,
        connect=connect,
        replication_connect=connect,
    )


def test_exact_identity_passes_when_replication_startup_is_privilege_denied() -> None:
    normal = _Connection(_Cursor())

    def connect(**kwargs: Any) -> _Connection:
        if kwargs.get("replication") == "database":
            raise _Denied("must be superuser or replication role to start walsender")
        return normal

    _verify(connect)

    assert normal.closed is True


def test_exact_identity_accepts_structured_pg8000_replication_denial() -> None:
    normal = _Connection(_Cursor())

    def connect(**kwargs: Any) -> _Connection:
        if kwargs.get("replication") == "database":
            raise gate.Pg8000DatabaseError(
                {"S": "ERROR", "C": "42501", "M": "sanitized"}
            )
        return normal

    _verify(connect)


def test_structured_replication_connect_requires_exact_tls_contract(
    monkeypatch: Any,
) -> None:
    connection = SimpleNamespace()
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(gate, "pg8000_connect", connect)
    kwargs = {
        "host": "reviewed.database.example",
        "port": 5432,
        "dbname": "mip_app_state",
        "user": _APP_ID,
        "password": "short-lived-token",
        "sslmode": "require",
        "connect_timeout": 15,
        "replication": "database",
    }

    assert gate._structured_replication_connect(**kwargs) is connection
    call = connect.call_args.kwargs
    assert call["replication"] == "database"
    assert call["timeout"] == 15.0
    assert isinstance(call["ssl_context"], ssl.SSLContext)

    for key, invalid in (
        ("host", ""),
        ("port", 5433),
        ("sslmode", "disable"),
        ("connect_timeout", 30),
        ("replication", "true"),
    ):
        with pytest.raises(gate.LakebaseIdentityGateError, match="misconfigured"):
            gate._structured_replication_connect(**{**kwargs, key: invalid})


def test_exact_identity_passes_when_identify_system_is_privilege_denied() -> None:
    normal = _Connection(_Cursor())
    replication = _Connection(_Cursor(replication_error=_Denied("permission denied")))

    _verify(lambda **kwargs: replication if kwargs.get("replication") == "database" else normal)

    assert replication.closed is True


def test_executable_replication_protocol_is_release_blocking() -> None:
    normal = _Connection(_Cursor())
    replication = _Connection(_Cursor())

    with pytest.raises(gate.LakebaseIdentityGateError, match="accepted IDENTIFY_SYSTEM"):
        _verify(lambda **kwargs: replication if kwargs.get("replication") == "database" else normal)


def test_replication_transport_failure_is_inconclusive_not_a_pass() -> None:
    normal = _Connection(_Cursor())

    def connect(**kwargs: Any) -> _Connection:
        if kwargs.get("replication") == "database":
            raise TimeoutError("network timeout")
        return normal

    with pytest.raises(gate.LakebaseIdentityGateError, match="inconclusive"):
        _verify(connect)


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(13, "Permission denied"),
        RuntimeError("proxy permission denied by firewall"),
        RuntimeError("insufficient privilege at network edge"),
        gate.psycopg.OperationalError("connection failed: Permission denied"),
        gate.psycopg.Error("permission denied without a server SQLSTATE"),
    ],
)
def test_non_postgres_permission_text_is_inconclusive(error: BaseException) -> None:
    normal = _Connection(_Cursor())

    def connect(**kwargs: Any) -> _Connection:
        if kwargs.get("replication") == "database":
            raise error
        return normal

    with pytest.raises(gate.LakebaseIdentityGateError, match="inconclusive"):
        _verify(connect)


def test_replication_attribute_drift_fails_before_protocol_probe() -> None:
    profile = list(gate.SAFE_OAUTH_PROFILE)
    profile[3] = True
    calls = 0

    def connect(**_kwargs: Any) -> _Connection:
        nonlocal calls
        calls += 1
        return _Connection(_Cursor(profile=tuple(profile)))

    with pytest.raises(gate.LakebaseIdentityGateError, match="unsafe attributes"):
        _verify(connect)

    assert calls == 1


def test_oauth_security_label_drift_fails_closed() -> None:
    normal = _Connection(_Cursor(label=[("databricks_auth", "id=wrong,type=service_principal")]))

    with pytest.raises(gate.LakebaseIdentityGateError, match="security label mismatch"):
        _verify(lambda **_kwargs: normal)


def test_runtime_session_user_must_match_current_user() -> None:
    normal = _Connection(_Cursor(session_user="mapped-session-identity"))

    with pytest.raises(gate.LakebaseIdentityGateError, match="current_user identity mismatch"):
        _verify(lambda **_kwargs: normal)


@pytest.mark.parametrize(
    "settings",
    [
        (1, None, "********", None),
        (-1, "2027-01-01", "********", None),
        (-1, None, "unexpected", None),
        (-1, None, "********", ["search_path=public"]),
    ],
)
def test_any_runtime_role_setting_drift_fails_closed(
    settings: tuple[Any, ...],
) -> None:
    normal = _Connection(_Cursor(settings=settings))

    with pytest.raises(gate.LakebaseIdentityGateError, match="unsafe settings"):
        _verify(lambda **_kwargs: normal)


def test_runtime_database_scoped_setting_fails_closed() -> None:
    normal = _Connection(_Cursor(database_settings=[(42, 5102, ["statement_timeout=0"])]))

    with pytest.raises(gate.LakebaseIdentityGateError, match="database-scoped settings"):
        _verify(lambda **_kwargs: normal)


def test_any_role_relationship_fails_closed() -> None:
    normal = _Connection(_Cursor(has_membership=True))

    with pytest.raises(gate.LakebaseIdentityGateError, match="role relationship"):
        _verify(lambda **_kwargs: normal)


@pytest.mark.parametrize(
    "public_schema_privileges",
    [
        pytest.param((True, False), id="usage"),
        pytest.param((False, True), id="create"),
        pytest.param((True, True), id="usage-and-create"),
    ],
)
def test_any_public_schema_privilege_fails_closed(
    public_schema_privileges: tuple[bool, bool],
) -> None:
    normal = _Connection(_Cursor(public_schema_privileges=public_schema_privileges))

    with pytest.raises(gate.LakebaseIdentityGateError, match="public schema privileges"):
        _verify(lambda **_kwargs: normal)


def test_missing_public_schema_privilege_proof_fails_closed() -> None:
    normal = _Connection(_Cursor(public_schema_privileges=None))

    with pytest.raises(gate.LakebaseIdentityGateError, match="public schema privileges"):
        _verify(lambda **_kwargs: normal)


def test_database_user_must_equal_expected_application_id() -> None:
    with pytest.raises(gate.LakebaseIdentityGateError, match="database user"):
        gate.verify_lakebase_oauth_identity(
            host="reviewed.database.example",
            port=5432,
            database="mip_app_state",
            user="wrong-user",
            password="short-lived-token",
            sslmode="require",
            expected_application_id=_APP_ID,
            expected_service_principal_id=_SCIM_ID,
            connect=lambda **_kwargs: _Connection(_Cursor()),
        )
