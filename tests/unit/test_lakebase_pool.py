from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from backend.services import lakebase as lakebase_mod
from backend.services.lakebase import LakebaseClient, LakebaseError, ResilientLakebaseClient
from backend.services.resilience import CircuitBreaker, DependencyDownError

# The child scripts enforce the product behavior with their own 3-4 second
# elapsed-time assertions. The outer watchdog must additionally cover a cold
# interpreter import while the full suite saturates xdist workers; tying it to
# the inner eight-second fake-server release made the otherwise bounded tests
# fail nondeterministically before their assertions could report a result.
_SUBPROCESS_WALL_TIMEOUT_S = 20.0


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeConnection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False
        self.close_count = 0
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> _FakeConnection:
        self.enter_count += 1
        return self

    def __exit__(self, *_exc: Any) -> bool:
        self.exit_count += 1
        return False

    def close(self) -> None:
        self.closed = True
        self.close_count += 1


class _FakeResilient:
    def __init__(self, breaker: CircuitBreaker) -> None:
        self.breaker = breaker


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pool_max_size: int = 2,
    pool_timeout_s: float = 0.05,
    pool_max_lifetime_s: float = 60.0,
    clock: _Clock | None = None,
) -> tuple[LakebaseClient, list[_FakeConnection]]:
    created: list[_FakeConnection] = []

    def _connect(_dsn: str, *, row_factory: Any = None) -> _FakeConnection:
        _ = row_factory
        conn = _FakeConnection(f"c{len(created) + 1}")
        created.append(conn)
        return conn

    monkeypatch.setattr(lakebase_mod.psycopg, "connect", _connect)
    return (
        LakebaseClient(
            host="lakebase.local",
            port=5432,
            database="mip_app_state",
            user="mip",
            password="secret",
            pool_max_size=pool_max_size,
            pool_timeout_s=pool_timeout_s,
            pool_max_lifetime_s=pool_max_lifetime_s,
            now=clock or _Clock(),
        ),
        created,
    )


def test_lakebase_pool_reuses_idle_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    client, created = _client(monkeypatch)

    with client.transaction() as first:
        assert first.name == "c1"
    with client.transaction() as second:
        assert second is first

    assert len(created) == 1
    assert created[0].enter_count == 2
    assert created[0].closed is False

    client.close()
    assert created[0].closed is True


def test_lakebase_pool_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    client, created = _client(monkeypatch, pool_max_size=0)

    with client.transaction() as first:
        assert first.name == "c1"
    with client.transaction() as second:
        assert second.name == "c2"

    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is True


def test_lakebase_pool_enforces_max_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    client, created = _client(monkeypatch, pool_max_size=1, pool_timeout_s=0.01)

    with client.transaction() as first:
        assert first.name == "c1"
        with pytest.raises(LakebaseError, match="pool exhausted"), client.transaction():
            pass

    assert len(created) == 1
    assert created[0].closed is False


def test_lakebase_pool_replaces_expired_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    client, created = _client(
        monkeypatch,
        pool_max_size=1,
        pool_max_lifetime_s=10.0,
        clock=clock,
    )

    with client.transaction() as first:
        assert first.name == "c1"

    clock.advance(11.0)

    with client.transaction() as second:
        assert second.name == "c2"

    assert len(created) == 2
    assert created[0].closed is True
    assert created[1].closed is False


def test_lakebase_pool_closes_connection_after_psycopg_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, created = _client(monkeypatch, pool_max_size=1)

    with pytest.raises(lakebase_mod.psycopg.OperationalError), client.transaction():
        raise lakebase_mod.psycopg.OperationalError("socket closed")

    assert len(created) == 1
    assert created[0].closed is True

    with client.transaction() as second:
        assert second.name == "c2"

    assert len(created) == 2


def test_lakebase_execute_without_params_does_not_bind_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    calls: list[tuple[str, object]] = []

    class _Cursor:
        def __enter__(self) -> _Cursor:
            return self

        def __exit__(self, *_exc: Any) -> bool:
            return False

        def execute(self, sql: str, params: object = sentinel) -> None:
            calls.append((sql, params))

    class _Connection(_FakeConnection):
        def cursor(self) -> _Cursor:
            return _Cursor()

    def _connect(_dsn: str, *, row_factory: Any = None) -> _Connection:
        _ = row_factory
        return _Connection("c1")

    monkeypatch.setattr(lakebase_mod.psycopg, "connect", _connect)
    client = LakebaseClient(
        host="lakebase.local",
        port=5432,
        database="mip_app_state",
        user="mip",
        password="secret",
        pool_max_size=0,
    )

    sql = "DO $$ BEGIN RAISE EXCEPTION 'append-only; % is blocked'; END $$;"
    client.execute(sql)

    assert calls == [(sql, sentinel)]


