"""Workspace-bound, no-redirect authenticated Databricks App health reads."""

from __future__ import annotations

import urllib.parse
from typing import Any

import httpx


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
) -> dict[str, Any]:
    canonical = canonical_workspace_app_url(workspace, app_name=app_name, base_url=base_url)
    owns_client = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=False)
    try:
        response = client.get(
            f"{canonical}/api/health",
            headers={"Authorization": f"Bearer {bearer_token}", "Accept": "application/json"},
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"authenticated App health returned HTTP {response.status_code}; redirects are forbidden"
            )
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("authenticated App health returned a non-object payload")
        return body
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("authenticated App health request failed") from exc
    finally:
        if owns_client:
            client.close()
