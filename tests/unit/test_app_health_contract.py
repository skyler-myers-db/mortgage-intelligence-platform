from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tools.databricks.app_health_contract import (
    active_app_deployment_pin,
    authenticated_app_health,
    wait_for_authenticated_app_health,
)


def _workspace(url: str = "https://mip-app.example") -> object:
    return SimpleNamespace(apps=SimpleNamespace(get=lambda _name: SimpleNamespace(url=url)))


def _deployment_workspace(*, value: object = None, value_from: object = None) -> object:
    deployment_id = "deployment-green"
    deployment = SimpleNamespace(
        deployment_id=deployment_id,
        env_vars=[
            SimpleNamespace(
                name="MIP_APP_DEPLOYMENT_LEASE_ID",
                value=value,
                value_from=value_from,
            )
        ],
    )

    class _Apps:
        def get(self, _app_name: str) -> object:
            return SimpleNamespace(active_deployment=SimpleNamespace(deployment_id=deployment_id))

        def get_deployment(self, _app_name: str, _deployment_id: str) -> object:
            return deployment

    return SimpleNamespace(apps=_Apps())


def test_active_deployment_pin_binds_expected_redacted_lease() -> None:
    pin = active_app_deployment_pin(
        _deployment_workspace(),
        app_name="mip-app",
        expected_lease_id="12345678-1234-5678-1234-567812345678",
    )

    assert pin.deployment_id == "deployment-green"
    assert pin.lease_id == "12345678-1234-5678-1234-567812345678"


def test_active_deployment_pin_preserves_redacted_lease_for_health_discovery() -> None:
    pin = active_app_deployment_pin(
        _deployment_workspace(),
        app_name="mip-app",
    )

    assert pin.deployment_id == "deployment-green"
    assert pin.lease_id is None


def test_active_deployment_pin_rejects_nonliteral_lease_binding() -> None:
    with pytest.raises(RuntimeError, match="literal"):
        active_app_deployment_pin(
            _deployment_workspace(value_from="secret-resource"),
            app_name="mip-app",
            expected_lease_id="12345678-1234-5678-1234-567812345678",
        )


def test_authenticated_health_binds_workspace_url_and_forbids_redirects() -> None:
    calls: list[str] = []
    client = SimpleNamespace(
        get=lambda url, **_kwargs: calls.append(url)
        or SimpleNamespace(status_code=302, json=lambda: {}, text="redirect")
    )

    with pytest.raises(RuntimeError, match="redirects are forbidden"):
        authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            client=client,
        )

    assert calls == ["https://mip-app.example/api/health"]


def test_authenticated_health_rejects_mismatched_url_before_sending_token() -> None:
    calls: list[str] = []
    client = SimpleNamespace(get=lambda url, **_kwargs: calls.append(url))

    with pytest.raises(RuntimeError, match="does not match"):
        authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://attacker.example",
            bearer_token="secret-token",
            client=client,
        )

    assert calls == []


@pytest.mark.parametrize(
    "url",
    (
        "http://mip-app.example",
        "https://user@mip-app.example",
        "https://mip-app.example/path",
        "https://mip-app.example?next=evil",
    ),
)
def test_authenticated_health_rejects_noncanonical_workspace_url(url: str) -> None:
    with pytest.raises(RuntimeError, match="URL is invalid"):
        authenticated_app_health(
            _workspace(url),
            app_name="mip-app",
            base_url=url,
            bearer_token="secret-token",
            client=SimpleNamespace(get=lambda *_args, **_kwargs: None),
        )


def test_wait_for_authenticated_health_retries_transient_proxy_statuses() -> None:
    responses = [
        SimpleNamespace(status_code=502),
        SimpleNamespace(status_code=503),
        SimpleNamespace(status_code=200, json=lambda: {"status": "ok"}),
    ]
    now = [0.0]
    retries: list[tuple[int, str, float]] = []

    body = wait_for_authenticated_app_health(
        _workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="secret-token",
        timeout_s=10,
        interval_s=2,
        client=SimpleNamespace(get=lambda *_args, **_kwargs: responses.pop(0)),
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
        on_retry=lambda attempt, error, delay: retries.append((attempt, str(error), delay)),
    )

    assert body == {"status": "ok"}
    assert now == [4.0]
    assert retries == [
        (1, "authenticated App health returned HTTP 502; redirects are forbidden", 2),
        (2, "authenticated App health returned HTTP 503; redirects are forbidden", 2),
    ]


@pytest.mark.parametrize(
    "error_type",
    (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout),
)
def test_wait_for_authenticated_health_retries_transport_failure(
    error_type: type[httpx.TransportError],
) -> None:
    responses: list[object] = [
        error_type(
            "proxy not ready",
            request=httpx.Request("GET", "https://mip-app.example/api/health"),
        ),
        SimpleNamespace(status_code=200, json=lambda: {"status": "ok"}),
    ]
    now = [0.0]

    def get(*_args: object, **_kwargs: object) -> object:
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    assert wait_for_authenticated_app_health(
        _workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="secret-token",
        timeout_s=10,
        interval_s=1,
        client=SimpleNamespace(get=get),
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
    ) == {"status": "ok"}
    assert now == [1.0]


