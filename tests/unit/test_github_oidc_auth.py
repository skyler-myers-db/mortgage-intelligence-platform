"""Cached github-oidc deployer auth: one exchange per token lifetime, env-scrub safe."""

from __future__ import annotations

import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.oauth import IgnoreNetrcAuth

from tests.fixtures.github_oidc import (
    DEPLOYER_CLIENT_ID,
    REQUEST_TOKEN,
    REQUEST_URL,
    TOKEN_ENDPOINT,
    WORKSPACE_HOST,
    FakeClock,
    FakeOidcHttp,
    install_github_oidc_deployer,
    write_profiles,
)
from tools.databricks import github_oidc_auth
from tools.databricks.github_oidc_auth import CachedGitHubOidcCredentials
from tools.databricks.workspace_auth import deployment_workspace_client


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unit test attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _recording_factory(calls: list[dict[str, Any]]) -> Any:
    def build(**kwargs: Any) -> object:
        calls.append(kwargs)
        return object()

    return build


def _cfg(**overrides: object) -> Any:
    values: dict[str, object] = {
        "host": WORKSPACE_HOST,
        "client_id": DEPLOYER_CLIENT_ID,
        "token_audience": None,
        "authorization_details": None,
        "databricks_oidc_endpoints": SimpleNamespace(token_endpoint=TOKEN_ENDPOINT),
        "get_scopes_as_string": lambda: "all-apis",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _strategy(http: FakeOidcHttp, clock: FakeClock | None = None) -> CachedGitHubOidcCredentials:
    return CachedGitHubOidcCredentials(
        environ={
            github_oidc_auth.REQUEST_URL_ENV: REQUEST_URL,
            github_oidc_auth.REQUEST_TOKEN_ENV: REQUEST_TOKEN,
        },
        http=http,
        now=clock or FakeClock(),
    )


def test_github_oidc_profile_binds_cached_strategy_to_the_profile_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_github_oidc_deployer(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "normal-app-client")
    monkeypatch.setenv("DATABRICKS_HOST", "https://hostile.cloud.databricks.com")
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))

    assert len(calls) == 1
    strategy = calls[0].pop("credentials_strategy")
    assert isinstance(strategy, CachedGitHubOidcCredentials)
    assert calls == [
        {
            "profile": "DEFAULT",
            "host": WORKSPACE_HOST,
            "client_id": DEPLOYER_CLIENT_ID,
            "auth_type": "github-oidc",
        }
    ]
    assert REQUEST_TOKEN not in repr(strategy)