def test_lakebase_healthcheck_bounds_connect_and_statement_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_dsns: list[str] = []
    calls: list[bytes] = []

    class _Result:
        def __init__(self, status: object, *, ntuples: int = 0) -> None:
            self.status = status
            self.ntuples = ntuples
            self.error_message = b""

    class _PGConnection:
        def __init__(self) -> None:
            self.nonblocking = 0
            self.socket = 1
            self._results = iter(
                [
                    _Result(lakebase_mod.ExecStatus.COMMAND_OK),
                    _Result(lakebase_mod.ExecStatus.COMMAND_OK),
                    _Result(lakebase_mod.ExecStatus.TUPLES_OK, ntuples=1),
                    _Result(lakebase_mod.ExecStatus.COMMAND_OK),
                    None,
                ]
            )

        def send_query(self, query: bytes) -> None:
            calls.append(query)

        def flush(self) -> int:
            return 0

        def is_busy(self) -> int:
            return 0

        def get_result(self) -> object | None:
            return next(self._results)

    class _Connection(_FakeConnection):
        def __init__(self, name: str) -> None:
            super().__init__(name)
            self.pgconn = _PGConnection()

    def _connect(dsn: str, *, row_factory: Any = None) -> _Connection:
        _ = row_factory
        connect_dsns.append(dsn)
        return _Connection("c1")

    monkeypatch.setattr(lakebase_mod.psycopg, "connect", _connect)
    client = LakebaseClient(
        host="lakebase.local",
        port=5432,
        database="mip_app_state",
        user="mip",
        password="secret",
        pool_max_size=0,
        connect_timeout_s=2,
        transport_timeout_s=2,
        health_statement_timeout_s=1.75,
    )

    assert client.healthcheck() is True
    assert "connect_timeout=2" in connect_dsns[0]
    assert "keepalives_idle=2" in connect_dsns[0]
    assert "keepalives_interval=1" in connect_dsns[0]
    assert "keepalives_count=1" in connect_dsns[0]
    assert "tcp_user_timeout=2000" in connect_dsns[0]
    assert calls == [b"BEGIN; SET LOCAL statement_timeout = 1750; SELECT 1 AS one; COMMIT"]


