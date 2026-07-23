"""Workspace-bound, no-redirect authenticated Databricks App health reads."""

from __future__ import annotations

import math
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

APP_HEALTH_READY_TIMEOUT_S = 120.0
APP_HEALTH_READY_INTERVAL_S = 5.0
APP_HEALTH_REQUEST_TIMEOUT_S = 15.0
_TRANSIENT_STATUS_CODES = frozenset({502, 503})
_TRANSIENT_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


class AppHealthNotReadyError(RuntimeError):
    """A bounded-retry-safe App proxy or transport readiness failure."""


@dataclass(frozen=True)
class ActiveAppDeploymentPin:
    deployment_id: str
    lease_id: str | None


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _deployment_id(value: object) -> str:
    return str(_field(value, "deployment_id") or "").strip()


def active_app_deployment_pin(
    workspace: Any,
    *,
    app_name: str,
    expected_lease_id: str | None = None,
) -> ActiveAppDeploymentPin:
    """Read a stable active deployment and its unique lease binding."""

    apps = workspace.apps
    app = apps.get(app_name)
    active_id = _deployment_id(_field(app, "active_deployment"))
    if not active_id:
        raise RuntimeError("Databricks App has no exact active deployment identity")
    get_deployment = getattr(apps, "get_deployment", None)
    if not callable(get_deployment):
        raise RuntimeError("Databricks Apps client cannot read the active deployment")
    deployment = get_deployment(app_name, active_id)
    if _deployment_id(deployment) != active_id:
        raise RuntimeError("Databricks App returned a different active deployment")
    raw_env_vars = _field(deployment, "env_vars")
    if raw_env_vars is not None and not isinstance(raw_env_vars, list):
        raise RuntimeError("active Databricks App deployment environment is invalid")
    matching = [
        item
        for item in (raw_env_vars or [])
        if str(_field(item, "name") or "") == "MIP_APP_DEPLOYMENT_LEASE_ID"
    ]
    if len(matching) != 1:
        raise RuntimeError(
            "active Databricks App deployment must contain exactly one "
            "MIP_APP_DEPLOYMENT_LEASE_ID"
        )
    if _field(matching[0], "value_from") is not None:
        raise RuntimeError("active Databricks App deployment lease must be a literal value")
    raw_lease_value = _field(matching[0], "value")
    lease_id = (
        str(raw_lease_value).strip()
        if raw_lease_value is not None
        else (expected_lease_id or "").strip()
    )
    if not lease_id:
        # Databricks redacts literal deployment environment values from
        # get-deployment. The active deployment ID and unique binding remain
        # pinned here; authenticated health supplies the lease for read-only
        # nightly verification. Governed deploy probes always provide the
        # caller-known lease and therefore pin both before the first request.
        pinned_lease_id: str | None = None
    else:
        try:
            UUID(lease_id)
        except ValueError as exc:
            raise RuntimeError("active Databricks App deployment lease is invalid") from exc
        pinned_lease_id = lease_id
    if expected_lease_id is not None:
        expected = expected_lease_id.strip()
        try:
            UUID(expected)
        except ValueError as exc:
            raise RuntimeError("expected Databricks App deployment lease is invalid") from exc
        if pinned_lease_id != expected:
            raise RuntimeError("active App lease does not match the expected deployment lease")
    if _deployment_id(_field(apps.get(app_name), "active_deployment")) != active_id:
        raise RuntimeError("Databricks App active deployment changed during lease verification")
    return ActiveAppDeploymentPin(deployment_id=active_id, lease_id=pinned_lease_id)


def assert_active_app_deployment_pin(
    workspace: Any,
    *,
    app_name: str,
    expected: ActiveAppDeploymentPin,
) -> None:
    if (
        active_app_deployment_pin(
            workspace,
            app_name=app_name,
            expected_lease_id=expected.lease_id,
        )
        != expected
    ):
        raise RuntimeError("Databricks App active deployment or lease changed during proof")


def canonical_workspace_app_url(workspace: Any, *, app_name: str, base_url: str) -> str:
    configured = base_url.strip().rstrip("/")
    actual = str(getattr(workspace.apps.get(app_name), "url", None) or "").strip().rstrip("/")
    for label, value in (("configured", configured), ("workspace", actual)):
        parsed = urllib.parse.urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(f"{label} Databricks App URL is invalid")
    if configured.casefold() != actual.casefold():
        raise RuntimeError("configured App URL does not match the workspace App resource")
    return actual