def test_n_requests_across_workers_make_one_github_fetch_and_one_exchange(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    deployer = install_github_oidc_deployer(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "normal-app-client")
    client = deployment_workspace_client()
    # The GitHub fetch holds until a second worker overlaps it (or 0.5 s
    # passes), so an unserialised cache miss is observed, not raced past.
    overlap = threading.Event()
    inside = [0]
    counter = threading.Lock()
    fetch = deployer.http.get

    def overlapping_get(url: str, **kwargs: Any) -> Any:
        with counter:
            inside[0] += 1
            if inside[0] > 1:
                overlap.set()
        overlap.wait(timeout=0.5)
        try:
            return fetch(url, **kwargs)
        finally:
            with counter:
                inside[0] -= 1

    monkeypatch.setattr(deployer.http, "get", overlapping_get)

    with ThreadPoolExecutor(max_workers=8) as pool:
        headers = list(pool.map(lambda _i: client.config.authenticate(), range(64)))

    assert not overlap.is_set()

    assert client.config.auth_type == "github-oidc"
    assert {h["Authorization"] for h in headers} == {"Bearer databricks-token-1"}
    assert deployer.http.gets == [
        {
            "url": REQUEST_URL,
            "params": {"audience": TOKEN_ENDPOINT},
            "authorization": f"Bearer {REQUEST_TOKEN}",
            "timeout": 30,
        }
    ]
    assert deployer.http.posts == [
        {
            "url": TOKEN_ENDPOINT,
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "subject_token": "github-id-token-1",
            "client_id": DEPLOYER_CLIENT_ID,
            "scope": "all-apis",
            "timeout": 30,
        }
    ]
    assert client.config.oauth_token().access_token == "databricks-token-1"


def test_client_keeps_authenticating_and_refreshing_after_actions_env_is_gone(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    deployer = install_github_oidc_deployer(monkeypatch, tmp_path)
    client = deployment_workspace_client()
    for name in list(os.environ):
        if name.startswith("ACTIONS_"):
            monkeypatch.delenv(name)

    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-1"}
    deployer.clock.advance(3600 - 300 - 1)
    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-1"}
    deployer.clock.advance(1)
    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-2"}

    assert [call["authorization"] for call in deployer.http.gets] == [
        f"Bearer {REQUEST_TOKEN}",
        f"Bearer {REQUEST_TOKEN}",
    ]
    assert {call["url"] for call in deployer.http.gets} == {REQUEST_URL}
    assert [call["subject_token"] for call in deployer.http.posts] == [
        "github-id-token-1",
        "github-id-token-2",
    ]


@pytest.mark.parametrize("missing", [github_oidc_auth.REQUEST_URL_ENV, github_oidc_auth.REQUEST_TOKEN_ENV])
def test_missing_actions_request_at_construction_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
) -> None:
    deployer = install_github_oidc_deployer(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError, match="id-token: write"):
        deployment_workspace_client(factory=lambda **_kwargs: pytest.fail("client built"))

    assert deployer.http.gets == [] and deployer.http.posts == []


@pytest.mark.parametrize(
    "request_url",
    [
        "http://actions.example.invalid/x?y=1",
        "https://user:pass@actions.example.invalid/x?y=1",
        "https://user@actions.example.invalid/x?y=1",
        "https://:pass@actions.example.invalid/x?y=1",
        "https://@actions.example.invalid/x?y=1",
    ],
)
def test_non_https_or_userinfo_actions_request_url_fails_closed(request_url: str) -> None:
    with pytest.raises(RuntimeError, match="not an HTTPS URL"):
        CachedGitHubOidcCredentials(
            environ={
                github_oidc_auth.REQUEST_URL_ENV: request_url,
                github_oidc_auth.REQUEST_TOKEN_ENV: REQUEST_TOKEN,
            },
            http=FakeOidcHttp(),
        )


def test_userinfo_token_endpoint_fails_closed_before_any_exchange() -> None:
    http = FakeOidcHttp()
    endpoints = SimpleNamespace(token_endpoint="https://user@oidc-unit.example.invalid/token")

    with pytest.raises(RuntimeError, match="not an HTTPS URL"):
        _strategy(http)(_cfg(databricks_oidc_endpoints=endpoints))

    assert http.gets == [] and http.posts == []


def test_unbound_ambient_github_oidc_profile_also_gets_the_cached_strategy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_github_oidc_deployer(monkeypatch, tmp_path, bind_deployer_profile=False)
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))

    assert isinstance(calls[0].pop("credentials_strategy"), CachedGitHubOidcCredentials)
    assert calls == [
        {
            "profile": "DEFAULT",
            "host": WORKSPACE_HOST,
            "client_id": DEPLOYER_CLIENT_ID,
            "auth_type": "github-oidc",
        }
    ]


@pytest.mark.parametrize("bind_deployer_profile", [True, False])
def test_pat_and_oauth_profiles_keep_their_existing_construction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bind_deployer_profile: bool,
) -> None:
    install_github_oidc_deployer(monkeypatch, tmp_path, bind_deployer_profile=bind_deployer_profile)
    write_profiles(
        tmp_path / "databrickscfg",
        {
            "DEFAULT": {"host": WORKSPACE_HOST, "token": "operator-pat"},
            "oauth": {"host": WORKSPACE_HOST, "auth_type": "databricks-cli"},
        },
    )
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))
    monkeypatch.setenv(
        "MIP_DEPLOYER_DATABRICKS_PROFILE" if bind_deployer_profile else "DATABRICKS_CONFIG_PROFILE",
        "oauth",
    )
    deployment_workspace_client(factory=_recording_factory(calls))

    expected = (
        [{"profile": "DEFAULT"}, {"profile": "oauth"}] if bind_deployer_profile else [{}, {}]
    )
    assert calls == expected


def test_named_profile_does_not_inherit_github_oidc_from_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_github_oidc_deployer(monkeypatch, tmp_path)
    path = tmp_path / "databrickscfg"
    path.write_text(
        path.read_text(encoding="utf-8") + f"[operator]\nhost = {WORKSPACE_HOST}\ntoken = pat\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", "operator")
    calls: list[dict[str, Any]] = []

    deployment_workspace_client(factory=_recording_factory(calls))

    assert calls == [{"profile": "operator"}]


@pytest.mark.parametrize("missing", ["host", "client_id"])
def test_github_oidc_profile_without_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing: str,
) -> None:
    install_github_oidc_deployer(monkeypatch, tmp_path)
    section = {"host": WORKSPACE_HOST, "auth_type": "github-oidc", "client_id": DEPLOYER_CLIENT_ID}
    del section[missing]
    write_profiles(tmp_path / "databrickscfg", {"DEFAULT": section})

    with pytest.raises(RuntimeError, match="requires host and client_id"):
        deployment_workspace_client(factory=lambda **_kwargs: pytest.fail("client built"))


