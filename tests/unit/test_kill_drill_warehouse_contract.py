"""The warehouse kill drills follow "a stop is not an outage" (audit delivery-01).

/api/health reads the warehouse lifecycle state: STOPPED / STOPPING are "up"
(available on demand) and STARTING is "resuming". The `warehouse` and
`warehouse-real` drills used to wait for STOPPED and then require
status=degraded with warehouse=down (or an open breaker), so both failed
deterministically once health stopped running SQL. The drills now assert the
new contract: health stays ok, and the next data read resumes the warehouse.
The degraded warehouse contract stays with `warehouse-sim`.

The behavioural tests run the drill's own shell (its `log` and `probe_health`,
plus the sourced contract lib) with the real curl against a local server that
forwards /api/health to the real app with the warehouse state stubbed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.service.sql import State
from fastapi.testclient import TestClient

from backend.config.settings import settings
from backend.main import app
from backend.services import health_probes, resilience

REPO_ROOT = Path(__file__).resolve().parents[2]
DRILL = REPO_ROOT / "tools" / "kill_drill" / "run_drill.sh"
LIB = REPO_ROOT / "tools" / "kill_drill" / "lib_warehouse_on_demand.sh"
DRILL_TEXT = DRILL.read_text(encoding="utf-8")

needs_shell_tools = pytest.mark.skipif(
    shutil.which("curl") is None or shutil.which("jq") is None,
    reason="the drill shells out to curl and jq",
)


def _drill_function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", DRILL_TEXT, re.DOTALL | re.MULTILINE)
    assert match, f"{name}() is defined in run_drill.sh"
    return match.group(0)


def _drill_actor_header() -> tuple[str, str]:
    match = re.search(r'^HEALTH_ACTOR_HEADER="([^:"]+): ([^"]+)"$', DRILL_TEXT, re.MULTILINE)
    assert match, "run_drill.sh declares the actor header its probes send"
    return match.group(1), match.group(2)


# --- static guards: the stop drills no longer gate on the degraded contract ---


@pytest.mark.parametrize("drill", ["drill_warehouse", "drill_warehouse_real"])
def test_the_warehouse_stop_drills_gate_on_the_on_demand_contract(drill: str) -> None:
    body = _drill_function(drill)

    assert 'assert_degraded_health "$APP_URL" warehouse' not in body
    assert "assert_data_endpoint_degraded" not in body
    assert 'assert_stopped_warehouse_health "$APP_URL" 30' in body
    assert 'assert_stopped_warehouse_read_resumes "$APP_URL"' in body


def test_warehouse_real_still_restarts_the_warehouse_when_the_health_gate_fails() -> None:
    body = _drill_function("drill_warehouse_real")
    gate = body.index('if ! assert_stopped_warehouse_health "$APP_URL" 30; then')
    failure_branch = body[gate : body.index("return 1", gate)]

    assert 'real_infra_logged start warehouse "$whid"' in failure_branch


def test_the_degraded_warehouse_path_is_still_proved_by_warehouse_sim() -> None:
    body = _drill_function("drill_warehouse_sim")

    assert 'assert_degraded_health "$DRILL_APP_URL" warehouse' in body
    assert 'assert_data_endpoint_degraded "$DRILL_APP_URL" "/api/leads?limit=5"' in body


def test_the_drill_sources_the_contract_lib_and_both_scripts_parse() -> None:
    source_line = '. "$REPO_ROOT/tools/kill_drill/lib_warehouse_on_demand.sh"'
    assert source_line in DRILL_TEXT
    assert DRILL_TEXT.index(source_line) < DRILL_TEXT.index('log "drill started at')
    for script in (DRILL, LIB):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


# --- behavioural: the drill's shell against the real /api/health body ---


class _Warehouses:
    def __init__(self, state: State) -> None:
        self._state = state

    def get(self, *, id: str) -> Any:  # noqa: A002 -- SDK keyword
        return SimpleNamespace(state=self._state)


@dataclass
class _Upstream:
    """Forwards /api/health to the real app; answers /api/leads from a script."""

    lead_statuses: list[int]
    lead_requests: list[dict[str, str]] = field(default_factory=list)
    base_url: str = ""


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Upstream]:
    monkeypatch.setattr(settings, "databricks_warehouse_id", "wh-drill")
    monkeypatch.setattr(health_probes, "probe_lakebase", lambda: True)
    monkeypatch.setattr(health_probes, "probe_genie", lambda: True)
    resilience._reset_breakers_for_tests()
    health_probes._probe_cache.clear()
    client = TestClient(app)
    state = _Upstream(lead_statuses=[200])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- http.server hook
            if self.path.startswith("/api/health"):
                forwarded = {k: v for k, v in self.headers.items() if k.lower() == "x-forwarded-email"}
                response = client.get(self.path, headers=forwarded)
                self._reply(response.status_code, response.content)
            elif self.path.startswith("/api/leads"):
                state.lead_requests.append(dict(self.headers.items()))
                status = state.lead_statuses.pop(0) if len(state.lead_statuses) > 1 else state.lead_statuses[0]
                body = b"[{}, {}]" if status == 200 else b'{"detail": {"retryable": true}}'
                self._reply(status, body)
            else:
                self._reply(404, b"{}")

        def _reply(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        resilience._reset_breakers_for_tests()
        health_probes._probe_cache.clear()


def _stub_warehouse_state(monkeypatch: pytest.MonkeyPatch, state: State) -> list[bool]:
    monkeypatch.setattr(
        health_probes,
        "_warehouse_state_client",
        lambda: SimpleNamespace(warehouses=_Warehouses(state)),
    )
    select_one_calls: list[bool] = []

    def select_one() -> bool:
        select_one_calls.append(True)
        return True

    monkeypatch.setattr(health_probes, "_probe_warehouse_select_one", select_one)
    return select_one_calls


def _run_drill_shell(
    upstream: _Upstream, calls: str, tmp_path: Path, *, resume_window_s: int = 30
) -> subprocess.CompletedProcess[str]:
    header_name, header_value = _drill_actor_header()
    script = "\n".join(
        [
            "set -euo pipefail",
            "TARGET=warehouse-real",
            f'LOG="{tmp_path / "drill.log"}"',
            f'HEALTH_ACTOR_HEADER="{header_name}: {header_value}"',
            _drill_function("log"),
            _drill_function("probe_health"),
            f'. "{LIB}"',
            calls.replace("BASE", upstream.base_url),
        ]
    )
    env = {**os.environ, "MIP_KILL_DRILL_RESUME_READ_SECONDS": str(resume_window_s)}
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60
    )


@needs_shell_tools
@pytest.mark.parametrize("state", [State.STOPPED, State.STOPPING, State.STARTING])
def test_the_stop_gate_passes_on_the_real_health_body_for_a_stopped_warehouse(
    monkeypatch: pytest.MonkeyPatch, upstream: _Upstream, tmp_path: Path, state: State
) -> None:
    select_one = _stub_warehouse_state(monkeypatch, state)

    result = _run_drill_shell(upstream, 'assert_stopped_warehouse_health "BASE" 1 || exit 1', tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: stopped warehouse reads as available on demand (status=ok" in result.stdout
    assert select_one == [], "health read the lifecycle state, not SQL"


@needs_shell_tools
@pytest.mark.parametrize("outage", ["deleted", "open-breaker"])
def test_the_stop_gate_fails_when_health_reports_an_outage(
    monkeypatch: pytest.MonkeyPatch, upstream: _Upstream, tmp_path: Path, outage: str
) -> None:
    _stub_warehouse_state(monkeypatch, State.DELETED if outage == "deleted" else State.STOPPED)
    if outage == "open-breaker":
        breaker = resilience.get_breaker("warehouse", failure_threshold=1, cooldown_s=60)
        breaker.record_failure()

    result = _run_drill_shell(upstream, 'assert_stopped_warehouse_health "BASE" 1 || exit 1', tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "status=degraded warehouse=down" in result.stdout
    assert "FAIL: warehouse stopped and /api/health reported an outage" in result.stdout


@needs_shell_tools
def test_a_data_read_that_resumes_the_warehouse_passes_after_a_retry(
    upstream: _Upstream, tmp_path: Path
) -> None:
    upstream.lead_statuses = [503, 200]

    result = _run_drill_shell(upstream, 'assert_stopped_warehouse_read_resumes "BASE" || exit 1', tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "GET /api/leads?limit=5 -> HTTP 503" in result.stdout
    assert "PASS: a data read resumed the stopped warehouse (/api/leads?limit=5 -> HTTP 200, rows=2)" in result.stdout
    header_name, header_value = _drill_actor_header()
    assert len(upstream.lead_requests) == 2
    assert all(request.get(header_name) == header_value for request in upstream.lead_requests)


@needs_shell_tools
def test_a_data_read_that_never_resumes_the_warehouse_fails(upstream: _Upstream, tmp_path: Path) -> None:
    upstream.lead_statuses = [503]

    result = _run_drill_shell(
        upstream, 'assert_stopped_warehouse_read_resumes "BASE" || exit 1', tmp_path, resume_window_s=0
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL: /api/leads?limit=5 never returned HTTP 200 within 0s" in result.stdout
    assert len(upstream.lead_requests) == 1