def test_lakebase_connect_timeout_retires_blackholed_subprocess() -> None:
    """A TCP peer that never speaks PostgreSQL cannot pin process shutdown."""

    script = textwrap.dedent(
        """
        import socket
        import threading
        import time

        from backend.services.lakebase import LakebaseClient, LakebaseError

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        release = threading.Event()

        def blackhole():
            conn, _address = server.accept()
            try:
                release.wait(timeout=8.0)
            finally:
                conn.close()
                server.close()

        thread = threading.Thread(target=blackhole, daemon=True)
        thread.start()
        client = LakebaseClient(
            host="127.0.0.1",
            port=port,
            database="mip_app_state",
            user="mip",
            password="secret",
            pool_max_size=0,
            connect_timeout_s=1,
            transport_timeout_s=1,
            health_statement_timeout_s=1.0,
        )
        started = time.monotonic()
        try:
            client.healthcheck()
        except LakebaseError:
            elapsed = time.monotonic() - started
            assert elapsed < 4.0, elapsed
            print("bounded")
        else:
            raise AssertionError("blackholed PostgreSQL handshake unexpectedly succeeded")
        finally:
            release.set()
            thread.join(timeout=1.0)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=_SUBPROCESS_WALL_TIMEOUT_S,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bounded"


def test_lakebase_transport_timeout_retires_authenticated_silent_peer() -> None:
    """An authenticated peer that ACKs a query but never answers is retired."""

    script = textwrap.dedent(
        """
        import socket
        import struct
        import threading
        import time

        from backend.services.lakebase import LakebaseClient, LakebaseError

        def packet(kind, payload):
            return kind + struct.pack("!I", len(payload) + 4) + payload

        def recv_exact(conn, size):
            chunks = []
            remaining = size
            while remaining:
                chunk = conn.recv(remaining)
                if not chunk:
                    raise RuntimeError("client disconnected")
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        query_received = threading.Event()
        release = threading.Event()

        def silent_postgres():
            conn, _address = server.accept()
            try:
                startup_length = struct.unpack("!I", recv_exact(conn, 4))[0]
                recv_exact(conn, startup_length - 4)
                conn.sendall(packet(b"R", struct.pack("!I", 0)))
                conn.sendall(packet(b"S", b"client_encoding\\x00UTF8\\x00"))
                conn.sendall(packet(b"S", b"server_version\\x0016.0\\x00"))
                conn.sendall(packet(b"K", struct.pack("!II", 1234, 5678)))
                conn.sendall(packet(b"Z", b"I"))
                assert recv_exact(conn, 1) == b"Q"
                query_length = struct.unpack("!I", recv_exact(conn, 4))[0]
                recv_exact(conn, query_length - 4)
                query_received.set()
                release.wait(timeout=8.0)
            finally:
                conn.close()
                server.close()

        thread = threading.Thread(target=silent_postgres, daemon=True)
        thread.start()
        client = LakebaseClient(
            host="127.0.0.1",
            port=port,
            database="mip_app_state",
            user="mip",
            password="secret",
            sslmode="disable",
            pool_max_size=0,
            connect_timeout_s=2,
            transport_timeout_s=1,
            health_statement_timeout_s=1.0,
        )
        started = time.monotonic()
        try:
            client.healthcheck()
        except LakebaseError as exc:
            elapsed = time.monotonic() - started
            assert query_received.is_set()
            assert "transport read timed out" in str(exc)
            assert elapsed < 3.0, elapsed
            print("bounded")
        else:
            raise AssertionError("silent authenticated peer unexpectedly returned a result")
        finally:
            release.set()
            thread.join(timeout=1.0)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=_SUBPROCESS_WALL_TIMEOUT_S,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bounded"


def test_reset_client_for_tests_closes_cached_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, created = _client(monkeypatch)
    with client.transaction():
        pass

    monkeypatch.setattr(lakebase_mod, "_CLIENT", client)
    lakebase_mod._reset_client_for_tests()

    assert lakebase_mod._CLIENT is None
    assert created[0].closed is True


def test_lakebase_warm_start_uses_databricks_app_pg_env_hints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Databricks Apps can bind Lakebase as PGHOST/PGUSER without
    LAKEBASE_HOST/LAKEBASE_USER. Warm-start should exercise that live
    path instead of skipping it as "no creds configured"."""
    from backend import main as main_mod
    from backend.services import health_probes

    calls: list[str] = []

    class _WarmClient:
        def fetchone(self, sql: str) -> dict[str, int]:
            calls.append(sql)
            return {"warm": 1}

    monkeypatch.setattr(main_mod.settings, "lakebase_host", "")
    monkeypatch.setattr(main_mod.settings, "lakebase_user", "")
    monkeypatch.setenv("PGHOST", "lakebase.bound.databricks.local")
    monkeypatch.setenv("PGUSER", "app-sp")
    monkeypatch.setattr(lakebase_mod, "get_lakebase_client", lambda: _WarmClient())
    health_probes._probe_cache.clear()

    main_mod._warm_lakebase()

    assert calls == ["SELECT 1 AS one"]


def test_lakebase_health_probe_uses_bound_host_without_pguser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed Apps path may expose the database binding as PGHOST only.

    ``get_lakebase_client`` resolves the user and token through the Databricks
    SDK, so the health probe must not short-circuit to down just because
    PGUSER is absent.
    """
    from backend.services import health_probes

    calls: list[str] = []

    class _WarmClient:
        def fetchone(self, sql: str) -> dict[str, int]:
            calls.append(sql)
            return {"one": 1}

    monkeypatch.setattr(health_probes.settings, "lakebase_host", "")
    monkeypatch.setattr(health_probes.settings, "lakebase_user", "")
    monkeypatch.setenv("PGHOST", "lakebase.bound.databricks.local")
    monkeypatch.delenv("PGUSER", raising=False)
    monkeypatch.setattr(lakebase_mod, "get_lakebase_client", lambda: _WarmClient())

    assert health_probes.probe_lakebase() is True
    assert calls == ["SELECT 1 AS one"]


def test_lakebase_health_probe_prefers_bounded_client_healthcheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import health_probes

    calls: list[str] = []

    class _BoundedClient:
        def healthcheck(self) -> bool:
            calls.append("healthcheck")
            return True

        def fetchone(self, _sql: str) -> None:
            raise AssertionError("unbounded compatibility seam must not be used")

    monkeypatch.setattr(health_probes.settings, "lakebase_host", "lakebase.local")
    monkeypatch.setattr(lakebase_mod, "get_lakebase_client", lambda: _BoundedClient())

    assert health_probes.probe_lakebase() is True
    assert calls == ["healthcheck"]


def test_lakebase_health_probe_accepts_success_beyond_old_one_second_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.services import health_probes

    class _SlowHealthyClient:
        def fetchone(self, _sql: str) -> dict[str, int]:
            time.sleep(1.05)
            return {"one": 1}

    monkeypatch.setattr(health_probes.settings, "lakebase_host", "lakebase.local")
    monkeypatch.setattr(
        lakebase_mod,
        "get_lakebase_client",
        lambda: _SlowHealthyClient(),
    )

    assert health_probes.probe_lakebase() is True


def test_lakebase_startup_warm_uses_bound_host_without_pguser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PGHOST is sufficient because the shared client resolver obtains user.

    The previous startup-only gate required PGUSER as well and silently
    skipped a Lakebase binding that the actual request client could resolve.
    """
    from backend import main as main_mod
    from backend.services import health_probes

    calls: list[str] = []

    class _WarmClient:
        def fetchone(self, sql: str) -> dict[str, int]:
            calls.append(sql)
            return {"warm": 1}

    monkeypatch.setattr(main_mod.settings, "lakebase_host", "")
    monkeypatch.setattr(main_mod.settings, "lakebase_user", "")
    monkeypatch.setenv("PGHOST", "lakebase.bound.databricks.local")
    monkeypatch.delenv("PGUSER", raising=False)
    monkeypatch.setattr(lakebase_mod, "get_lakebase_client", lambda: _WarmClient())
    health_probes._probe_cache.clear()

    main_mod._warm_lakebase()

    assert calls == ["SELECT 1 AS one"]


def test_lakebase_startup_warm_obeys_shared_caller_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import main as main_mod
    from backend.services import health_probes

    release = Event()
    finished = Event()

    class _BlockingClient:
        def fetchone(self, _sql: str) -> dict[str, int]:
            release.wait(timeout=2.0)
            finished.set()
            return {"one": 1}

    monkeypatch.setattr(main_mod.settings, "lakebase_host", "lakebase.local")
    monkeypatch.setattr(health_probes.settings, "lakebase_host", "lakebase.local")
    monkeypatch.setattr(
        health_probes.settings,
        "mip_health_cold_wait_budget_s",
        0.05,
    )
    monkeypatch.setattr(lakebase_mod, "get_lakebase_client", lambda: _BlockingClient())
    health_probes._probe_cache.clear()

    started = time.monotonic()
    try:
        main_mod._warm_lakebase()
        elapsed = time.monotonic() - started
        # The semantic contract is that startup returns before the blocked
        # dependency, not that an xdist worker is always scheduled within a
        # 250 ms wall-clock window. Keep a generous bound well below the
        # dependency's two-second block and prove the probe is still pending.
        assert elapsed < 1.0
        assert not finished.is_set()
    finally:
        release.set()
        health_probes._probe_cache.clear()


def test_resilient_lakebase_transaction_refuses_when_breaker_open() -> None:
    breaker = CircuitBreaker("lakebase-test", failure_threshold=1, cooldown_s=60)
    breaker.record_failure()

    class _RawClient:
        @contextmanager
        def transaction(self) -> Iterator[object]:
            raise AssertionError("raw transaction should not be reached")
            yield object()

    client = ResilientLakebaseClient(_RawClient(), _FakeResilient(breaker))  # type: ignore[arg-type]

    with pytest.raises(DependencyDownError) as exc_info, client.transaction():
        pass

    assert exc_info.value.dependency == "lakebase"
    assert exc_info.value.kind == DependencyDownError.KIND_BREAKER_OPEN


def test_resilient_lakebase_transaction_records_success_and_failures() -> None:
    breaker = CircuitBreaker("lakebase-test", failure_threshold=1, cooldown_s=60)

    class _RawClient:
        @contextmanager
        def transaction(self) -> Iterator[object]:
            yield object()

    client = ResilientLakebaseClient(_RawClient(), _FakeResilient(breaker))  # type: ignore[arg-type]

    with client.transaction():
        pass

    assert breaker.state == "closed"

    class _FailingRawClient:
        @contextmanager
        def transaction(self) -> Iterator[object]:
            raise lakebase_mod.psycopg.OperationalError("socket closed")
            yield object()

    failing = ResilientLakebaseClient(_FailingRawClient(), _FakeResilient(breaker))  # type: ignore[arg-type]

    with pytest.raises(LakebaseError, match="Lakebase transaction failed"), failing.transaction():
        pass

    assert breaker.state == "open"
