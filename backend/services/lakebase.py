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

import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from backend.config.settings import settings
from backend.services.observability import emit

log = logging.getLogger(__name__)


def _stmt_hash(sql: str) -> str:
    return hashlib.sha1(sql.encode("utf-8")).hexdigest()[:16]  # noqa: S324 -- not a secret


def _emit_start(op: str, sql: str) -> tuple[str, float]:
    stmt_hash = _stmt_hash(sql)
    emit(log, "lakebase_query_start", dependency="lakebase",
         operation=op, statement_hash=stmt_hash)
    return stmt_hash, time.monotonic()


def _emit_end(op: str, stmt_hash: str, start: float, *,
              rows_returned: int | None = None) -> None:
    kwargs: dict[str, Any] = {
        "dependency": "lakebase",
        "operation": op,
        "statement_hash": stmt_hash,
        "duration_ms": round((time.monotonic() - start) * 1000.0, 2),
        "outcome": "ok",
    }
    if rows_returned is not None:
        kwargs["rows_returned"] = rows_returned
    emit(log, "lakebase_query_end", **kwargs)


def _emit_err(op: str, stmt_hash: str, start: float, exc: BaseException,
              attempt: int | None = None) -> None:
    kwargs: dict[str, Any] = {
        "level": logging.WARNING,
        "dependency": "lakebase",
        "operation": op,
        "statement_hash": stmt_hash,
        "duration_ms": round((time.monotonic() - start) * 1000.0, 2),
        "outcome": "error",
        "exc_type": type(exc).__name__,
        "exc_msg": str(exc)[:500],
    }
    if attempt is not None:
        kwargs["attempt"] = attempt
    emit(log, "lakebase_query_error", **kwargs)


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
    for the Module 0 traffic shape; Slice 6 will add pooling.
    """

    _supports_atomic_transactions = True

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str | None = None,
        sslmode: str = "require",
        password_provider: Callable[[], str] | None = None,
    ) -> None:
        if not host:
            raise LakebaseError("Lakebase host is empty")
        if not database:
            raise LakebaseError("Lakebase database is empty")
        if not user:
            raise LakebaseError("Lakebase user is empty")
        # 2026-05-04 fix: prior client cached the password ONCE at
        # startup. Lakebase's workspace-identity OAuth tokens are
        # short-lived (~1h) — once expired, every reconnect failed
        # with `password authentication failed for user '<sp-uuid>'`,
        # the breaker tripped open, half-opened, failed again, and the
        # FE banner showed "Reconnecting to operational database" 99 %
        # of the time. The fix mirrors the warehouse OAuth-refresh
        # pattern in commit cdacafd: a `password_provider` callable
        # that mints a fresh credential per connection. Static-string
        # passwords (laptop / CI with `.env.local` LAKEBASE_PASSWORD)
        # still work — pass `password=...` and we wrap it in a const
        # provider.
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._sslmode = sslmode
        if password_provider is not None:
            self._password_provider = password_provider
        else:
            constant = password or ""
            self._password_provider = lambda: constant

    def _dsn(self) -> str:
        """Build a libpq keyword-style DSN.

        Calls ``self._password_provider()`` on each invocation so a
        rotating workspace-identity OAuth token is always fresh.
        Static-string passwords just return the same value every call
        (zero overhead for the laptop / CI path).
        """
        pwd = self._password_provider() or ""
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
        stmt_hash, start = _emit_start("execute", sql)
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
        except psycopg.Error as exc:
            _emit_err("execute", stmt_hash, start, exc)
            raise LakebaseError(f"Lakebase execute failed: {exc}") from exc
        _emit_end("execute", stmt_hash, start)

    def executemany(self, sql: str, params_list: list[dict[str, Any]]) -> None:
        """Batch-execute a write. All rows run inside one transaction."""
        if not params_list:
            return
        stmt_hash, start = _emit_start("executemany", sql)
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.executemany(sql, params_list)
        except psycopg.Error as exc:
            _emit_err("executemany", stmt_hash, start, exc)
            raise LakebaseError(f"Lakebase executemany failed: {exc}") from exc
        _emit_end("executemany", stmt_hash, start, rows_returned=len(params_list))

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Return the first row as a dict, or None if the query produced no rows."""
        stmt_hash, start = _emit_start("fetchone", sql)
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
                row = cur.fetchone()
                if row is None:
                    _emit_end("fetchone", stmt_hash, start, rows_returned=0)
                    return None
                # dict_row factory gives us a dict already; cast for mypy.
                result = dict(row)
        except psycopg.Error as exc:
            _emit_err("fetchone", stmt_hash, start, exc)
            raise LakebaseError(f"Lakebase fetchone failed: {exc}") from exc
        _emit_end("fetchone", stmt_hash, start, rows_returned=1)
        return result

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
        stmt_hash, start = _emit_start("fetchall", sql)
        try:
            with self.transaction() as conn, conn.cursor() as cur:
                cur.execute(sql, params or {})
                rows = cur.fetchmany(size=limit)
                result = [dict(r) for r in rows]
        except psycopg.Error as exc:
            _emit_err("fetchall", stmt_hash, start, exc)
            raise LakebaseError(f"Lakebase fetchall failed: {exc}") from exc
        _emit_end("fetchall", stmt_hash, start, rows_returned=len(result))
        return result


