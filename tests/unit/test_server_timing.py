"""Server-Timing contract through the real ``backend.main.app`` stack.

2026-09-21 UI/UX audit ``delivery-v3`` (server half). The header is the
contract w2-error-telemetry's ``rum.ts`` consumes:

    cache;desc=hit|miss|stale, warehouse;dur=<ms>, lakebase;dur=<ms>, total;dur=<ms>

Probe routes are inserted ahead of the ``/api/{path}`` 404 catch-all (the same
technique as ``test_cache_headers.py``) so each assertion runs behind the real
middleware stack: gzip, security headers, correlation id, Server-Timing, visit
tracking and backpressure.
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from backend.config.settings import settings
from backend.main import _backpressure_controller, app
from backend.services import lakebase
from backend.services.databricks_sql import DatabricksSqlClient
from backend.services.resilience_cache import TTLCache
from backend.services.server_timing import record_cache, record_dependency

_DUR = r"\d+\.\d"
_PROBE_CACHE = TTLCache()


def _sync_probe(_request: Request) -> Response:
    """A SYNC endpoint: Starlette runs it in the threadpool, like every route."""
    record_dependency("warehouse", 12.34)
    record_cache("hit")
    record_cache("stale")
    record_cache("hit")
    return JSONResponse({"ok": True})


def _executor_probe(_request: Request) -> Response:
    """Work handed to a plain executor starts with an empty context."""

    def background() -> None:
        record_dependency("lakebase", 50.0)
        record_cache("miss")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(background).result()
    record_dependency("warehouse", 1.0)
    return JSONResponse({"ok": True})


def _ttl_cache_probe(_request: Request) -> Response:
    _PROBE_CACHE.get_or_set("server-timing-probe", lambda: {"v": 1}, ttl_s=60.0)
    record_dependency("lakebase", 2.0)
    record_dependency("lakebase", 3.0)
    return JSONResponse({"ok": True})


def _client_recorder_probe(_request: Request) -> Response:
    """The real recorders: the SQL client's statement path and Lakebase's end hook."""
    client = DatabricksSqlClient("https://workspace.invalid", "token", "warehouse-id")
    succeeded = {
        "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": "one", "type_name": "INT"}]}},
        "result": {"data_array": [["1"]]},
    }
    client._post = lambda _url, _body: succeeded  # type: ignore[method-assign]
    client.execute("SELECT 1 AS one")
    lakebase._emit_end("fetchone", "0" * 16, time.monotonic())
    return JSONResponse({"ok": True})


async def _async_probe(_request: Request) -> Response:
    record_dependency("warehouse", 7.0)
    return JSONResponse({"ok": True})


def _page_probe(_request: Request) -> Response:
    record_dependency("warehouse", 5.0)
    return Response("<html></html>", media_type="text/html")


@pytest.fixture
def client() -> Iterator[TestClient]:
    probes = [
        Route("/api/v1/pytest-timing-sync", _sync_probe),
        Route("/api/v1/pytest-timing-executor", _executor_probe),
        Route("/api/v1/pytest-timing-ttl", _ttl_cache_probe),
        Route("/api/v1/pytest-timing-async", _async_probe),
        Route("/api/v1/pytest-timing-clients", _client_recorder_probe),
        Route("/pytest-timing-page", _page_probe),
    ]
    app.router.routes[0:0] = probes
    _PROBE_CACHE.clear()
    try:
        yield TestClient(app)
    finally:
        for probe in probes:
            app.router.routes.remove(probe)
        _PROBE_CACHE.clear()


def test_sync_handler_records_reach_the_header_exactly(client: TestClient) -> None:
    response = client.get("/api/v1/pytest-timing-sync")

    assert response.status_code == 200
    header = response.headers["server-timing"]
    assert re.fullmatch(rf"cache;desc=stale, warehouse;dur=12\.3, total;dur={_DUR}", header), header


def test_executor_thread_records_do_not_leak_into_the_request(client: TestClient) -> None:
    header = client.get("/api/v1/pytest-timing-executor").headers["server-timing"]

    assert re.fullmatch(rf"warehouse;dur=1\.0, total;dur={_DUR}", header), header
    assert "lakebase" not in header
    assert "cache" not in header


def test_ttl_cache_outcomes_and_summed_lakebase_duration(client: TestClient) -> None:
    first = client.get("/api/v1/pytest-timing-ttl").headers["server-timing"]
    second = client.get("/api/v1/pytest-timing-ttl").headers["server-timing"]

    assert re.fullmatch(rf"cache;desc=miss, lakebase;dur=5\.0, total;dur={_DUR}", first), first
    assert re.fullmatch(rf"cache;desc=hit, lakebase;dur=5\.0, total;dur={_DUR}", second), second


def test_async_handler_records_too(client: TestClient) -> None:
    header = client.get("/api/v1/pytest-timing-async").headers["server-timing"]

    assert re.fullmatch(rf"warehouse;dur=7\.0, total;dur={_DUR}", header), header


def test_sql_client_and_lakebase_hooks_record_their_durations(client: TestClient) -> None:
    header = client.get("/api/v1/pytest-timing-clients").headers["server-timing"]

    assert re.fullmatch(rf"warehouse;dur={_DUR}, lakebase;dur={_DUR}, total;dur={_DUR}", header), header


def test_non_api_paths_carry_no_header(client: TestClient) -> None:
    response = client.get("/pytest-timing-page")

    assert response.status_code == 200
    assert "server-timing" not in response.headers


def test_unmatched_api_path_still_carries_total_only(client: TestClient) -> None:
    header = client.get("/api/v1/pytest-timing-nowhere").headers["server-timing"]

    assert re.fullmatch(rf"total;dur={_DUR}", header), header


def test_backpressure_429_still_carries_total(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mip_rate_limit_default_per_minute", 1)
    _backpressure_controller.clear()
    headers = {
        "X-Enable-Backpressure-Test": "1",
        "X-Forwarded-Email": "timing.probe@summit.example",
    }
    try:
        client.get("/api/v1/pytest-timing-async", headers=headers)
        limited = client.get("/api/v1/pytest-timing-async", headers=headers)
    finally:
        _backpressure_controller.clear()

    assert limited.status_code == 429
    assert re.fullmatch(rf"total;dur={_DUR}", limited.headers["server-timing"])


def test_header_values_are_enum_or_numeric_only(client: TestClient) -> None:
    header = client.get("/api/v1/pytest-timing-sync").headers["server-timing"]

    for entry in header.split(", "):
        name, _, value = entry.partition(";")
        assert name in {"cache", "warehouse", "lakebase", "total"}
        assert re.fullmatch(rf"desc=(hit|miss|stale)|dur={_DUR}", value), entry
