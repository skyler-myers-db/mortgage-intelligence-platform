"""Lakebase Postgres client for Module 0 app state.

Slice-5 cutover: the in-memory ``AuditStore`` is replaced with a
Postgres-backed store that writes to ``mip_app.action_audit`` and reads
it back in descending-time order. This module is the thin, reusable
connection seam; the audit adapter lives in
``backend.services.audit_store``.

Design notes:

* We use **psycopg3 (binary wheel)** because the FastAPI runtime on
  Databricks Apps is sync (`app.yaml` -> `python -m backend.runtime` ->
  uvicorn) and psycopg3 has first-class sync + async interfaces. The
  binary wheel avoids the libpq-dev toolchain on the App image.
* Connections are reused via a process-wide singleton client; each
  write opens a short transaction and commits. For Slice 5 we keep it
  simple (no pool); Slice 6 will layer ``psycopg_pool`` + circuit
  breaker on top of this module without reshaping the API.
* **Named-parameter binding only** (``%(name)s``). String interpolation
  is banned -- every call site goes through ``execute`` /
  ``fetchone`` / ``fetchall`` / ``executemany`` with a params dict.
* **No silent fallback.** Connection failures raise ``LakebaseError``.
  The audit router propagates that as a 503; the FastAPI lifespan does
  *not* gate on Lakebase (we gate on Databricks warehouse creds -- see
  ``backend/runtime.py``) because Slice 6 will add retry/circuit-break
  and Slice 5 must prove the round-trip works at all.

The client is built once per process via ``get_lakebase_client()`` and
reused across requests. Tests do NOT touch this module directly; the
audit-store unit tests inject a mock ``LakebaseClient`` through the
factory override so no real network call is ever attempted in pytest.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from backend.config.settings import settings

log = logging.getLogger(__name__)


class LakebaseError(RuntimeError):
    """Raised when a Lakebase query cannot be executed.

    Wraps ``psycopg`` errors so the audit router can catch a single
    type and convert to HTTP 503, and so tests can assert on a stable
    exception class across psycopg major versions.
    """


class LakebaseClient:
    """Sync Postgres client wired to the Lakebase instance.

    One instance per process. Thread-safe at the method level because
    each call opens a fresh connection with ``psycopg.connect(...)``
    and closes it when the context manager exits. That keeps the
    connection count linear with concurrent requests, which is fine
    for the DAIS-demo traffic shape; Slice 6 will add pooling.
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        sslmode: str = "require",
    ) -> None:
        if not host:
            raise LakebaseError("Lakebase host is empty")
        if not database:
            raise LakebaseError("Lakebase database is empty")
        if not user:
            raise LakebaseError("Lakebase user is empty")
        # password can be empty for workspace-identity auth paths; the
        # real validation is done by Postgres on CONNECT.
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password
        self._sslmode = sslmode

    def _dsn(self) -> str:
        """Build a libpq keyword-style DSN.

        We construct the DSN string rather than using a conninfo dict
        so psycopg can log / reuse the exact form the user configured
        in ``.env.local``. The password is passed positionally; no
        value ever reaches stdout unless the user enables SQL debug.
        """
        # Password may be None / empty in workspace-identity flows;
        # psycopg accepts an empty password and delegates to libpq.
        pwd = self._password or ""
        # Escape single quotes in the password so it can't break out
        # of the value -- libpq also requires this.
        pwd_escaped = pwd.replace("\\", "\\\\").replace("'", "\\'")
        return (
            f"host={self._host} "
            f"port={self._port} "
            f"dbname={self._database} "
            f"user={self._user} "
            f"password='{pwd_escaped}' "
            f"sslmode={self._sslmode}"
        )

    def _connect(self) -> Connection[Any]:
        try:
            return psycopg.connect(self._dsn(), row_factory=dict_row)
        except psycopg.Error as exc:
            raise LakebaseError(f"Lakebase connect failed: {exc}") from exc

    @contextmanager
    def transaction(self) -> Iterator[Connection[Any]]:
        """Short transaction context manager.

        Usage::

            with client.transaction() as conn:
                conn.execute("INSERT ...", params)
                conn.execute("INSERT ...", params)

        The `with` block commits on clean exit and rolls back on any
        exception. Use this for multi-statement writes where
        all-or-nothing semantics matter (e.g. approval-plus-audit).
        """
        conn = self._connect()
        try:
            with conn:  # psycopg 3 commits on __exit__, rolls back on exc
                yield conn
        finally:
            conn.close()

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        """Execute a write statement. Returns None on success, raises otherwise."""
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
        except psycopg.Error as exc:
            raise LakebaseError(f"Lakebase execute failed: {exc}") from exc

    def executemany(self, sql: str, params_list: list[dict[str, Any]]) -> None:
        """Batch-execute a write. All rows run inside one transaction."""
        if not params_list:
            return
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.executemany(sql, params_list)
        except psycopg.Error as exc:
            raise LakebaseError(f"Lakebase executemany failed: {exc}") from exc

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Return the first row as a dict, or None if the query produced no rows."""
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
                row = cur.fetchone()
                if row is None:
                    return None
                # dict_row factory gives us a dict already; cast for mypy.
                return dict(row)
        except psycopg.Error as exc:
            raise LakebaseError(f"Lakebase fetchone failed: {exc}") from exc

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` rows as a list of dicts.

        ``limit`` is enforced at the Python layer in addition to any
        ``LIMIT`` clause the caller passed -- a belt-and-suspenders
        guard against an unbounded SELECT accidentally streaming the
        whole audit table into memory.
        """
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
                rows = cur.fetchmany(size=limit)
                return [dict(r) for r in rows]
        except psycopg.Error as exc:
            raise LakebaseError(f"Lakebase fetchall failed: {exc}") from exc


_CLIENT: LakebaseClient | None = None
_LOCK = Lock()


def get_lakebase_client() -> LakebaseClient:
    """Lazy process-singleton accessor.

    Reads credentials from ``settings`` (which reads them from
    ``.env.local`` + workspace environment). Missing host / user / db
    raise ``LakebaseError`` -- the audit router catches this and
    returns HTTP 503, preserving the no-silent-fallback invariant.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _LOCK:
        if _CLIENT is None:
            password_secret = settings.lakebase_password
            password = password_secret.get_secret_value() if password_secret else ""
            _CLIENT = LakebaseClient(
                host=settings.lakebase_host or "",
                port=settings.lakebase_port,
                database=settings.lakebase_database,
                user=settings.lakebase_user or "",
                password=password,
                sslmode=settings.lakebase_sslmode,
            )
        return _CLIENT


def _reset_client_for_tests() -> None:
    """Test helper -- drop the cached client so factory overrides stick."""
    global _CLIENT
    with _LOCK:
        _CLIENT = None
