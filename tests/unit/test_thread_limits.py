"""anyio's default thread limiter is sized in lifespan (audit delivery-09, S half).

Every sync route, sync dependency and static file runs on anyio's default
worker-thread limiter (40 tokens by default), which the 24 + 16 + 6 dependency
slots alone could exhaust. The lifespan now sizes it from
``MIP_ANYIO_THREAD_TOKENS`` first thing, including under pytest.
"""
from __future__ import annotations

from collections.abc import Iterator

import anyio.to_thread
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend.config.settings import Settings, settings
from backend.main import app


async def _limiter_probe(_request: Request) -> Response:
    limiter = anyio.to_thread.current_default_thread_limiter()
    return JSONResponse({"total_tokens": limiter.total_tokens})


@pytest.fixture
def probe_route() -> Iterator[None]:
    route = Route("/api/v1/pytest-thread-limiter", _limiter_probe)
    app.router.routes.insert(0, route)
    try:
        yield
    finally:
        app.router.routes.remove(route)


def _tokens_seen_by_a_request() -> int:
    with TestClient(app) as client:  # runs the real lifespan
        return int(client.get("/api/v1/pytest-thread-limiter").json()["total_tokens"])


def test_lifespan_raises_the_default_limiter_to_100(probe_route: None) -> None:
    assert Settings(_env_file=None).mip_anyio_thread_tokens == 100

    assert _tokens_seen_by_a_request() == 100


def test_the_limiter_follows_the_setting(probe_route: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "mip_anyio_thread_tokens", 160)

    assert _tokens_seen_by_a_request() == 160


def test_without_the_lifespan_the_limiter_is_anyio_default(probe_route: None) -> None:
    """Control: a TestClient that skips the lifespan sees anyio's 40."""
    assert int(TestClient(app).get("/api/v1/pytest-thread-limiter").json()["total_tokens"]) == 40


def test_the_limit_stays_above_every_dependency_slot() -> None:
    fresh = Settings(_env_file=None)
    slots = (
        fresh.mip_warehouse_concurrency_limit
        + fresh.mip_lakebase_concurrency_limit
        + fresh.mip_genie_concurrency_limit
    )

    assert fresh.mip_anyio_thread_tokens > slots


@pytest.mark.parametrize("value", ["39", "401"])
def test_the_setting_is_bounded(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIP_ANYIO_THREAD_TOKENS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