def test_cache_refreshes_at_expiry_minus_skew_and_halves_skew_for_short_tokens() -> None:
    http = FakeOidcHttp(expires_in=120)
    clock = FakeClock()
    provider = _strategy(http, clock)(_cfg())

    assert provider() == {"Authorization": "Bearer databricks-token-1"}
    clock.advance(59)
    assert provider() == {"Authorization": "Bearer databricks-token-1"}
    clock.advance(1)
    assert provider() == {"Authorization": "Bearer databricks-token-2"}
    assert len(http.gets) == len(http.posts) == 2


def test_explicit_token_audience_and_authorization_details_reach_the_exchange() -> None:
    http = FakeOidcHttp()
    provider = _strategy(http)(
        _cfg(token_audience="account-audience", authorization_details='[{"type":"x"}]')
    )

    provider()

    assert http.gets[0]["params"] == {"audience": "account-audience"}
    assert http.posts[0]["authorization_details"] == '[{"type":"x"}]'


@pytest.mark.parametrize(
    "cfg_overrides",
    [
        {"client_id": ""},
        {"host": ""},
        {"databricks_oidc_endpoints": SimpleNamespace(token_endpoint="")},
        {"databricks_oidc_endpoints": SimpleNamespace(token_endpoint="http://plain/token")},
    ],
)
def test_incomplete_config_fails_closed_before_any_exchange(cfg_overrides: dict[str, object]) -> None:
    http = FakeOidcHttp()

    with pytest.raises((ValueError, RuntimeError)):
        _strategy(http)(_cfg(**cfg_overrides))

    assert http.gets == [] and http.posts == []


def test_github_refusal_fails_closed_without_exchanging() -> None:
    http = FakeOidcHttp(github_status=403)
    provider = _strategy(http)(_cfg())

    with pytest.raises(RuntimeError, match=r"ID-token request was refused \(HTTP 403\)"):
        provider()

    assert http.posts == []


def test_exchange_refusal_reports_the_oauth_error_code_and_caches_nothing() -> None:
    http = FakeOidcHttp(
        exchange_status=400,
        exchange_payload={"error": "invalid_request", "error_description": "no federation policy"},
    )
    provider = _strategy(http)(_cfg())

    for _ in range(2):
        with pytest.raises(RuntimeError) as raised:
            provider()
        assert "HTTP 400" in str(raised.value)
        assert "invalid_request no federation policy" in str(raised.value)
        assert "github-id-token" not in str(raised.value)

    assert len(http.posts) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "", "token_type": "Bearer", "expires_in": 3600},
        {"access_token": "t", "token_type": "", "expires_in": 3600},
        {"access_token": "t", "token_type": "Bearer", "expires_in": 0},
        {"access_token": "t", "token_type": "Bearer", "expires_in": True},
        {"access_token": "t", "token_type": "Bearer", "expires_in": "-5"},
        {"access_token": "t", "token_type": "Bearer"},
    ],
)
def test_malformed_exchange_response_fails_closed(payload: dict[str, object]) -> None:
    http = FakeOidcHttp(exchange_payload=payload)

    with pytest.raises(RuntimeError, match="malformed token"):
        _strategy(http)(_cfg())()


@pytest.mark.parametrize("failing_leg", ["github_status", "exchange_status"])
@pytest.mark.parametrize("status", [429, 503])
def test_failed_early_refresh_keeps_serving_the_still_valid_token_with_a_retry_backoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failing_leg: str,
    status: int,
) -> None:
    deployer = install_github_oidc_deployer(monkeypatch, tmp_path)
    client = deployment_workspace_client()
    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-1"}

    setattr(deployer.http, failing_leg, status)
    deployer.clock.advance(3600 - 300)
    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-1"}
    attempts = len(deployer.http.gets)
    deployer.clock.advance(29)
    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-1"}
    assert len(deployer.http.gets) == attempts
    deployer.clock.advance(1)
    assert client.config.authenticate() == {"Authorization": "Bearer databricks-token-1"}
    assert len(deployer.http.gets) == attempts + 1

    setattr(deployer.http, failing_leg, 200)
    deployer.clock.advance(30)
    fresh = client.config.authenticate()
    assert fresh != {"Authorization": "Bearer databricks-token-1"}
    assert fresh == {"Authorization": f"Bearer databricks-token-{len(deployer.http.posts)}"}
    deployer.clock.advance(3600 - 300 - 1)
    assert client.config.authenticate() == fresh


@pytest.mark.parametrize("failing_leg", ["github_status", "exchange_status"])
def test_refresh_failure_once_the_cached_token_is_unusable_raises(
    failing_leg: str,
) -> None:
    http = FakeOidcHttp()
    clock = FakeClock()
    provider = _strategy(http, clock)(_cfg())
    assert provider() == {"Authorization": "Bearer databricks-token-1"}

    setattr(http, failing_leg, 503)
    clock.advance(3600 - 30 - 1)
    assert provider() == {"Authorization": "Bearer databricks-token-1"}
    clock.advance(1)
    with pytest.raises(RuntimeError, match=r"refused \(HTTP 503\)"):
        provider()
    with pytest.raises(RuntimeError, match=r"refused \(HTTP 503\)"):
        provider()


