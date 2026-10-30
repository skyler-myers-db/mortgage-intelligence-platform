"""App startup and a digest-pinned lender record: refuse or verify, then one audit row.

* backend/runtime.py refuses a boot whose pinned record does not verify
  (unpinned, mispinned, tampered, or a pin without a record) before settings
  load, with one line that names no value; the code-registry boot is untouched.
* backend/services/lender_identity_boot.py writes exactly one
  LENDER_IDENTITY_VERIFIED row per boot on the pinned path, carrying the
  digest and never the name; zero on the code-registry path; a failed write
  degrades instead of crashing the boot; no passive read writes a row.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas.lender_identity_record import (
    LENDER_IDENTITY_JSON_ENV,
    LENDER_IDENTITY_SHA256_ENV,
    lender_identity_digest,
    reviewed_lender_identity_from_env,
)
from backend.services import lender_identity_boot
from backend.services.audit_event_types import is_server_owned_audit_event_type
from backend.services.audit_lakebase_store import build_audit_insert_params
from backend.services.audit_store import get_audit_store
from backend.services.lender_identity_boot import (
    LENDER_IDENTITY_AUDIT_EVENT_TYPE,
    record_lender_identity_boot,
)
from backend.services.lender_ref import LenderRefResolver
from tests.fixtures.client_profiles import LENDER_IDENTITY
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore

ROOT = Path(__file__).resolve().parents[2]
RECORD_TEXT = json.dumps(LENDER_IDENTITY)
RECORD_SHA256 = lender_identity_digest(RECORD_TEXT)
PINNED = {LENDER_IDENTITY_JSON_ENV: RECORD_TEXT, LENDER_IDENTITY_SHA256_ENV: RECORD_SHA256}
REFUSED = {
    "unpinned": {LENDER_IDENTITY_JSON_ENV: RECORD_TEXT},
    "mispinned": {**PINNED, LENDER_IDENTITY_SHA256_ENV: "0" * 64},
    "tampered": {**PINNED, LENDER_IDENTITY_JSON_ENV: RECORD_TEXT.replace("Harbor", "Harbour")},
    "pin_without_record": {LENDER_IDENTITY_SHA256_ENV: RECORD_SHA256},
}
NAME = LENDER_IDENTITY["legal_name"]


@pytest.fixture(autouse=True)
def _fresh_boot() -> Iterator[None]:
    lender_identity_boot._reset_boot_audit_for_tests()
    yield
    lender_identity_boot._reset_boot_audit_for_tests()


class _RecordingStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def write(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if self.fail:
            raise ConnectionError("lakebase down")


# --- backend/runtime.py --------------------------------------------------------------


def _runtime(env: dict[str, str], code: str) -> subprocess.CompletedProcess[str]:
    base = {key: value for key, value in os.environ.items() if not key.startswith(("MIP_", "PYTEST"))}
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**base, "PYTHONPATH": str(ROOT), "MIP_DISABLE_DOTENV": "1", **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_startup_refuses_an_unverifiable_record_before_settings_load(case: str) -> None:
    completed = _runtime(
        REFUSED[case],
        "import sys\n"
        "from backend import runtime\n"
        "try:\n"
        "    runtime.main()\n"
        "finally:\n"
        "    print('settings_loaded=' + str('backend.config.settings' in sys.modules))\n",
    )

    assert completed.returncode == 1
    assert completed.stdout.strip() == "settings_loaded=False"
    assert completed.stderr.startswith("[mip-runtime] refusing to start: lender identity: MIP_LENDER_IDENTITY")
    assert completed.stderr.count("\n") == 1, "one line, no traceback"
    assert NAME not in completed.stderr and RECORD_TEXT not in completed.stderr


def test_python_dash_m_backend_runtime_refuses_a_tampered_record() -> None:
    completed = _runtime(REFUSED["tampered"], "import runpy; runpy.run_module('backend.runtime', run_name='__main__')")

    assert completed.returncode == 1
    assert "refusing to start: lender identity" in completed.stderr


def test_startup_verifies_a_pinned_record_and_prints_only_its_digest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend import runtime

    for name, value in PINNED.items():
        monkeypatch.setenv(name, value)
    runtime._preflight_lender_identity()

    assert capsys.readouterr().err == f"[mip-runtime] lender identity: digest-pinned record {RECORD_SHA256[:12]} verified\n"


def test_the_code_registry_boot_skips_the_lender_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend import runtime

    for name in PINNED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MIP_LENDER_NAME", "Not Reviewed Mortgage")  # settings refuse it, as before

    runtime._preflight_lender_identity()
    assert capsys.readouterr().err == ""


# --- the one audit row per boot ---------------------------------------------------------


def test_a_pinned_boot_writes_exactly_one_digest_only_row() -> None:
    identity = reviewed_lender_identity_from_env(PINNED)
    store = _RecordingStore()

    assert record_lender_identity_boot(identity, store_factory=lambda: store, actor="system@databricks-apps") == "recorded"
    assert record_lender_identity_boot(identity, store_factory=lambda: store, actor="system@databricks-apps") == "already_attempted"
    assert len(store.calls) == 1
    (call,) = store.calls
    assert call["event_type"] == LENDER_IDENTITY_AUDIT_EVENT_TYPE
    assert call["action"] == "lender_identity_verified"
    assert call["payload_json"] == {"lender_identity_sha256": RECORD_SHA256}
    assert NAME not in json.dumps(call) and LENDER_IDENTITY["nmls_id"] not in json.dumps(call)
    # The real Lakebase store's column and metadata policy accept the row.
    params = build_audit_insert_params(**call)
    assert params["event_type"] == "LENDER_IDENTITY_VERIFIED"
    assert json.loads(params["metadata"]) == {"lender_identity_sha256": RECORD_SHA256, "action": "lender_identity_verified"}
    assert params["entity_id"].startswith("lenderidentity-") and not any(ch.isdigit() for ch in params["entity_id"])


def test_a_code_registry_boot_writes_nothing_and_builds_no_store() -> None:
    def factory() -> _RecordingStore:
        raise AssertionError("the code-registry boot must not touch the audit store")

    assert record_lender_identity_boot(reviewed_lender_identity_from_env({}), store_factory=factory, actor="x") == "not_pinned"


def test_a_failed_write_degrades_without_raising_or_retrying(caplog: pytest.LogCaptureFixture) -> None:
    identity = reviewed_lender_identity_from_env(PINNED)
    store = _RecordingStore(fail=True)

    with caplog.at_level(logging.ERROR):
        outcome = record_lender_identity_boot(identity, store_factory=lambda: store, actor="system@databricks-apps")
    assert outcome == "write_failed"
    assert record_lender_identity_boot(identity, store_factory=lambda: store, actor="system@databricks-apps") == "already_attempted"
    assert len(store.calls) == 1
    assert any(record.getMessage() == "lender_identity_audit_write_failed" for record in caplog.records)
    assert NAME not in caplog.text


def test_the_lifespan_records_the_boot_after_lakebase_warms() -> None:
    text = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    lifespan = text[text.index("async def _lifespan") :]
    assert lifespan.index("_warm_lakebase()") < lifespan.index("record_lender_identity_boot(") < lifespan.index("yield")
    assert "store_factory=get_audit_store" in lifespan


def test_the_boot_row_is_server_owned_and_the_client_route_refuses_it() -> None:
    assert is_server_owned_audit_event_type(LENDER_IDENTITY_AUDIT_EVENT_TYPE)
    store = InMemoryAuditStore()
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        response = TestClient(app).post(
            "/api/v1/audit/event",
            json={
                "actor": "ops@summit-mortgage.example",
                "action": "lender_identity_verified",
                "entity_type": "lender_identity",
                "entity_id": "lenderidentity-forged",
                "event_type": LENDER_IDENTITY_AUDIT_EVENT_TYPE,
            },
            headers={"X-Forwarded-Email": "ops@summit-mortgage.example", "X-Forwarded-Groups": "mip-admin"},
        )
    finally:
        app.dependency_overrides.pop(get_audit_store, None)
    assert response.status_code == 400
    assert store.list(limit=10) == []


def test_no_passive_read_of_the_identity_writes_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config.settings import settings

    store = InMemoryAuditStore()
    monkeypatch.setattr(settings, "_reviewed_lender_identity", reviewed_lender_identity_from_env(PINNED))
    app.dependency_overrides[get_audit_store] = lambda: store
    try:
        settings.reviewed_lender_identity()
        reviewed_lender_identity_from_env(PINNED)
        LenderRefResolver()
        TestClient(app).get("/api/v1/health")
    finally:
        app.dependency_overrides.pop(get_audit_store, None)
    assert [event for event in store.list(limit=50) if event.event_type == LENDER_IDENTITY_AUDIT_EVENT_TYPE] == []
