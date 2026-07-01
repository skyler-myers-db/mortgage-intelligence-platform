#!/usr/bin/env python3
"""Run due Mortgage Growth Agent monitors from a Databricks scheduled job.

The job calls the deployed Databricks App instead of importing app internals so
it exercises the same OAuth/RBAC/API path as an operator. The endpoint is
admin-gated and draft-only: it refreshes saved watchlists and creates Slack /
Teams review drafts, but never sends messages or activates a connector.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo


def _workspace_client() -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except Exception as exc:  # pragma: no cover - Databricks job env only
        raise RuntimeError("databricks-sdk is required to resolve the App URL and bearer token") from exc
    return WorkspaceClient()


def _resolve_app_url(*, app_name: str, explicit_url: str | None) -> str:
    if explicit_url:
        return explicit_url.rstrip("/")
    app = _workspace_client().apps.get(app_name)
    url = (getattr(app, "url", "") or "").rstrip("/")
    if not url:
        raise RuntimeError(f"Databricks app {app_name!r} has no resolved URL")
    return url


def _bearer_token() -> str:
    client = _workspace_client()
    headers = client.config.authenticate()
    auth = headers.get("Authorization", "") if isinstance(headers, dict) else ""
    if not auth.startswith("Bearer "):
        raise RuntimeError("Databricks SDK auth returned no Bearer token")
    return auth.removeprefix("Bearer ").strip()


def _post_json(
    *,
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - app URL is operator controlled
            body = resp.read().decode("utf-8")
            return json.loads(body or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"growth-agent scheduler returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"growth-agent scheduler request failed: {exc}") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", default=os.environ.get("MIP_APP_NAME", "mip-app"))
    parser.add_argument("--app-url", default=os.environ.get("MIP_APP_URL", ""))
    parser.add_argument("--limit", type=int, default=int(os.environ.get("MIP_GROWTH_AGENT_SCHEDULER_LIMIT", "20")))
    parser.add_argument("--channels", default=os.environ.get("MIP_GROWTH_AGENT_SCHEDULER_CHANNELS", "slack,teams"))
    parser.add_argument("--timeout-s", type=float, default=float(os.environ.get("MIP_GROWTH_AGENT_SCHEDULER_TIMEOUT_S", "90")))
    return parser


def _request_id_for_bucket(*, limit: int, channels: list[str], now: datetime | None = None) -> str:
    """Return a stable UUID for one scheduler cadence bucket.

    Databricks Jobs already sets ``max_concurrent_runs=1``. This key adds API
    idempotency for ordinary operator retries or app-side 5xx retry loops.
    """

    timestamp = now or datetime.now(UTC)
    bucket = timestamp.astimezone(ZoneInfo("America/Chicago")).strftime("%Y%m%d")
    channel_key = ",".join(sorted(channels))
    seed = f"mip-growth-agent-scheduler:{bucket}:limit={limit}:channels={channel_key}"
    return str(uuid5(NAMESPACE_URL, seed))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    channels = [item.strip() for item in args.channels.split(",") if item.strip()]
    app_url = _resolve_app_url(app_name=args.app_name, explicit_url=args.app_url or None)
    token = _bearer_token()
    payload = {
        "limit": args.limit,
        "channels": channels,
        "request_id": _request_id_for_bucket(limit=args.limit, channels=channels),
    }
    result = _post_json(
        url=f"{app_url}/api/v1/growth-agent/monitors/run-due-all",
        token=token,
        payload=payload,
        timeout_s=args.timeout_s,
    )
    print(json.dumps({"app_url": app_url, **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - Databricks job log should include concise failure
        print(f"[growth-agent-scheduler] ERROR: {exc}", file=sys.stderr)
        raise
