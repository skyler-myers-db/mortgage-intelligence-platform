"""Unit tests for ``backend.services.observability``.

Covers:
* StructuredFormatter produces parseable JSON with required fields
* PII denylist strips forbidden keys before emission
* correlation_id_var propagates across a ``timed_dependency`` block
* Correlation middleware mints + echoes the X-Correlation-ID header
* Health endpoint exposes ``breaker_state_changes_last_hour`` and
  ``recent_errors_count``

Every test is stdlib-only and deterministic -- no sleeps, no network.
"""
from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import health as health_mod
from backend.main import app
from backend.services import observability as obs
from backend.services import resilience

client = TestClient(app)


# ---------------------------------------------------------------------------
# StructuredFormatter
# ---------------------------------------------------------------------------


def _capture(logger: logging.Logger) -> io.StringIO:
    """Attach a stream handler with StructuredFormatter to ``logger``."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(obs.StructuredFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return buf


def test_structured_formatter_emits_valid_json() -> None:
    log = logging.getLogger("test.obs.format")
    buf = _capture(log)
    try:
        obs.emit(log, "warehouse_query_end", dependency="warehouse",
                 duration_ms=42.5, outcome="ok", rows_returned=7)
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "warehouse_query_end"
        assert record["level"] == "INFO"
        assert record["dependency"] == "warehouse"
        assert record["duration_ms"] == 42.5
        assert record["outcome"] == "ok"
        assert record["rows_returned"] == 7
        assert "ts" in record and record["ts"].endswith("+00:00")
        assert record["correlation_id"]  # non-empty
        assert record["logger"] == "test.obs.format"
    finally:
        log.handlers.clear()
        log.propagate = True


def test_pii_denylist_redacts_forbidden_kwargs() -> None:
    log = logging.getLogger("test.obs.pii")
    buf = _capture(log)
    try:
        obs.emit(
            log,
            "sink",
            owner_name_hash="dead-beef",
            mailing_street_address="123 Main St",
            situs_street_address="456 Oak Ave",
            authorization="Bearer secret-token",
            token="s3cr3t",
            password="hunter2",
            safe_field="visible",
        )
        record = json.loads(buf.getvalue().splitlines()[0])
        for key in (
            "owner_name_hash",
            "mailing_street_address",
            "situs_street_address",
            "authorization",
            "token",
            "password",
        ):
            assert record[key] == "<redacted>", f"{key} not redacted"
        assert record["safe_field"] == "visible"
    finally:
        log.handlers.clear()
        log.propagate = True


def test_pii_denylist_walks_nested_dict() -> None:
    log = logging.getLogger("test.obs.nested")
    buf = _capture(log)
    try:
        obs.emit(
            log,
            "sink",
            context={
                "clip_id": "CL-123",
                "owner_name_hash": "dead-beef",
                "mailing_city": "Denver",
            },
        )
        record = json.loads(buf.getvalue().splitlines()[0])
        ctx = record["context"]
        assert ctx["clip_id"] == "CL-123"
        assert ctx["owner_name_hash"] == "<redacted>"
        assert ctx["mailing_city"] == "<redacted>"
    finally:
        log.handlers.clear()
        log.propagate = True


# ---------------------------------------------------------------------------
# Correlation-ID propagation across timed_dependency
# ---------------------------------------------------------------------------


def test_correlation_id_propagates_into_timed_dependency() -> None:
    log = logging.getLogger("test.obs.cid")
    buf = _capture(log)
    token = obs.set_correlation_id("cid-fixed-123")
    try:
        with obs.timed_dependency("warehouse", "execute", logger=log) as ctx:
            ctx["rows_returned"] = 3
        lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 2
        start, end = lines
        assert start["event"] == "dependency_call_start"
        assert end["event"] == "dependency_call_end"
        assert start["correlation_id"] == "cid-fixed-123"
        assert end["correlation_id"] == "cid-fixed-123"
        assert end["dependency"] == "warehouse"
        assert end["outcome"] == "ok"
        assert "duration_ms" in end
        assert end["rows_returned"] == 3
    finally:
        obs.reset_correlation_id(token)
        log.handlers.clear()
        log.propagate = True


def test_timed_dependency_emits_error_on_exception() -> None:
    log = logging.getLogger("test.obs.err")
    buf = _capture(log)
    obs._reset_counters_for_tests()
    with (
        pytest.raises(ValueError, match="boom"),
        obs.timed_dependency("lakebase", "fetchone", logger=log),
    ):
        raise ValueError("boom")
    lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln]
    assert len(lines) == 2
    end = lines[1]
    assert end["event"] == "dependency_call_end"
    assert end["outcome"] == "error"
    assert end["exc_type"] == "ValueError"
    assert end["exc_msg"] == "boom"
    assert obs.recent_error_count() >= 1
    log.handlers.clear()
    log.propagate = True


# ---------------------------------------------------------------------------
# Correlation middleware
# ---------------------------------------------------------------------------


def test_middleware_mints_correlation_id_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    res = client.get("/api/health")
    assert res.status_code == 200
    cid = res.headers.get("X-Correlation-ID")
    assert cid
    # Minted UUIDs are 32 hex chars (uuid.uuid4().hex).
    assert len(cid) == 32
    assert all(c in "0123456789abcdef" for c in cid)


def test_middleware_echoes_client_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    res = client.get(
        "/api/health",
        headers={"X-Correlation-ID": "trace-abc-123"},
    )
    assert res.status_code == 200
    assert res.headers["X-Correlation-ID"] == "trace-abc-123"


# ---------------------------------------------------------------------------
# Health endpoint observability fields
# ---------------------------------------------------------------------------


def test_health_exposes_observability_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(health_mod, "_probe_warehouse", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_lakebase", lambda: True)
    monkeypatch.setattr(health_mod, "_probe_genie", lambda: True)
    obs._reset_counters_for_tests()
    obs.record_breaker_state_change(
        name="warehouse", from_state="closed", to_state="open"
    )
    obs.record_error(dependency="warehouse", exc_type="DatabricksSqlError")
    res = client.get("/api/health")
    payload: dict[str, Any] = res.json()
    assert payload["breaker_state_changes_last_hour"] >= 1
    assert payload["recent_errors_count"] >= 1


# ---------------------------------------------------------------------------
# Breaker state changes emit structured events
# ---------------------------------------------------------------------------


def test_breaker_open_transition_records_observability_event() -> None:
    resilience._reset_breakers_for_tests()
    obs._reset_counters_for_tests()
    cb = resilience.CircuitBreaker("obs-test", failure_threshold=2, cooldown_s=5.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert obs.recent_breaker_state_changes() >= 1


def test_structured_formatter_handles_adhoc_log_call() -> None:
    """A bare ``log.info("msg")`` still produces valid JSON."""
    log = logging.getLogger("test.obs.adhoc")
    buf = _capture(log)
    try:
        log.info("classic message, no mip_event attached")
        record = json.loads(buf.getvalue().splitlines()[0])
        assert record["event"] == "adhoc"  # last path segment of logger name
        assert record["message"] == "classic message, no mip_event attached"
        assert record["correlation_id"]
    finally:
        log.handlers.clear()
        log.propagate = True
