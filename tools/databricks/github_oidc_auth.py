"""GitHub Actions OIDC deployer credentials that survive ambient-auth scrubs.

databricks-sdk's own ``github-oidc`` strategy (0.103.0,
``credentials_provider._oidc_credentials_provider``) re-reads
``ACTIONS_ID_TOKEN_REQUEST_URL``/``ACTIONS_ID_TOKEN_REQUEST_TOKEN`` from
``os.environ``, fetches a new GitHub ID token, re-reads the OIDC discovery
document and performs a new token exchange on every API request. That costs
three extra HTTP calls per request on the hot deploy paths (none retried on a
429 or 5xx), and the client stops authenticating as soon as a fail-closed
scrub (``m2m_workspace_auth.bind_exact_workspace_m2m_auth`` pops every
``*TOKEN*`` variable) removes the Actions request variables.

``CachedGitHubOidcCredentials`` snapshots the Actions request coordinates once,
when the deployer client is constructed, and caches each exchanged Databricks
token until shortly before it expires. A missing snapshot fails closed at
construction. The snapshot is the job-scoped Actions request token, which
GitHub invalidates when the job ends; no Databricks secret exists in this flow.

The early refresh is an optimisation, not a validity boundary: when it fails,
the cached token keeps serving until just before its own expiry, with failed
refreshes spaced by a short backoff (the SDK's ``Refreshable`` stale window
does the same). Only a refresh that fails once the cached token is no longer
usable raises, so one transient token-endpoint error cannot abort a deploy
whose lease heartbeat renews for hours.
"""

from __future__ import annotations

import configparser
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from databricks.sdk.credentials_provider import (
    CredentialsProvider,
    CredentialsStrategy,
    OAuthCredentialsProvider,
)
from databricks.sdk.oauth import IgnoreNetrcAuth, Token

