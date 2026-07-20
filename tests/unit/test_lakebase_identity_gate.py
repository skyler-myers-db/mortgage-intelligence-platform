"""Tests for the runtime Lakebase OAuth and replication-denial gate."""

from __future__ import annotations

from typing import Any

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
        replication_error: BaseException | None = None,
    ) -> None:
        self.profile = profile
        self.label = label or [
            ("databricks_auth", f"id={_SCIM_ID},type=service_principal")
        ]
        self.has_membership = has_membership
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
            self._one = (_APP_ID, *self.profile)
        elif "pg_shseclabel" in rendered:
            self._all = list(self.label)
        elif "pg_auth_members" in rendered:
            self._one = (1,) if self.has_membership else None

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
    )


def test_exact_identity_passes_when_replication_startup_is_privilege_denied() -> None:
    normal = _Connection(_Cursor())

    def connect(**kwargs: Any) -> _Connection:
        if kwargs.get("replication") == "database":
            raise _Denied("must be superuser or replication role to start walsender")
        return normal

    _verify(connect)

    assert normal.closed is True


def test_exact_identity_passes_when_identify_system_is_privilege_denied() -> None:
    normal = _Connection(_Cursor())
    replication = _Connection(_Cursor(replication_error=_Denied("permission denied")))

    _verify(
        lambda **kwargs: replication if kwargs.get("replication") == "database" else normal
    )

    assert replication.closed is True


def test_executable_replication_protocol_is_release_blocking() -> None:
    normal = _Connection(_Cursor())
    replication = _Connection(_Cursor())

    with pytest.raises(gate.LakebaseIdentityGateError, match="accepted IDENTIFY_SYSTEM"):
        _verify(
            lambda **kwargs: replication
            if kwargs.get("replication") == "database"
            else normal
        )


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
    normal = _Connection(
        _Cursor(label=[("databricks_auth", "id=wrong,type=service_principal")])
    )

    with pytest.raises(gate.LakebaseIdentityGateError, match="security label mismatch"):
        _verify(lambda **_kwargs: normal)


def test_any_role_relationship_fails_closed() -> None:
    normal = _Connection(_Cursor(has_membership=True))

    with pytest.raises(gate.LakebaseIdentityGateError, match="role relationship"):
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