def authenticated_app_health(
    workspace: Any,
    *,
    app_name: str,
    base_url: str,
    bearer_token: str,
    client: Any | None = None,
    request_timeout_s: float | None = None,
) -> dict[str, Any]:
    canonical = canonical_workspace_app_url(workspace, app_name=app_name, base_url=base_url)
    owns_client = client is None
    client = client or httpx.Client(
        timeout=request_timeout_s or APP_HEALTH_REQUEST_TIMEOUT_S,
        follow_redirects=False,
    )
    try:
        request_kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": f"Bearer {bearer_token}",
                "Accept": "application/json",
            }
        }
        if request_timeout_s is not None:
            request_kwargs["timeout"] = request_timeout_s
        response = client.get(
            f"{canonical}/api/health",
            **request_kwargs,
        )
        if response.status_code != 200:
            error_type = (
                AppHealthNotReadyError
                if response.status_code in _TRANSIENT_STATUS_CODES
                else RuntimeError
            )
            raise error_type(
                f"authenticated App health returned HTTP {response.status_code}; redirects are forbidden"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("authenticated App health returned a non-object payload")
        return body
    except _TRANSIENT_TRANSPORT_ERRORS as exc:
        raise AppHealthNotReadyError("authenticated App health request failed") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("authenticated App health request failed") from exc
    finally:
        if owns_client:
            client.close()


def wait_for_authenticated_app_health(
    workspace: Any,
    *,
    app_name: str,
    base_url: str,
    bearer_token: str,
    timeout_s: float,
    interval_s: float,
    request_timeout_s: float = APP_HEALTH_REQUEST_TIMEOUT_S,
    client: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_retry: Callable[[int, AppHealthNotReadyError, float], None] | None = None,
    assert_pinned: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Wait only through transient proxy/transport readiness failures."""

    if not math.isfinite(timeout_s) or not 0 <= timeout_s <= APP_HEALTH_READY_TIMEOUT_S:
        raise ValueError(
            "App health readiness timeout must be finite and between "
            f"0 and {APP_HEALTH_READY_TIMEOUT_S:g} seconds"
        )
    if not math.isfinite(interval_s) or interval_s <= 0 or interval_s > APP_HEALTH_READY_TIMEOUT_S:
        raise ValueError(
            "App health readiness interval must be finite, positive, and no greater "
            f"than {APP_HEALTH_READY_TIMEOUT_S:g} seconds"
        )
    if (
        not math.isfinite(request_timeout_s)
        or request_timeout_s <= 0
        or request_timeout_s > APP_HEALTH_REQUEST_TIMEOUT_S
    ):
        raise ValueError(
            "App health request timeout must be finite, positive, and no greater "
            f"than {APP_HEALTH_REQUEST_TIMEOUT_S:g} seconds"
        )

    deadline = monotonic() + timeout_s
    attempts = 0
    last_error: AppHealthNotReadyError | None = None
    owns_client = client is None
    active_client = client or httpx.Client(
        timeout=request_timeout_s,
        follow_redirects=False,
    )
    try:
        while True:
            remaining_s = deadline - monotonic()
            if attempts > 0 and remaining_s <= 0:
                raise RuntimeError(
                    "authenticated App health did not become ready "
                    f"within {timeout_s:g}s after {attempts} attempt(s)"
                ) from last_error
            attempts += 1
            per_request_timeout_s = max(
                0.001,
                min(request_timeout_s, max(0.0, remaining_s)),
            )
            if assert_pinned is not None:
                assert_pinned()
            try:
                body = authenticated_app_health(
                    workspace,
                    app_name=app_name,
                    base_url=base_url,
                    bearer_token=bearer_token,
                    client=active_client,
                    request_timeout_s=(per_request_timeout_s if owns_client else None),
                )
                if assert_pinned is not None:
                    assert_pinned()
                return body
            except AppHealthNotReadyError as exc:
                last_error = exc
                if assert_pinned is not None:
                    assert_pinned()
                remaining_s = deadline - monotonic()
                if remaining_s <= 0:
                    raise RuntimeError(
                        "authenticated App health did not become ready "
                        f"within {timeout_s:g}s after {attempts} attempt(s)"
                    ) from exc
                delay_s = min(interval_s, remaining_s)
                if on_retry is not None:
                    on_retry(attempts, exc, delay_s)
                sleep(delay_s)
    finally:
        if owns_client:
            active_client.close()
