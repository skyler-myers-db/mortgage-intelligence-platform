from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from tools.verify_app_agent_green_path import validate_green_response, verify

_LEASE_ID = "12345678-1234-5678-1234-567812345678"


class _Apps:
    def __init__(self) -> None:
        self.active_id = "deployment-green"
        self.url = "https://mip-app.example"

    def get(self, _app_name: str) -> object:
        return SimpleNamespace(
            url=self.url,
            active_deployment=SimpleNamespace(deployment_id=self.active_id),
        )

    def get_deployment(self, _app_name: str, deployment_id: str) -> object:
        return SimpleNamespace(
            deployment_id=deployment_id,
            env_vars=[
                SimpleNamespace(
                    name="MIP_APP_DEPLOYMENT_LEASE_ID",
                    value=_LEASE_ID,
                    value_from=None,
                )
            ],
        )


def _workspace() -> object:
    return SimpleNamespace(apps=_Apps())


def _body() -> dict[str, object]:
    return {
        "execution_mode": "agent_framework",
        "trace_kind": "agent_framework",
        "genie_trusted_assets": ["databricks.serving_endpoint.mip-growth-agent-gateway"],
        "tool_steps": [
            {
                "tool_name": "fn_build_cohort",
                "status": "completed",
                "result_hash": "sha256:abc",
            }
        ],
    }


def test_accepts_functional_app_gateway_and_tool_path() -> None:
    validate_green_response(
        _body(),
        expected_endpoint="mip-growth-agent-gateway",
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("execution_mode", "deterministic", "fell back"),
        ("genie_trusted_assets", [], "expected Gateway"),
        ("tool_steps", [], "fn_build_cohort"),
    ],
)
def test_rejects_nonfunctional_or_fallback_path(
    field: str,
    value: object,
    message: str,
) -> None:
    body = _body()
    body[field] = value

    with pytest.raises(RuntimeError, match=message):
        validate_green_response(
            body,
            expected_endpoint="mip-growth-agent-gateway",
        )


def test_green_probe_retries_same_idempotent_request_after_transient_status() -> None:
    responses = [
        SimpleNamespace(status_code=502),
        SimpleNamespace(status_code=200, json=_body),
    ]
    requests: list[dict[str, object]] = []
    now = [0.0]

    def post(_url: str, **kwargs: object) -> object:
        requests.append(
            {
                "headers": dict(kwargs["headers"]),  # type: ignore[arg-type]
                "json": dict(kwargs["json"]),  # type: ignore[arg-type]
            }
        )
        return responses.pop(0)

    verify(
        workspace=_workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="release-probe-token",
        expected_endpoint="mip-growth-agent-gateway",
        expected_deployment_lease_id=_LEASE_ID,
        client=SimpleNamespace(post=post),
        timeout_s=10,
        interval_s=1,
        request_timeout_s=2,
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
    )

    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert requests[0]["headers"] == {
        "Authorization": "Bearer release-probe-token",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert requests[0]["json"]["save_monitor"] is False  # type: ignore[index]
    assert now == [1.0]


@pytest.mark.parametrize(
    "error_type",
    (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout),
)
def test_green_probe_retries_ambiguous_transport_with_same_request_id(
    error_type: type[httpx.TransportError],
) -> None:
    requests: list[dict[str, object]] = []
    responses: list[object] = [
        error_type(
            "ambiguous response",
            request=httpx.Request("POST", "https://mip-app.example/api/growth-agent/agent/run"),
        ),
        SimpleNamespace(status_code=200, json=_body),
    ]
    now = [0.0]

    def post(_url: str, **kwargs: object) -> object:
        requests.append(dict(kwargs["json"]))  # type: ignore[arg-type]
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    verify(
        workspace=_workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="release-probe-token",
        expected_endpoint="mip-growth-agent-gateway",
        expected_deployment_lease_id=_LEASE_ID,
        client=SimpleNamespace(post=post),
        timeout_s=10,
        interval_s=1,
        request_timeout_s=2,
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
    )

    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert requests[0]["request_id"] == requests[1]["request_id"]


@pytest.mark.parametrize("status_code", (302, 401, 403, 404, 409, 429, 500, 504))
def test_green_probe_does_not_retry_terminal_status(status_code: int) -> None:
    calls = 0
    sleeps: list[float] = []

    def post(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(status_code=status_code)

    with pytest.raises(RuntimeError, match=f"HTTP {status_code}"):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=post),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=sleeps.append,
        )

    assert calls == 1
    assert sleeps == []


def test_green_probe_does_not_retry_semantic_failure() -> None:
    sleeps: list[float] = []
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {**_body(), "execution_mode": "deterministic"},
    )

    with pytest.raises(RuntimeError, match="fell back"):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=lambda *_args, **_kwargs: response),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_green_probe_rejects_deployment_drift_before_retry() -> None:
    workspace = _workspace()
    sleeps: list[float] = []

    def post(*_args: object, **_kwargs: object) -> object:
        workspace.apps.active_id = "deployment-other"
        return SimpleNamespace(status_code=502)

    with pytest.raises(RuntimeError, match="changed during proof"):
        verify(
            workspace=workspace,
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=post),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_green_probe_rejects_expected_lease_before_http() -> None:
    calls = 0

    def post(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(status_code=200, json=_body)

    with pytest.raises(RuntimeError, match="expected deployment lease"):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id="87654321-4321-8765-4321-876543218765",
            client=SimpleNamespace(post=post),
        )

    assert calls == 0


