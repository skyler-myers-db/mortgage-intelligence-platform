"""OID-bound backend fencing for one-use Lakebase bootstrap identities."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class SessionFence:
    role_oid: int | None
    observed_pids: frozenset[int]
    executor: str


@dataclass(frozen=True)
class BootstrapBackendIdentity:
    """Immutable, secret-free identity for one retained bootstrap backend."""

    pid: int
    role_oid: int
    application_id: str
    database_name: str
    application_name: str
    backend_start: datetime
    backend_type: str
    client_addr: str

    def __post_init__(self) -> None:
        if self.pid <= 0 or self.role_oid <= 0:
            raise ValueError("temporary Lakebase bootstrap backend identity is incomplete")
        if not all(
            (
                self.application_id.strip(),
                self.database_name.strip(),
                self.application_name.strip(),
                self.client_addr.strip(),
            )
        ):
            raise ValueError("temporary Lakebase bootstrap backend identity is incomplete")
        if self.backend_type != "client backend":
            raise ValueError("temporary Lakebase bootstrap backend type is invalid")
        if self.backend_start.tzinfo is None or self.backend_start.utcoffset() is None:
            raise ValueError("temporary Lakebase bootstrap backend start is not UTC")
        if self.backend_start.utcoffset().total_seconds() != 0:
            raise ValueError("temporary Lakebase bootstrap backend start is not UTC")
        object.__setattr__(self, "backend_start", self.backend_start.astimezone(UTC))
        object.__setattr__(self, "client_addr", self.client_addr.strip())


def capture_bootstrap_backend_identity(
    cursor: Any,
    *,
    application_id: str,
    database_name: str,
    application_name: str,
) -> BootstrapBackendIdentity:
    """Capture every stable identity dimension of the current bootstrap backend."""

    if not application_id or not database_name or not application_name:
        raise RuntimeError("temporary Lakebase bootstrap backend expectation is incomplete")
    cursor.execute(
        """
        SELECT activity.pid,
               activity.usesysid,
               activity.usename,
               activity.datname,
               activity.application_name,
               activity.backend_start,
               activity.backend_type,
               activity.client_addr::text,
               current_user,
               session_user
        FROM pg_stat_activity activity
        WHERE activity.pid = pg_backend_pid()
        """
    )
    rows = list(cursor.fetchall())
    if len(rows) != 1:
        raise RuntimeError("temporary Lakebase bootstrap backend inventory is ambiguous")
    (
        pid_raw,
        oid_raw,
        user_raw,
        database_raw,
        name_raw,
        started_raw,
        backend_type_raw,
        client_addr_raw,
        current_raw,
        session_raw,
    ) = rows[0]
    exact_identity = (application_id, application_id, application_id)
    if (
        (str(user_raw or ""), str(current_raw or ""), str(session_raw or "")) != exact_identity
        or str(database_raw or "") != database_name
        or str(name_raw or "") != application_name
        or str(backend_type_raw or "") != "client backend"
        or not str(client_addr_raw or "").strip()
        or not isinstance(started_raw, datetime)
    ):
        raise RuntimeError("temporary Lakebase bootstrap backend identity mismatch")
    try:
        return BootstrapBackendIdentity(
            pid=int(pid_raw),
            role_oid=int(oid_raw),
            application_id=application_id,
            database_name=database_name,
            application_name=application_name,
            backend_start=started_raw,
            backend_type="client backend",
            client_addr=str(client_addr_raw).strip(),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("temporary Lakebase bootstrap backend identity is incomplete") from exc


def assert_exact_bootstrap_backend_inventory(
    cursor: Any,
    *,
    backend: BootstrapBackendIdentity,
) -> None:
    """Require exactly the captured backend under every deployer-visible key."""

    cursor.execute(
        """
        SELECT activity.pid,
               activity.usesysid,
               activity.usename,
               activity.datname,
               activity.application_name,
               activity.backend_start,
               activity.backend_type,
               activity.client_addr::text
        FROM pg_stat_activity activity
        WHERE activity.pid = %s
           OR activity.usesysid = %s
           OR activity.usename = %s
           OR activity.application_name = %s
        ORDER BY activity.pid
        """,
        (
            backend.pid,
            backend.role_oid,
            backend.application_id,
            backend.application_name,
        ),
    )
    rows = list(cursor.fetchall())
    expected = [
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
    ]
    if rows != expected:
        raise RuntimeError("temporary Lakebase bootstrap retained backend inventory drifted")
    cursor.execute(
        "SELECT oid, rolname FROM pg_roles WHERE oid = %s OR rolname = %s ORDER BY oid",
        (backend.role_oid, backend.application_id),
    )
    if list(cursor.fetchall()) != [(backend.role_oid, backend.application_id)]:
        raise RuntimeError("temporary Lakebase bootstrap retained role identity drifted")


def drain_captured_bootstrap_backend(
    cursor: Any,
    *,
    backend: BootstrapBackendIdentity,
    expected_executor: str,
    attempts: int = 15,
    required_absence: int = 3,
) -> None:
    """Terminate one captured reuse probe and prove stable PID/OID/name absence."""

    if attempts < required_absence or required_absence < 1:
        raise ValueError("temporary Lakebase bootstrap drain observation count is invalid")
    absence = 0
    termination_errors: list[str] = []
    for attempt in range(attempts):
        cleanup_executor_identity(
            cursor,
            excluded_application_id=backend.application_id,
            expected_executor=expected_executor,
        )
        cursor.execute(
            """
            SELECT pid,
                   usesysid,
                   usename,
                   datname,
                   application_name,
                   backend_start,
                   backend_type,
                   client_addr::text
            FROM pg_stat_activity
            WHERE pid = %s
            ORDER BY pid
            """,
            (backend.pid,),
        )
        rows = list(cursor.fetchall())
        if rows:
            expected = [
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
            ]
            if rows != expected:
                raise RuntimeError("temporary Lakebase bootstrap reuse backend identity drifted")
            absence = 0
            try:
                cursor.execute("SELECT pg_terminate_backend(%s)", (backend.pid,))
                if cursor.fetchone() != (True,):
                    termination_errors.append(f"pid={backend.pid}: returned false")
            except Exception as exc:  # noqa: BLE001 - later stable absence decides
                termination_errors.append(f"pid={backend.pid}: {type(exc).__name__}: {exc}")
        else:
            absence += 1
            if absence >= required_absence:
                if termination_errors:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap reuse backend termination was ambiguous: "
                        f"{termination_errors!r}"
                    )
                return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("temporary Lakebase bootstrap reuse backend did not drain")


def cleanup_executor_identity(
    cursor: Any,
    *,
    excluded_application_id: str,
    expected_executor: str | None = None,
) -> str:
    """Require an unassumed deployer with identical current/session identity."""

    cursor.execute("SELECT current_user, session_user")
    row = cursor.fetchone()
    identities = tuple(str(value or "") for value in row or ())
    if (
        len(identities) != 2
        or not identities[0]
        or identities[0] != identities[1]
        or identities[0] == excluded_application_id
        or (expected_executor is not None and identities[0] != expected_executor)
    ):
        raise RuntimeError(
            "temporary Lakebase bootstrap session cleanup executor identity is unsafe"
        )
    return identities[0]


def _role_oid(cursor: Any, application_id: str) -> int | None:
    cursor.execute("SELECT oid FROM pg_roles WHERE rolname = %s", (application_id,))
    rows = list(cursor.fetchall())
    if len(rows) > 1:
        raise RuntimeError("temporary Lakebase bootstrap role OID inventory is ambiguous")
    return int(rows[0][0]) if rows else None


def _activity_rows(
    cursor: Any,
    *,
    application_id: str,
    role_oid: int | None,
    observed_pids: frozenset[int] = frozenset(),
) -> list[tuple[Any, ...]]:
    cursor.execute(
        """
        SELECT pid, usesysid, usename
        FROM pg_stat_activity
        WHERE pid <> pg_backend_pid()
          AND (
              usesysid = %s
              OR usename = %s
              OR pid = ANY(%s::integer[])
          )
        ORDER BY pid
        """,
        (role_oid, application_id, sorted(observed_pids)),
    )
    return list(cursor.fetchall())


def terminate_bootstrap_sessions(
    cursor: Any,
    *,
    application_id: str,
    expected_executor: str,
    attempts: int = 15,
    required_absence: int = 3,
) -> SessionFence:
    """Terminate OID-bound backends and require stable zero observations."""

    executor = cleanup_executor_identity(
        cursor,
        excluded_application_id=application_id,
        expected_executor=expected_executor,
    )
    captured_oid = _role_oid(cursor, application_id)
    observed_pids: set[int] = set()
    absence_observations = 0
    termination_errors: list[str] = []
    survivors: list[tuple[Any, ...]] = []
    for attempt in range(attempts):
        cleanup_executor_identity(
            cursor,
            excluded_application_id=application_id,
            expected_executor=executor,
        )
        current_oid = _role_oid(cursor, application_id)
        if current_oid != captured_oid:
            raise RuntimeError("temporary Lakebase bootstrap role OID changed during fencing")
        rows = _activity_rows(
            cursor,
            application_id=application_id,
            role_oid=captured_oid,
            observed_pids=frozenset(observed_pids),
        )
        for pid_raw, usesysid_raw, usename_raw in rows:
            pid = int(pid_raw)
            usesysid = None if usesysid_raw is None else int(usesysid_raw)
            usename = str(usename_raw or "")
            observed_pids.add(pid)
            if captured_oid is None or usesysid != captured_oid or usename != application_id:
                raise RuntimeError("temporary Lakebase bootstrap backend OID/name binding drifted")
            try:
                cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
                if cursor.fetchone() != (True,):
                    termination_errors.append(f"pid={pid}: returned false")
            except Exception as exc:  # noqa: BLE001 - prove absence despite failures
                termination_errors.append(f"pid={pid}: {type(exc).__name__}: {exc}")

        survivors = _activity_rows(
            cursor,
            application_id=application_id,
            role_oid=captured_oid,
            observed_pids=frozenset(observed_pids),
        )
        if survivors:
            absence_observations = 0
        else:
            absence_observations += 1
            if absence_observations >= required_absence:
                if termination_errors:
                    raise RuntimeError(
                        "temporary Lakebase bootstrap backend termination was ambiguous: "
                        f"{termination_errors!r}"
                    )
                return SessionFence(
                    role_oid=captured_oid,
                    observed_pids=frozenset(observed_pids),
                    executor=executor,
                )
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError(
        "temporary Lakebase bootstrap sessions did not converge to zero: "
        f"survivors={survivors!r}, errors={termination_errors!r}"
    )


def prove_post_delete_session_absence(
    cursor: Any,
    *,
    application_id: str,
    fence: SessionFence,
    attempts: int = 15,
    required_absence: int = 3,
) -> None:
    """Prove the captured OID and every observed PID stay absent after DROP."""

    absence = 0
    for attempt in range(attempts):
        cleanup_executor_identity(
            cursor,
            excluded_application_id=application_id,
            expected_executor=fence.executor,
        )
        cursor.execute(
            """
            SELECT oid, rolname
            FROM pg_roles
            WHERE rolname = %s OR oid = %s
            ORDER BY oid
            """,
            (application_id, fence.role_oid),
        )
        role_rows = list(cursor.fetchall())
        activity_rows = _activity_rows(
            cursor,
            application_id=application_id,
            role_oid=fence.role_oid,
            observed_pids=fence.observed_pids,
        )
        if not role_rows and not activity_rows:
            absence += 1
            if absence >= required_absence:
                return
        else:
            absence = 0
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("temporary Lakebase bootstrap post-delete OID/PID absence did not converge")


def drain_post_delete_sessions(
    cursor: Any,
    *,
    application_id: str,
    fence: SessionFence,
    attempts: int = 15,
    required_absence: int = 3,
) -> SessionFence:
    """Drain backends bound to a provider-deleted role's captured OID/name."""

    observed_pids = set(fence.observed_pids)
    absence = 0
    errors: list[str] = []
    for attempt in range(attempts):
        cleanup_executor_identity(
            cursor,
            excluded_application_id=application_id,
            expected_executor=fence.executor,
        )
        rows = _activity_rows(
            cursor,
            application_id=application_id,
            role_oid=fence.role_oid,
            observed_pids=frozenset(observed_pids),
        )
        for pid_raw, usesysid_raw, usename_raw in rows:
            pid = int(pid_raw)
            usesysid = None if usesysid_raw is None else int(usesysid_raw)
            usename = str(usename_raw or "")
            observed_pids.add(pid)
            if fence.role_oid is None or usesysid != fence.role_oid or usename != application_id:
                raise RuntimeError("temporary Lakebase bootstrap backend OID/name binding drifted")
            try:
                cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
                if cursor.fetchone() != (True,):
                    errors.append(f"pid={pid}: returned false")
            except Exception as exc:  # noqa: BLE001 - stable absence still decides
                errors.append(f"pid={pid}: {type(exc).__name__}: {exc}")
        survivors = _activity_rows(
            cursor,
            application_id=application_id,
            role_oid=fence.role_oid,
            observed_pids=frozenset(observed_pids),
        )
        if survivors:
            absence = 0
        else:
            absence += 1
            if absence >= required_absence:
                if errors:
                    raise RuntimeError(
                        "temporary Lakebase post-delete backend termination was ambiguous: "
                        f"{errors!r}"
                    )
                return SessionFence(
                    role_oid=fence.role_oid,
                    observed_pids=frozenset(observed_pids),
                    executor=fence.executor,
                )
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("temporary Lakebase post-delete sessions did not converge to zero")


def assert_exact_session_identity(cursor: Any, *, application_id: str) -> None:
    """Require both PostgreSQL identity dimensions to be the one-use role."""

    cursor.execute("SELECT current_user, session_user")
    if cursor.fetchone() != (application_id, application_id):
        raise RuntimeError("temporary Lakebase bootstrap database identity mismatch")
