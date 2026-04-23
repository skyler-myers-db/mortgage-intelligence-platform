"""Unit tests for /api/geo/state-rollups.

Covers the schema + router contract so the USChoroplethMap can swap off
its hardcoded STATE_FACTS literal onto real rollups. The live Databricks
repo reads ``mip.gold.funnel_snapshot_daily`` filtered to the latest
snapshot + per-state / cross-segment row; the in-process fixture returns
a deterministic 6-state shape that exercises the same envelope.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_state_rollups_returns_six_state_footprint():
    """The fixture covers the 6-state Delta Share footprint. Response
    envelope must carry rollups[] + snapshot_date, and every state code
    must be a 2-char uppercase USPS code."""
    response = client.get("/api/geo/state-rollups")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "rollups" in payload
    assert "snapshot_date" in payload

    rollups = payload["rollups"]
    assert isinstance(rollups, list)
    assert len(rollups) == 6

    codes = {r["state"] for r in rollups}
    assert codes == {"IL", "CA", "FL", "TX", "WA", "CO"}, codes

    for r in rollups:
        assert len(r["state"]) == 2
        assert r["state"].isupper()
        assert r["addressable"] >= 0
        assert r["in_the_money"] >= 0
        assert r["top_tier_opportunities"] >= 0
        # avg_score is 0..100 bounded by schema
        assert 0 <= r["avg_score"] <= 100
        # in_the_money should never exceed addressable
        assert r["in_the_money"] <= r["addressable"]


def test_state_rollups_snapshot_date_format():
    """Snapshot date should be ISO-ish (YYYY-MM-DD) when present."""
    response = client.get("/api/geo/state-rollups")
    payload = response.json()
    if payload["snapshot_date"] is not None:
        # YYYY-MM-DD
        parts = payload["snapshot_date"].split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4