_CLIENT: LakebaseClient | None = None
_LOCK = Lock()


def _mint_lakebase_token(instance_name: str) -> str:
    """Mint a fresh Lakebase OAuth token via the workspace SDK.

    Called per-connection (not once at startup) so the bearer is
    always within its short-lived expiry window. Wraps every failure
    in a LakebaseError with enough context to surface in the audit-
    feed banner without leaking the raw SDK message. 2026-05-04 fix
    for the persistent "Reconnecting to operational database" banner
    that traced to a stale cached token.
    """
    try:  # pragma: no cover -- exercised on deployed Databricks Apps only
        import os as _os

        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
        cred = workspace.api_client.do(
            "POST",
            "/api/2.0/database/credentials",
            body={
                "request_id": (
                    f"mip-backend-"
                    f"{_os.environ.get('DATABRICKS_JOB_RUN_ID','app')}"
                ),
                "instance_names": [instance_name],
            },
        )
        token = cred.get("token") or ""
        if not token:
            raise LakebaseError(
                "Lakebase token mint returned empty token; check the database "
                "resource binding on the app and CAN_CONNECT_AND_CREATE on the "
                "instance."
            )
        return token
    except LakebaseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LakebaseError(f"Lakebase token mint failed: {exc}") from exc


def _resolve_lakebase_connection_params() -> tuple[
    str, int, str, str, str, str, Callable[[], str] | None,
]:
    """Return ``(host, port, database, user, password, sslmode, password_provider)``.

    The 7th element is an optional per-call provider used by
    ``LakebaseClient`` to mint a fresh OAuth token on every connection
    when the static password is empty (the deployed-Apps path). Local /
    CI paths return ``provider=None`` and the constant-password
    behaviour applies.

    Three supported pathways:

    1. **Local / CI with full LAKEBASE_* env vars** -- return them as-is.
    2. **Databricks Apps (``database`` resource binding)** -- the runtime
       injects ``PGHOST``, ``PGUSER``, ``PGPORT``, ``PGDATABASE`` but
       NOT ``PGPASSWORD`` (that's meant to be a short-lived OAuth
       token minted on demand). Fall through to the SDK credential
       minter below.
    3. **Databricks workspace identity** -- call
       ``/api/2.0/database/credentials`` via ``databricks-sdk`` to
       obtain a short-lived Postgres token; resolve the host via
       ``GET /api/2.0/database/instances/{instance}`` when it isn't
       already set.

    The shape mirrors ``jobs/lakebase_migrate.py::_resolve_connection``
    so both the backend and the migrate job share a single auth story;
    any update here should be mirrored there (and vice versa).
    """
    import os as _os

    host = (settings.lakebase_host or _os.environ.get("PGHOST") or "").strip()
    port_raw = _os.environ.get("PGPORT") or str(settings.lakebase_port)
    port = int(port_raw) if port_raw else 5432
    database = (settings.lakebase_database or _os.environ.get("PGDATABASE") or "mip_app_state").strip()
    user = (settings.lakebase_user or _os.environ.get("PGUSER") or "").strip()
    password_secret = settings.lakebase_password
    password = password_secret.get_secret_value() if password_secret else ""
    if not password:
        password = _os.environ.get("PGPASSWORD") or ""
    sslmode = settings.lakebase_sslmode or "require"

    if host and user and password:
        # Static-credential path (laptop / CI). No provider needed.
        return host, port, database, user, password, sslmode, None

    instance_name = _os.environ.get("LAKEBASE_INSTANCE_NAME", "mip-app-state")

    # Resolve host + user via the SDK (one-time; these don't rotate).
    try:  # pragma: no cover -- exercised on deployed Databricks Apps only
        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
        if not host:
            inst = workspace.api_client.do(
                "GET", f"/api/2.0/database/instances/{instance_name}"
            )
            host = inst.get("read_write_dns") or ""
        if not user:
            me = workspace.current_user.me()
            user = me.user_name or me.display_name or ""
    except Exception as exc:  # noqa: BLE001 -- surface reason on first call
        raise LakebaseError(f"Lakebase host/user resolution failed: {exc}") from exc

    # Per-call password provider so the OAuth token is always fresh.
    # An expired bearer used to surface as
    #   "password authentication failed for user '<sp-uuid>'"
    # on every reconnect, tripping the breaker and pinning the
    # "Reconnecting to operational database" banner.
    def _provider() -> str:
        return _mint_lakebase_token(instance_name)

    return host, port, database, user, "", sslmode, _provider


