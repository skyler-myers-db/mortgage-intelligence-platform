from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from backend.services import lakebase as lakebase_mod
from backend.services.lakebase import LakebaseClient, LakebaseError, ResilientLakebaseClient
from backend.services.resilience import CircuitBreaker, DependencyDownError


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

    main_mod._warm_lakebase()

    assert calls == ["SELECT 1 AS warm"]


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