def test_green_probe_exhausts_bounded_deadline() -> None:
    calls = 0
    payloads: list[dict[str, object]] = []
    now = [0.0]

    def post(*_args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        payloads.append(dict(kwargs["json"]))  # type: ignore[arg-type]
        return SimpleNamespace(status_code=502)

    with pytest.raises(RuntimeError, match="within 2.5s after 3 attempt"):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=post),
            timeout_s=2.5,
            interval_s=1,
            request_timeout_s=2,
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            monotonic=lambda: now[0],
        )

    assert calls == 3
    assert payloads[0] == payloads[1] == payloads[2]
    assert now == [2.5]


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ([], "non-object"),
        ("ok", "non-object"),
        (None, "non-object"),
    ),
)
def test_green_probe_does_not_retry_non_object_success(
    payload: object,
    message: str,
) -> None:
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match=message):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(
                post=lambda *_args, **_kwargs: SimpleNamespace(
                    status_code=200,
                    json=lambda: payload,
                )
            ),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_green_probe_does_not_retry_malformed_json() -> None:
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match="malformed JSON"):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(
                post=lambda *_args, **_kwargs: SimpleNamespace(
                    status_code=200,
                    json=lambda: (_ for _ in ()).throw(ValueError("invalid")),
                )
            ),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=sleeps.append,
        )

    assert sleeps == []


@pytest.mark.parametrize(
    "overrides",
    (
        {"timeout_s": float("nan")},
        {"timeout_s": float("inf")},
        {"timeout_s": -1},
        {"timeout_s": 301},
        {"interval_s": float("nan")},
        {"interval_s": float("inf")},
        {"interval_s": 0},
        {"interval_s": -1},
        {"interval_s": 301},
        {"request_timeout_s": float("nan")},
        {"request_timeout_s": float("inf")},
        {"request_timeout_s": 0},
        {"request_timeout_s": -1},
        {"request_timeout_s": 121},
    ),
)
def test_green_probe_rejects_unbounded_settings_before_http(
    overrides: dict[str, float],
) -> None:
    calls = 0

    def post(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(status_code=200, json=_body)

    kwargs = {
        "timeout_s": 10.0,
        "interval_s": 1.0,
        "request_timeout_s": 2.0,
        **overrides,
    }
    with pytest.raises(ValueError):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=post),
            **kwargs,
        )

    assert calls == 0


def test_green_probe_reuses_and_closes_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        SimpleNamespace(status_code=502),
        SimpleNamespace(status_code=200, json=_body),
    ]
    now = [0.0]
    constructor_kwargs: list[dict[str, object]] = []
    request_timeouts: list[float] = []
    closes = 0

    class _Client:
        def post(self, *_args: object, **kwargs: object) -> object:
            request_timeouts.append(float(kwargs["timeout"]))
            return responses.pop(0)

        def close(self) -> None:
            nonlocal closes
            closes += 1

    def client_factory(**kwargs: object) -> object:
        constructor_kwargs.append(kwargs)
        return _Client()

    monkeypatch.setattr(
        "tools.verify_app_agent_green_path.httpx.Client",
        client_factory,
    )

    verify(
        workspace=_workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="release-probe-token",
        expected_endpoint="mip-growth-agent-gateway",
        expected_deployment_lease_id=_LEASE_ID,
        timeout_s=3,
        interval_s=2.5,
        request_timeout_s=2,
        sleep=lambda delay: now.__setitem__(0, now[0] + delay),
        monotonic=lambda: now[0],
    )

    assert constructor_kwargs == [{"timeout": 2, "follow_redirects": False}]
    assert request_timeouts == [2.0, 0.5]
    assert closes == 1


def test_green_probe_never_closes_injected_client() -> None:
    closes = 0

    class _Client:
        def post(self, *_args: object, **_kwargs: object) -> object:
            return SimpleNamespace(status_code=200, json=_body)

        def close(self) -> None:
            nonlocal closes
            closes += 1

    verify(
        workspace=_workspace(),
        app_name="mip-app",
        base_url="https://mip-app.example",
        bearer_token="release-probe-token",
        expected_endpoint="mip-growth-agent-gateway",
        expected_deployment_lease_id=_LEASE_ID,
        client=_Client(),
    )
    assert closes == 0


def test_green_probe_does_not_retry_write_timeout() -> None:
    sleeps: list[float] = []
    error = httpx.WriteTimeout(
        "request body could not be written",
        request=httpx.Request("POST", "https://mip-app.example/api/growth-agent/agent/run"),
    )

    with pytest.raises(RuntimeError, match="permanently"):
        verify(
            workspace=_workspace(),
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=lambda *_args, **_kwargs: (_ for _ in ()).throw(error)),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=sleeps.append,
        )

    assert sleeps == []


def test_green_probe_rejects_canonical_url_drift_before_retry() -> None:
    workspace = _workspace()
    calls = 0
    now = [0.0]

    def post(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        workspace.apps.url = "https://other-app.example"
        return SimpleNamespace(status_code=502)

    with pytest.raises(RuntimeError, match="does not match"):
        verify(
            workspace=workspace,
            app_name="mip-app",
            base_url="https://mip-app.example",
            bearer_token="release-probe-token",
            expected_endpoint="mip-growth-agent-gateway",
            expected_deployment_lease_id=_LEASE_ID,
            client=SimpleNamespace(post=post),
            timeout_s=10,
            interval_s=1,
            request_timeout_s=2,
            sleep=lambda delay: now.__setitem__(0, now[0] + delay),
            monotonic=lambda: now[0],
        )

    assert calls == 1
    assert now == [1.0]
