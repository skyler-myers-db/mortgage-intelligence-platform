"""Session-level serialization for one target's Lakebase OAuth bootstrap."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BootstrapLockLease:
    key: int
    backend_pid: int


def bootstrap_lock_key(
    *,
    instance_name: str,
    target_application_id: str,
) -> int:
    digest = hashlib.sha256(f"{instance_name}\0{target_application_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _lock_parts(lock_key: int) -> tuple[int, int]:
    unsigned = lock_key & ((1 << 64) - 1)
    return unsigned >> 32, unsigned & ((1 << 32) - 1)


def assert_bootstrap_lock_held(
    cursor: Any,
    *,
    lock_key: BootstrapLockLease,
) -> None:
    cursor.execute("SELECT pg_backend_pid()")
    if cursor.fetchone() != (lock_key.backend_pid,):
        raise RuntimeError("Lakebase bootstrap target advisory lock backend changed")
    class_id, object_id = _lock_parts(lock_key.key)
    cursor.execute(
        """
        SELECT count(*)
        FROM pg_locks
        WHERE locktype = 'advisory'
          AND pid = pg_backend_pid()
          AND classid = %s
          AND objid = %s
          AND objsubid = 1
          AND mode = 'ExclusiveLock'
          AND granted
        """,
        (class_id, object_id),
    )
    if cursor.fetchone() != (1,):
        raise RuntimeError("Lakebase bootstrap target advisory lock was lost")


def acquire_bootstrap_lock(
    cursor: Any,
    *,
    instance_name: str,
    target_application_id: str,
    attempts: int = 15,
) -> BootstrapLockLease:
    lock_key = bootstrap_lock_key(
        instance_name=instance_name,
        target_application_id=target_application_id,
    )
    for attempt in range(attempts):
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
        if cursor.fetchone() == (True,):
            cursor.execute("SELECT pg_backend_pid()")
            pid_row = cursor.fetchone()
            if pid_row is None:
                raise RuntimeError("Lakebase bootstrap lock backend PID is absent")
            lease = BootstrapLockLease(key=lock_key, backend_pid=int(pid_row[0]))
            assert_bootstrap_lock_held(cursor, lock_key=lease)
            return lease
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RuntimeError("Lakebase bootstrap target advisory lock is contended")


def release_bootstrap_lock(cursor: Any, *, lock_key: BootstrapLockLease) -> None:
    assert_bootstrap_lock_held(cursor, lock_key=lock_key)
    cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_key.key,))
    if cursor.fetchone() != (True,):
        raise RuntimeError("Lakebase bootstrap target advisory lock release failed")
    class_id, object_id = _lock_parts(lock_key.key)
    cursor.execute(
        """
        SELECT count(*)
        FROM pg_locks
        WHERE locktype = 'advisory'
          AND pid = pg_backend_pid()
          AND classid = %s
          AND objid = %s
          AND objsubid = 1
          AND mode = 'ExclusiveLock'
          AND granted
        """,
        (class_id, object_id),
    )
    if cursor.fetchone() != (0,):
        raise RuntimeError("Lakebase bootstrap target advisory lock survived release")
