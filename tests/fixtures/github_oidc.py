"""Hermetic GitHub Actions OIDC deployer fixture for unit tests.

Installs a ``github-oidc`` Databricks CLI profile, synthetic Actions ID-token
request variables, and recording fakes for both HTTP calls (the GitHub ID-token
fetch and the Databricks token exchange). Host metadata and OIDC discovery are
answered in-process, so a real ``WorkspaceClient`` can be built without any
network access. Every value is synthetic.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from databricks.sdk import config as sdk_config
from databricks.sdk.oauth import HostMetadata, OidcEndpoints

from tools.databricks import github_oidc_auth

WORKSPACE_HOST = "https://oidc-unit.cloud.databricks.com"
TOKEN_ENDPOINT = f"{WORKSPACE_HOST}/oidc/v1/token"
DEPLOYER_CLIENT_ID = "00000000-0000-4000-8000-00000000d3a1"
REQUEST_URL = "https://actions-id-token.example.invalid/idtoken?api-version=2.0"
REQUEST_TOKEN = "synthetic-actions-request-token"
ISSUED_AT = datetime(2026, 10, 28, 12, tzinfo=UTC)


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@dataclass
class FakeOidcHttp:
    """Records both OIDC HTTP calls and answers them deterministically."""

    expires_in: object = 3600
    github_status: int = 200
    exchange_status: int = 200
    exchange_payload: object | None = None
    gets: list[dict[str, Any]] = field(default_factory=list)
    posts: list[dict[str, Any]] = field(default_factory=list)
    # The ``auth`` each call carried, in call order (``None`` when absent).
    auths: list[tuple[str, object]] = field(default_factory=list)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.auths.append(("get", kwargs.get("auth")))
        self.gets.append(
            {
                "url": url,
                "params": dict(kwargs["params"]),
                "authorization": kwargs["headers"]["Authorization"],
                "timeout": kwargs["timeout"],
            }
        )
        return FakeResponse(self.github_status, {"value": f"github-id-token-{len(self.gets)}"})

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.auths.append(("post", kwargs.get("auth")))
        self.posts.append({"url": url, **dict(kwargs["data"]), "timeout": kwargs["timeout"]})
        payload = self.exchange_payload
        if payload is None:
            payload = {
                "access_token": f"databricks-token-{len(self.posts)}",
                "token_type": "Bearer",
                "expires_in": self.expires_in,
            }
        return FakeResponse(self.exchange_status, payload)


class FakeClock:
    def __init__(self) -> None:
        self.now = ISSUED_AT

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class GitHubOidcDeployer:
    http: FakeOidcHttp
    clock: FakeClock
    config_file: Path


def write_profiles(path: Path, sections: dict[str, dict[str, str]]) -> Path:
    path.write_text(
        "".join(
            f"[{name}]\n" + "".join(f"{key} = {value}\n" for key, value in values.items())
            for name, values in sections.items()
        ),
        encoding="utf-8",
    )
    return path


def install_github_oidc_deployer(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    profile: str = "DEFAULT",
    bind_deployer_profile: bool = True,
) -> GitHubOidcDeployer:
    """Bind a synthetic ``github-oidc`` deployer the way deploy.sh binds a profile."""

    config_file = write_profiles(
        tmp_path / "databrickscfg",
        {
            profile: {
                "host": WORKSPACE_HOST,
                "auth_type": "github-oidc",
                "client_id": DEPLOYER_CLIENT_ID,
            }
        },
    )
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", profile)
    for name in (
        "MIP_DEPLOYER_DATABRICKS_HOST",
        "MIP_DEPLOYER_DATABRICKS_TOKEN",
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_AUTH_TYPE",
        "DATABRICKS_TOKEN_AUDIENCE",
        "DATABRICKS_DISCOVERY_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    if bind_deployer_profile:
        monkeypatch.setenv("MIP_DEPLOYER_DATABRICKS_PROFILE", profile)
    else:
        monkeypatch.delenv("MIP_DEPLOYER_DATABRICKS_PROFILE", raising=False)
    def refuse_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("github-oidc unit fixture attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse_network)
    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setenv(github_oidc_auth.REQUEST_URL_ENV, REQUEST_URL)
    monkeypatch.setenv(github_oidc_auth.REQUEST_TOKEN_ENV, REQUEST_TOKEN)
    http = FakeOidcHttp()
    clock = FakeClock()
    monkeypatch.setattr(github_oidc_auth, "requests", http)
    # The SDK's own github-oidc provider calls requests.get/post directly; route
    # it to the same fakes so a test can reproduce its per-request exchange and
    # its failure once the Actions request variables are scrubbed.
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, headers=None, **_kwargs: http.get(
            url, params={}, headers=headers or {}, timeout=None
        ),
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, data=None, **_kwargs: http.post(url, data=data or {}, timeout=None),
    )
    monkeypatch.setattr(github_oidc_auth, "_utcnow", clock)
    monkeypatch.setattr(
        sdk_config,
        "get_host_metadata",
        lambda host: HostMetadata(
            oidc_endpoint=f"{host}/oidc",
            workspace_id="1234567890",
            token_federation_default_oidc_audiences=[TOKEN_ENDPOINT],
        ),
    )
    monkeypatch.setattr(
        sdk_config,
        "get_endpoints_from_url",
        lambda _url: OidcEndpoints(
            authorization_endpoint=f"{WORKSPACE_HOST}/oidc/v1/authorize",
            token_endpoint=TOKEN_ENDPOINT,
        ),
    )
    return GitHubOidcDeployer(http=http, clock=clock, config_file=config_file)


@contextmanager
def restored_environ() -> Iterator[None]:
    """Restore os.environ exactly after a fail-closed scrub pops unrelated keys."""

    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def actions_keys(environ: Mapping[str, str]) -> list[str]:
    return sorted(name for name in environ if name.startswith("ACTIONS_"))