def test_short_token_keeps_a_proportional_usable_window_after_a_failed_refresh() -> None:
    http = FakeOidcHttp(expires_in=60)
    clock = FakeClock()
    provider = _strategy(http, clock)(_cfg())
    provider()

    http.exchange_status = 503
    clock.advance(30)
    assert provider() == {"Authorization": "Bearer databricks-token-1"}
    clock.advance(14)
    assert provider() == {"Authorization": "Bearer databricks-token-1"}
    clock.advance(1)
    with pytest.raises(RuntimeError, match=r"HTTP 503"):
        provider()


def test_a_slow_failed_refresh_is_judged_by_the_clock_after_it_returns() -> None:
    clock = FakeClock()

    class SlowRefusal(FakeOidcHttp):
        def post(self, url: str, **kwargs: Any) -> Any:
            response = super().post(url, **kwargs)
            if len(self.posts) > 1:
                clock.advance(kwargs["timeout"])
            return response

    http = SlowRefusal()
    provider = _strategy(http, clock)(_cfg())
    provider()

    http.exchange_status = 504
    clock.advance(3600 - 30 - 1)
    with pytest.raises(RuntimeError, match=r"HTTP 504"):
        provider()


@pytest.mark.parametrize("token_audience", [None, "", "  "])
def test_unset_token_audience_requests_the_github_token_for_the_token_endpoint(
    token_audience: str | None,
) -> None:
    http = FakeOidcHttp()

    _strategy(http)(_cfg(token_audience=token_audience))()

    assert [call["params"] for call in http.gets] == [{"audience": TOKEN_ENDPOINT}]


def test_both_oidc_calls_ignore_a_runner_netrc() -> None:
    http = FakeOidcHttp()

    _strategy(http)(_cfg())()

    assert [call for call, _auth in http.auths] == ["get", "post"]
    assert all(isinstance(auth, IgnoreNetrcAuth) for _call, auth in http.auths)


def test_scopes_authorization_details_and_endpoint_each_key_their_own_token() -> None:
    # One explicit audience for every config, so only the remaining key field
    # differs between them and a key that ignores it would reuse token 1.
    http = FakeOidcHttp()
    strategy = _strategy(http)
    other_endpoint = "https://other-workspace.example.invalid/oidc/v1/token"
    base = strategy(_cfg(token_audience="fixed-aud"))
    sql_scope = strategy(_cfg(token_audience="fixed-aud", get_scopes_as_string=lambda: "sql"))
    detailed = strategy(_cfg(token_audience="fixed-aud", authorization_details='[{"type":"x"}]'))
    elsewhere = strategy(
        _cfg(
            token_audience="fixed-aud",
            databricks_oidc_endpoints=SimpleNamespace(token_endpoint=other_endpoint),
        )
    )

    assert base() == {"Authorization": "Bearer databricks-token-1"}
    assert sql_scope() == {"Authorization": "Bearer databricks-token-2"}
    assert detailed() == {"Authorization": "Bearer databricks-token-3"}
    assert elsewhere() == {"Authorization": "Bearer databricks-token-4"}
    assert base() == {"Authorization": "Bearer databricks-token-1"}
    assert elsewhere() == {"Authorization": "Bearer databricks-token-4"}
    assert len(http.posts) == 4


def test_each_exchange_target_keeps_its_own_cached_token() -> None:
    http = FakeOidcHttp()
    strategy = _strategy(http)
    deployer = strategy(_cfg())
    other_client = strategy(_cfg(client_id="00000000-0000-4000-8000-0000000000b2"))
    other_audience = strategy(_cfg(token_audience="other-audience"))

    assert deployer() == {"Authorization": "Bearer databricks-token-1"}
    assert other_client() == {"Authorization": "Bearer databricks-token-2"}
    assert other_audience() == {"Authorization": "Bearer databricks-token-3"}
    assert deployer() == {"Authorization": "Bearer databricks-token-1"}
    assert other_client() == {"Authorization": "Bearer databricks-token-2"}
    assert [post["client_id"] for post in http.posts] == [
        DEPLOYER_CLIENT_ID,
        "00000000-0000-4000-8000-0000000000b2",
        DEPLOYER_CLIENT_ID,
    ]
    assert [call["params"]["audience"] for call in http.gets] == [
        TOKEN_ENDPOINT,
        TOKEN_ENDPOINT,
        "other-audience",
    ]