def get_lakebase_client() -> LakebaseClient:
    """Lazy process-singleton accessor.

    Reads credentials via ``_resolve_lakebase_connection_params``, which
    honours the full LAKEBASE_* env-var set on laptops / CI and falls
    through to Databricks Apps' ``PG*`` + workspace-identity token
    minting for in-workspace deploys. Missing host / user / db raise
    ``LakebaseError`` -- the audit router catches this and returns HTTP
    503, preserving the no-silent-fallback invariant.

    Slice-6: the returned object is a ``ResilientLakebaseClient`` that
    wraps the raw psycopg client with a circuit breaker + retry. When
    the breaker is open, calls raise ``DependencyDownError`` instead
    of spending 30s stalling on a dead socket.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    # Local import to avoid import cycle: resilience imports nothing
    # application-specific, so this direction is safe.
    from backend.services.resilience import Resilient, get_breaker

    with _LOCK:
        if _CLIENT is None:
            (
                host, port, database, user, password, sslmode, provider,
            ) = _resolve_lakebase_connection_params()
            bare = LakebaseClient(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                sslmode=sslmode,
                password_provider=provider,
            )
            resilient = Resilient[Any](
                breaker=get_breaker("lakebase"),
                dependency_name="lakebase",
                attempts=3,
                backoff_base=0.2,
                backoff_max=1.5,
                retry_on=(LakebaseError, OSError),
            )
            _CLIENT = ResilientLakebaseClient(bare, resilient)
        return _CLIENT


def _reset_client_for_tests() -> None:
    """Test helper -- drop the cached client so factory overrides stick."""
    global _CLIENT
    with _LOCK:
        _CLIENT = None


# ---------------------------------------------------------------------------
# Slice-6 resilience wrapper -- keeps the LakebaseClient surface the audit
# store depends on (``execute`` / ``executemany`` / ``fetchone`` /
# ``fetchall``) but routes each call through the breaker+retry composition.
# ---------------------------------------------------------------------------


class ResilientLakebaseClient:
    """Thin adapter that adds breaker + retry to the Lakebase client.

    Duck-types ``LakebaseClient`` -- the audit store already holds a
    ``LakebaseClient``-typed attribute, and the wrapper exposes the
    same read/write + transaction methods so no call-site changes are
    required. Multi-statement transaction blocks intentionally are not
    retried as a unit; the caller owns idempotency keys before entering
    the transaction.
    """

    _supports_atomic_transactions = True

    def __init__(self, client: LakebaseClient, resilient: Any) -> None:
        self._client = client
        self._resilient = resilient

    @property
    def resilient(self) -> Any:
        return self._resilient

    @contextmanager
    def transaction(self) -> Iterator[Connection[Any]]:
        with self._client.transaction() as conn:
            yield conn

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self._resilient.call(lambda: self._client.execute(sql, params))

    def executemany(self, sql: str, params_list: list[dict[str, Any]]) -> None:
        self._resilient.call(lambda: self._client.executemany(sql, params_list))

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        return self._resilient.call(lambda: self._client.fetchone(sql, params))

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self._resilient.call(lambda: self._client.fetchall(sql, params, limit))