AUTH_TYPE = "github-oidc"
REQUEST_URL_ENV = "ACTIONS_ID_TOKEN_REQUEST_URL"
REQUEST_TOKEN_ENV = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
DEFAULT_REFRESH_SKEW = timedelta(minutes=5)
# A cached token stops serving this long before its expiry (halved again for
# short-lived tokens), so a request never leaves with an about-to-expire token.
_EXPIRY_MARGIN = timedelta(seconds=30)
# After a failed early refresh, the next attempt waits at least this long.
_REFRESH_RETRY_BACKOFF = timedelta(seconds=30)
_TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
_JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
_HTTP_TIMEOUT_SECONDS = 30
# A sentinel default section keeps ``DEFAULT`` an ordinary section, so a named
# profile never inherits DEFAULT keys: the SDK reads profiles the same way.
_NO_DEFAULT_SECTION = "\x00mip-no-default-section"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _https_url(value: str, *, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError(f"{label} is not an HTTPS URL")
    return value


def profile_section(profile: str) -> dict[str, str]:
    """Return one Databricks CLI profile's own keys, exactly as the SDK reads them."""

    path = Path(os.environ.get("DATABRICKS_CONFIG_FILE") or "~/.databrickscfg").expanduser()
    parser = configparser.ConfigParser(
        interpolation=None,
        default_section=_NO_DEFAULT_SECTION,
    )
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return {}
    if not parser.has_section(profile):
        return {}
    return {key: value.strip() for key, value in parser.items(profile, raw=True)}


@dataclass(frozen=True)
class _ExchangeTarget:
    token_endpoint: str
    client_id: str
    audience: str
    scopes: str
    authorization_details: str


@dataclass(frozen=True)
class _CachedToken:
    token: Token
    refresh_after: datetime
    usable_until: datetime


class CachedGitHubOidcCredentials(CredentialsStrategy):
    """``github-oidc`` with a construction-time snapshot and an expiry cache."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        http: Any = None,
        now: Callable[[], datetime] | None = None,
        refresh_skew: timedelta = DEFAULT_REFRESH_SKEW,
    ) -> None:
        env = os.environ if environ is None else environ
        request_url = str(env.get(REQUEST_URL_ENV) or "").strip()
        request_token = str(env.get(REQUEST_TOKEN_ENV) or "").strip()
        if not request_url or not request_token:
            raise RuntimeError(
                "github-oidc deployer auth requires the GitHub Actions ID-token "
                "request environment (job permission id-token: write)"
            )
        self._request_url = _https_url(request_url, label="GitHub Actions ID-token request URL")
        self._request_token = request_token
        self._http = requests if http is None else http
        self._now = now or _utcnow
        self._refresh_skew = refresh_skew
        self._lock = threading.Lock()
        self._cache: dict[_ExchangeTarget, _CachedToken] = {}

    def __repr__(self) -> str:
        return "CachedGitHubOidcCredentials(<Actions request snapshot redacted>)"

    def auth_type(self) -> str:
        return AUTH_TYPE

    def __call__(self, cfg: Any) -> CredentialsProvider:
        host = str(getattr(cfg, "host", "") or "").strip()
        client_id = str(getattr(cfg, "client_id", "") or "").strip()
        if not host or not client_id:
            raise ValueError("github-oidc deployer auth requires a host and a client_id")
        endpoints = cfg.databricks_oidc_endpoints
        token_endpoint = str(getattr(endpoints, "token_endpoint", "") or "").strip()
        if not token_endpoint:
            raise ValueError("github-oidc deployer auth found no Databricks token endpoint")
        _https_url(token_endpoint, label="Databricks OIDC token endpoint")
        target = _ExchangeTarget(
            token_endpoint=token_endpoint,
            client_id=client_id,
            audience=str(getattr(cfg, "token_audience", "") or "").strip() or token_endpoint,
            scopes=str(cfg.get_scopes_as_string() or "").strip(),
            authorization_details=str(getattr(cfg, "authorization_details", "") or "").strip(),
        )

        def token() -> Token:
            return self._token(target)

        def headers() -> dict[str, str]:
            current = token()
            return {"Authorization": f"{current.token_type} {current.access_token}"}

        return OAuthCredentialsProvider(headers, token)

    def _token(self, target: _ExchangeTarget) -> Token:
        with self._lock:
            cached = self._cache.get(target)
            now = self._now()
            if cached is not None and now < cached.refresh_after and now < cached.usable_until:
                return cached.token
            try:
                fresh = self._exchange(target)
            except Exception:
                # Same contract as the SDK's Refreshable stale window: a failed
                # early refresh keeps the still-valid token and retries after a
                # backoff; once the cached token is unusable, the error raises.
                failed_at = self._now()
                if cached is None or failed_at >= cached.usable_until:
                    raise
                self._cache[target] = replace(
                    cached, refresh_after=failed_at + _REFRESH_RETRY_BACKOFF
                )
                return cached.token
            self._cache[target] = fresh
            return fresh.token

    def _github_id_token(self, audience: str) -> str:
        response = self._http.get(
            self._request_url,
            params={"audience": audience},
            headers={"Authorization": f"Bearer {self._request_token}"},
            auth=IgnoreNetrcAuth(),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if not response.ok:
            raise RuntimeError(
                f"GitHub Actions ID-token request was refused (HTTP {response.status_code})"
            )
        value = response.json().get("value")
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("GitHub Actions ID-token response carried no token")
        return value.strip()

    def _exchange(self, target: _ExchangeTarget) -> _CachedToken:
        data = {
            "grant_type": _TOKEN_EXCHANGE_GRANT,
            "subject_token_type": _JWT_TOKEN_TYPE,
            "subject_token": self._github_id_token(target.audience),
            "client_id": target.client_id,
        }
        if target.scopes:
            data["scope"] = target.scopes
        if target.authorization_details:
            data["authorization_details"] = target.authorization_details
        response = self._http.post(
            target.token_endpoint,
            data=data,
            auth=IgnoreNetrcAuth(),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if not response.ok:
            raise RuntimeError(_exchange_refusal(response))
        payload = response.json()
        access_token = payload.get("access_token")
        token_type = payload.get("token_type")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(token_type, str)
            or not token_type
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, int | str)
            or not str(expires_in).isdigit()
            or int(expires_in) <= 0
        ):
            raise RuntimeError("Databricks OIDC token exchange returned a malformed token")
        issued = self._now()
        lifetime = timedelta(seconds=int(expires_in))
        expiry = issued + lifetime
        skew = min(self._refresh_skew, lifetime / 2)
        return _CachedToken(
            token=Token(access_token=access_token, token_type=token_type, expiry=expiry),
            refresh_after=expiry - skew,
            usable_until=expiry - min(_EXPIRY_MARGIN, skew / 2),
        )


def _exchange_refusal(response: Any) -> str:
    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        code = str(payload.get("error") or payload.get("errorCode") or "").strip()
        summary = str(
            payload.get("error_description") or payload.get("errorSummary") or ""
        ).strip()
        detail = f": {code[:64]} {summary[:160]}".rstrip()
    return f"Databricks OIDC token exchange was refused (HTTP {response.status_code}){detail}"
