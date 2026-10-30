"""The release tools' 429 handling against the SDK's own retry, not a fake's.

SDK 0.103.0's ``ApiClient.do`` retries a GET (export, list) that answers 429
until ``retry_timeout_seconds`` (300 s by default) and then raises
``TimeoutError`` from the 429; it sends a workspace import (POST with form
fields) once. A FakeTree that raises a bare 429 from a GET hides that, so the
second half of this module drives the release functions through a real
``WorkspaceClient`` against a local server that throttles, on an instant
clock: the SDK's real retry, the default timeout, and no test waits.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from databricks.sdk.errors import (
    RequestLimitExceeded,
    ResourceExhausted,
    TemporarilyUnavailable,
    TooManyRequests,
)

from tests.fixtures.fake_workspace import InstantClock, sdk_gave_up
from tools.databricks import app_release_contract as contract
from tools.databricks import app_release_retry as retry
from tools.databricks import app_release_source as source

EXPORT = "GET /api/2.0/workspace/export"
LIST = "GET /api/2.0/workspace/list"
IMPORT = "POST /api/2.0/workspace/import"
ROOT = "/Workspace/Users/deployer/app-src/release"


def _policy() -> tuple[retry.RateLimitRetry, list[float]]:
    waits: list[float] = []
    return retry.RateLimitRetry(sleep=waits.append, jitter=lambda: 1.0), waits


def _raising(error: BaseException, calls: list[int]) -> Any:
    def call() -> None:
        calls.append(1)
        raise error

    return call


# -- RateLimitRetry.call and the shapes the SDK's retry gives up with ---------------------


@pytest.mark.parametrize("cls", [TooManyRequests, ResourceExhausted, RequestLimitExceeded])
@pytest.mark.parametrize("max_attempts", [None, 3], ids=["timed-out", "max-attempts"])
def test_a_429_the_sdk_already_retried_is_refused_at_once(
    cls: type[Exception], max_attempts: int | None
) -> None:
    gave_up = sdk_gave_up(cls("429", retry_after_secs=1), max_attempts=max_attempts)
    assert isinstance(gave_up, TimeoutError if max_attempts is None else RuntimeError)
    policy, waits = _policy()
    calls: list[int] = []

    with pytest.raises(retry.RateLimitExhausted) as refused:
        policy.call("download of /x", _raising(gave_up, calls))

    assert calls == [1] and waits == []  # the SDK spent the time; no second budget
    assert "the download of /x was still rate limited (HTTP 429)" in str(refused.value)
    assert str(gave_up) in str(refused.value)
    assert refused.value.__cause__ is gave_up
    assert isinstance(refused.value, contract.ReleaseContractError)  # so the CLIs exit 2


@pytest.mark.parametrize(
    "gave_up",
    [
        sdk_gave_up(TemporarilyUnavailable("503", retry_after_secs=1)),
        sdk_gave_up(ConnectionError("reset by peer")),
        TimeoutError("a socket timeout, not the SDK's retry"),
        RuntimeError("no cause at all"),
        contract.ReleaseContractError("already a refusal"),
    ],
    ids=["503-timed-out", "connection-timed-out", "bare-timeout", "bare-runtime", "refusal"],
)
def test_a_timeout_that_was_not_a_rate_limit_propagates_unchanged(gave_up: Exception) -> None:
    policy, waits = _policy()
    calls: list[int] = []

    with pytest.raises(type(gave_up)) as raised:
        policy.call("download of /x", _raising(gave_up, calls))

    assert raised.value is gave_up
    assert not isinstance(raised.value, retry.RateLimitExhausted)
    assert calls == [1] and waits == []


def test_a_refusal_caused_by_a_429_is_never_wrapped_twice() -> None:
    inner = retry.RateLimitExhausted("the upload of /x was rate limited")
    inner.__cause__ = ResourceExhausted("429", retry_after_secs=1)
    policy, _ = _policy()

    with pytest.raises(retry.RateLimitExhausted) as raised:
        policy.call("upload of /x", _raising(inner, []))

    assert raised.value is inner


# -- a real WorkspaceClient against a local server that throttles --------------------------


class _Server:
    """``throttled`` routes answer 429 RESOURCE_EXHAUSTED with Retry-After 60.

    Otherwise an import answers RESOURCE_ALREADY_EXISTS and an export serves
    ``files``; every request is counted in ``hits`` by route.
    """

    def __init__(self, throttled: set[str], files: dict[str, bytes]) -> None:
        self.throttled, self.files = throttled, files
        self.hits: dict[str, int] = {}
        self.lock = threading.Lock()

    def handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                url = urlsplit(self.path)
                route = f"{self.command} {url.path}"
                with server.lock:
                    server.hits[route] = server.hits.get(route, 0) + 1
                if route in server.throttled:
                    self._json(429, {"error_code": "RESOURCE_EXHAUSTED",
                                     "message": "Too many requests"}, retry_after="60")
                elif route == IMPORT:
                    self._json(400, {"error_code": "RESOURCE_ALREADY_EXISTS",
                                     "message": "already exists"})
                elif route == EXPORT and (path := parse_qs(url.query)["path"][0]) in server.files:
                    self._send(200, server.files[path], "application/octet-stream")
                else:  # host metadata and anything unexpected
                    self._json(404, {"error_code": "NOT_FOUND", "message": route})

            def _json(self, status: int, body: dict[str, str], retry_after: str = "") -> None:
                self._send(status, json.dumps(body).encode(), "application/json", retry_after)

            def _send(self, status: int, body: bytes, kind: str, retry_after: str = "") -> None:
                self.send_response(status)
                if retry_after:
                    self.send_header("Retry-After", retry_after)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer  # noqa: N815 -- http.server hooks

            def log_message(self, *_: object) -> None:
                return

        return Handler


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    """``workspace(throttled, files)`` -> (a real WorkspaceClient, its server)."""
    for name in [key for key in os.environ if key.startswith("DATABRICKS_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / "no-databrickscfg"))
    servers: list[ThreadingHTTPServer] = []

    def make(throttled: set[str], files: dict[str, bytes] | None = None) -> Any:
        state = _Server(throttled, files or {})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), state.handler())
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        config = Config(
            host=f"http://127.0.0.1:{httpd.server_address[1]}", token="not-a-real-token",
            auth_type="pat", clock=InstantClock(),
        )
        assert config.retry_timeout_seconds in (None, 300)  # the default under test
        return WorkspaceClient(config=config), state

    yield make
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _entry(path: str, data: bytes) -> contract.ManifestEntry:
    return contract.ManifestEntry(path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest())


def test_the_sdk_retries_a_throttled_download_itself_and_the_refusal_names_it(
    workspace: Any,
) -> None:
    client, server = workspace({EXPORT})
    policy, waits = _policy()

    with pytest.raises(retry.RateLimitExhausted) as refused:
        source.verify_hashes(client, ROOT, [_entry("app.yaml", b"a: 1\n")], workers=1, retry=policy)

    assert f"the download of {ROOT}/app.yaml was still rate limited (HTTP 429)" in str(refused.value)
    assert "Timed out after 0:05:00" in str(refused.value)
    assert server.hits[EXPORT] >= 2  # the SDK's own retries, at Retry-After 60
    assert waits == []  # and no second budget on top of its 300 s


def test_the_sdk_retries_a_throttled_listing_itself_and_the_refusal_names_it(
    workspace: Any,
) -> None:
    client, server = workspace({LIST})
    policy, waits = _policy()

    with pytest.raises(retry.RateLimitExhausted) as refused:
        source.remote_files(client, ROOT, retry=policy)

    assert f"the listing of {ROOT} was still rate limited (HTTP 429)" in str(refused.value)
    assert "Timed out after 0:05:00" in str(refused.value)
    assert server.hits[LIST] >= 2
    assert waits == []


def test_the_sdk_sends_a_throttled_import_once_so_the_budget_retries_it(
    workspace: Any,
) -> None:
    client, server = workspace({IMPORT})
    policy, waits = _policy()

    with pytest.raises(retry.RateLimitExhausted, match="on all 6 attempts"):
        source._upload_one(client, f"{ROOT}/app.yaml", b"a: 1\n", policy)

    assert server.hits[IMPORT] == 6  # one per attempt: the SDK never retries an import
    assert waits == [30.0] * 5  # Retry-After 60 beats each backoff and is cut to the cap
    assert EXPORT not in server.hits  # a spent budget is not reconciled


def test_an_existing_object_read_back_while_throttled_is_a_rate_limit_refusal(
    workspace: Any,
) -> None:
    client, server = workspace({EXPORT})
    policy, _ = _policy()

    with pytest.raises(retry.RateLimitExhausted) as refused:
        source._upload_one(client, f"{ROOT}/app.yaml", b"a: 1\n", policy)

    assert "cannot be read back" not in str(refused.value)
    assert f"the download of {ROOT}/app.yaml was still rate limited" in str(refused.value)
    assert server.hits[IMPORT] == 1 and server.hits[EXPORT] >= 2


def test_the_local_server_control_reuses_equal_bytes_through_the_real_client(
    workspace: Any,
) -> None:
    # Positive control: unthrottled, the same client and server reach the byte compare.
    client, server = workspace(set(), {f"{ROOT}/app.yaml": b"a: 1\n"})
    policy, waits = _policy()

    assert source._upload_one(client, f"{ROOT}/app.yaml", b"a: 1\n", policy) == "reused"
    assert (server.hits[IMPORT], server.hits[EXPORT], waits) == (1, 1, [])
