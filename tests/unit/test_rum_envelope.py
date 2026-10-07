"""The browser RUM privacy envelope (D-platform-process-d1 buildSpec step 6).

(i) the wire fields are exactly the allowed sets; (ii) every detail key is
documented; (iii) the stored columns are exactly the aggregate columns, with
no identifier-shaped name and no sub-day timestamp; (iv) with RUM off the
endpoint stores nothing; (v) no per-event log line, only the per-flush
counts line; (vi) a route outside the registry templates is refused;
(vii) the code default is off; (viii) app.yaml never sets the flag;
(ix) the docs section exists without legal conclusions; (x) .env.example
documents the default (the integrator applies that line at merge).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from backend.api import telemetry as telemetry_mod
from backend.config.settings import Settings
from backend.main import app
from backend.schemas.telemetry import RumBatch, RumDetailKey, RumEvent
from backend.services import rum_rollup

ROOT = Path(__file__).resolve().parents[2]
RUM_PATH = "/api/telemetry/rum"
ALLOWED_EVENT_FIELDS = {"metric", "value", "rating", "route", "navigation_type", "details"}
ALLOWED_DETAIL_KEYS = {
    "dom_content_loaded_ms", "ttfb_ms", "transfer_size", "from_route", "duration_ms", "attempt", "retryable",
    "dependency", "error_name", "error_kind", "error_source", "boundary", "api_route", "cache", "warehouse_ms",
    "lakebase_ms", "total_ms", "interaction_target", "lcp_element", "input_delay_ms", "processing_ms",
    "presentation_ms",
}
AGGREGATE_COLUMNS = {
    "slot", "day", "metric", "route", "facet", "rating", "builds", "sample_count", "value_sum", "value_min",
    "value_max", "buckets",
}
IDENTIFIER_SHAPED = re.compile(r"actor|user|email|ip|session|agent|device", re.IGNORECASE)
FORBIDDEN_SENTENCES = ("No consent banner is needed", "cannot monitor an individual employee")


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        recorder = self

        class _Cursor:
            def __enter__(self) -> _Cursor:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
                recorder.statements.append(sql)

        class _Conn:
            def cursor(self) -> _Cursor:
                return _Cursor()

        yield _Conn()


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    fake = _Recorder()
    rum_rollup._reset_for_tests(client_factory=lambda: fake)
    try:
        yield fake
    finally:
        rum_rollup._reset_for_tests()


def _security_section() -> str:
    text = (ROOT / "docs" / "security-and-compliance.md").read_text(encoding="utf-8")
    match = re.search(r"^## Browser telemetry \(RUM\)\n(?P<body>.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    assert match, "docs/security-and-compliance.md needs a '## Browser telemetry (RUM)' section"
    return match.group("body")


def _rum_daily_columns() -> dict[str, str]:
    schema = (ROOT / "lakebase" / "schema.sql").read_text(encoding="utf-8")
    match = re.search(r"CREATE TABLE IF NOT EXISTS mip_app\.rum_daily \((?P<body>.*?)\n\);", schema, flags=re.S)
    assert match, "lakebase/schema.sql must create mip_app.rum_daily"
    columns: dict[str, str] = {}
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("PRIMARY KEY"):
            continue
        name, _, rest = stripped.partition(" ")
        columns[name] = rest.strip()
    return columns


def test_i_the_wire_fields_are_exactly_the_allowed_sets() -> None:
    assert set(RumEvent.model_fields) == ALLOWED_EVENT_FIELDS
    assert set(RumBatch.model_fields) == {"events"}
    assert RumEvent.model_config.get("extra") == "forbid"
    assert RumBatch.model_config.get("extra") == "forbid"


def test_ii_every_detail_key_is_in_the_documented_closed_list() -> None:
    keys = set(get_args(RumDetailKey))
    assert keys == ALLOWED_DETAIL_KEYS
    documented = set(re.findall(r"`([a-z_]+)`", _security_section()))
    assert keys <= documented, sorted(keys - documented)


def test_iii_the_stored_columns_are_aggregates_with_no_identifier_or_timestamp() -> None:
    columns = _rum_daily_columns()
    assert set(columns) == AGGREGATE_COLUMNS
    assert [name for name in columns if IDENTIFIER_SHAPED.search(name)] == []
    assert [name for name, decl in columns.items() if "TIMESTAMP" in decl.upper()] == []


def test_iv_with_rum_off_nothing_is_accepted_or_stored(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", False)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(RUM_PATH, json={"events": [{"metric": "lcp", "value": 900, "route": "/"}]})

    assert response.status_code == 202
    assert response.json() == {"accepted": 0, "enabled": False}
    assert rum_rollup._rollup().pending_keys() == []
    rum_rollup._rollup().flush()
    assert recorder.statements == []


def test_v_no_per_event_line_only_the_per_flush_counts_line(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(telemetry_mod.settings, "mip_rum_enabled", True)
    client = TestClient(app, raise_server_exceptions=False)
    events = [
        {"metric": "lcp", "value": 900, "route": "/lead-queue", "details": {"lcp_element": "h1"}},
        {"metric": "cls", "value": 0.01, "route": "/"},
        {"metric": "inp", "value": 120, "route": "/glossary", "details": {"interaction_target": "nav"}},
    ]

    with caplog.at_level(logging.DEBUG):
        assert client.post(RUM_PATH, json={"events": events}).json() == {"accepted": 3, "enabled": True}
        rum_rollup._rollup().flush()

    names = [getattr(record, "mip_event", None) for record in caplog.records]
    assert "rum_metric" not in names
    flushed = [record for record in caplog.records if getattr(record, "mip_event", None) == "rum_rollup_flushed"]
    assert len(flushed) == 1
    assert set(flushed[0].mip_extras) == {"rows", "events", "dropped"}  # type: ignore[attr-defined]
    assert flushed[0].mip_extras["events"] == 3  # type: ignore[attr-defined]
    assert len(recorder.statements) >= 1


@pytest.mark.parametrize("route", ["/borrower-360/abc", "/lower case", "/borrower-360/:borrower_id"])
def test_vi_a_route_outside_the_registry_templates_is_refused(route: str) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(RUM_PATH, json={"events": [{"metric": "lcp", "value": 900, "route": route}]})
    assert response.status_code == 422


def test_vii_the_code_default_is_off() -> None:
    assert Settings.model_fields["mip_rum_enabled"].default is False


def test_viii_app_yaml_never_sets_the_flag() -> None:
    assert "MIP_RUM_ENABLED" not in (ROOT / "app.yaml").read_text(encoding="utf-8")


def test_ix_the_docs_section_states_facts_not_legal_conclusions() -> None:
    section = _security_section()
    for fact in ("MIP_RUM_ENABLED=0", "mip_app.rum_daily", "90 days", "Administrators only", "first-party"):
        assert fact.lower() in section.lower(), fact
    full = (ROOT / "docs" / "security-and-compliance.md").read_text(encoding="utf-8")
    for sentence in FORBIDDEN_SENTENCES:
        assert sentence.lower() not in full.lower(), sentence


def test_x_env_example_documents_the_default() -> None:
    """EXPECTED RED until the integrator applies the .env.example line at merge."""
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "MIP_RUM_ENABLED=1" in lines
    assert "MIP_RUM_ENABLED=0" not in lines
    index = lines.index("MIP_RUM_ENABLED=1")
    assert lines[index - 1] == "# set 0 to disable all browser telemetry"