def test_wait_for_authenticated_health_does_not_retry_write_timeout() -> None:
    sleeps: list[float] = []
    error = httpx.WriteTimeout(
        "request could not be written",
        request=httpx.Request("GET", "https://mip-app.example/api/health"),
    )

    with pytest.raises(RuntimeError, match="request failed"):
        wait_for_authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            timeout_s=10,
            interval_s=1,
            client=SimpleNamespace(get=lambda *_args, **_kwargs: (_ for _ in ()).throw(error)),
            sleep=sleeps.append,
        )

    assert sleeps == []


@pytest.mark.parametrize("status_code", (302, 401, 403, 404, 429, 500, 504))
def test_wait_for_authenticated_health_does_not_retry_permanent_http_failure(
    status_code: int,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(status_code=status_code)

    with pytest.raises(RuntimeError, match=f"HTTP {status_code}"):
        wait_for_authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            timeout_s=10,
            interval_s=1,
            client=SimpleNamespace(get=get),
            sleep=sleeps.append,
        )

    assert calls == 1
    assert sleeps == []


def test_wait_for_authenticated_health_exhausts_bounded_timeout() -> None:
    calls = 0
    now = [0.0]

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(status_code=502)

    with pytest.raises(RuntimeError, match="within 2.5s after 3 attempt"):
        wait_for_authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            timeout_s=2.5,
            interval_s=1,
            client=SimpleNamespace(get=get),
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            monotonic=lambda: now[0],
        )

    assert calls == 3
    assert now == [2.5]


def test_wait_for_authenticated_health_does_not_retry_malformed_success() -> None:
    sleeps: list[float] = []
    client = SimpleNamespace(
        get=lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: (_ for _ in ()).throw(ValueError("not json")),
        )
    )

    with pytest.raises(RuntimeError, match="request failed"):
        wait_for_authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            timeout_s=10,
            interval_s=1,
            client=client,
            sleep=sleeps.append,
        )

    assert sleeps == []


@pytest.mark.parametrize("payload", ([], "ok", None))
def test_wait_for_authenticated_health_does_not_retry_non_object_success(
    payload: object,
) -> None:
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match="non-object"):
        wait_for_authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            timeout_s=10,
            interval_s=1,
            client=SimpleNamespace(
                get=lambda *_args, **_kwargs: SimpleNamespace(
                    status_code=200,
                    json=lambda: payload,
                )
            ),
            sleep=sleeps.append,
        )

    assert sleeps == []


@pytest.mark.parametrize(
    "overrides",
    (
        {"timeout_s": float("nan")},
        {"timeout_s": float("inf")},
        {"timeout_s": -1},
        {"timeout_s": 121},
        {"interval_s": float("nan")},
        {"interval_s": float("inf")},
        {"interval_s": 0},
        {"interval_s": -1},
        {"interval_s": 121},
        {"request_timeout_s": float("nan")},
        {"request_timeout_s": float("inf")},
        {"request_timeout_s": 0},
        {"request_timeout_s": -1},
        {"request_timeout_s": 16},
    ),
)
def test_wait_for_authenticated_health_rejects_unbounded_settings_before_network(
    overrides: dict[str, float],
) -> None:
    calls = 0

    def get(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

    kwargs = {
        "timeout_s": 10.0,
        "interval_s": 1.0,
        "request_timeout_s": 2.0,
        **overrides,
    }
    with pytest.raises(ValueError):
        wait_for_authenticated_app_health(
            _workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="secret-token",
            client=SimpleNamespace(get=get),
            **kwargs,
        )

    assert calls == 0


def test_wait_for_authenticated_health_reuses_and_closes_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        SimpleNamespace(status_code=502),
        SimpleNamespace(status_code=200, json=lambda: {"status": "ok"}),
    ]
    now = [0.0]
    constructor_kwargs: list[dict[str, object]] = []
    request_timeouts: list[float] = []
    closes = 0

    class _Client:
        def get(self, *_args: object, **kwargs: object) -> object:
            request_timeouts.append(float(kwargs["timeout"]))
            return responses.pop(0)

        def close(self) -> None:
            nonlocal closes
            closes += 1

    def client_factory(**kwargs: object) -> object:
        constructor_kwargs.append(kwargs)
        return _Client()

    monkeypatch.setattr(
        "tools.databricks.app_health_contract.httpx.Client",
        client_factory,
    )

    assert wait_for_authenticated_app_health(
        _workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="secret-token",
        timeout_s=3,
        interval_s=2.5,
        request_timeout_s=2,
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
    ) == {"status": "ok"}

    assert constructor_kwargs == [{"timeout": 2, "follow_redirects": False}]
    assert request_timeouts == [2.0, 0.5]
    assert closes == 1


def test_wait_for_authenticated_health_never_closes_injected_client() -> None:
    closes = 0

    class _Client:
        def get(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(status_code=200, json=lambda: {"status": "ok"})

        def close(self) -> None:
            nonlocal closes
            closes += 1

    assert wait_for_authenticated_app_health(
        _workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="secret-token",
        timeout_s=1,
        interval_s=1,
        client=_Client(),
    ) == {"status": "ok"}
    assert closes == 0
