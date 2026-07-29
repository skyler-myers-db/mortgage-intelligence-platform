"""Post-activation Lakebase readiness through the deployed App.

The full Lakebase schema and grant transaction belongs exclusively to the
pre-activation ``mip_lakebase_migrate`` deployment job. Replaying it after the
App starts can lock live tables and race runtime requests.

This test therefore proves only the active App's non-mutating Lakebase read
path and circuit-breaker state. The later, explicitly mutation-gated
``test_campaign_audit_workflow_live.py`` performs the uniquely marked
write/read round trip through the public API, which makes the deployed App
service principal—not the CI operator—the Lakebase database actor.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

APP_URL = (
    os.environ.get("MIP_DEPLOYED_APP_URL")
    or os.environ.get("MIP_APP_URL")
    or ""
).rstrip("/")
TOKEN = os.environ.get("MIP_BEARER_TOKEN") or os.environ.get("DATABRICKS_TOKEN") or ""
_LIVE_READ_READY = (
    os.environ.get("LAKEBASE_INTEGRATION") == "1" and bool(APP_URL) and bool(TOKEN)
)

pytestmark = pytest.mark.skipif(
    not _LIVE_READ_READY,
    reason="Set LAKEBASE_INTEGRATION=1, MIP_APP_URL, and a bearer token to run",
)


def _get_json(path: str) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"{APP_URL}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed: Any = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed


def test_lakebase_post_activation_read_path_is_healthy() -> None:
    health_status, health = _get_json("/api/health")
    assert health_status == 200
    assert isinstance(health, dict)
    breakers = health.get("circuit_breakers")
    assert isinstance(breakers, dict)
    assert breakers.get("lakebase") == "closed"

    audit_status, events = _get_json("/api/audit/my-events?limit=1")
    assert audit_status == 200
    assert isinstance(events, dict)
    assert set(events) == {"items", "next_cursor"}
    assert isinstance(events["items"], list)
    assert events["next_cursor"] is None or isinstance(events["next_cursor"], str)
