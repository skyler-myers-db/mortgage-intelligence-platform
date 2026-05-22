"""End-to-end round-trip test against a real Lakebase instance.

SKIPPED unless ``LAKEBASE_HOST`` is present. When it is, this test:

1. Opens a connection via the Slice-5 ``LakebaseClient``.
2. Applies ``lakebase/schema.sql`` (idempotent; re-runs are no-ops).
3. INSERTs an audit row via the ``LakebaseAuditStore``.
4. SELECTs it back via ``store.list(limit=50)``.
5. Asserts the row round-trips (event_type, actor, subject_clip,
   metadata JSONB).

The test cleans up its own row after assertion so repeat runs don't
pile up data in the real ``action_audit`` table.
"""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path
from uuid import uuid4

import pytest

from backend.services.audit_lakebase_store import LakebaseAuditStore
from backend.services.lakebase import (
    LakebaseClient,
    _reset_client_for_tests,
    get_lakebase_client,
)

# Only run when the operator has explicitly opted in via
# ``LAKEBASE_INTEGRATION=1`` and either static LAKEBASE_* credentials or
# Databricks workspace credentials are present. A bare ``LAKEBASE_HOST``
# in ``.env.local`` must not trigger the test -- that would fail
# cryptically against a dev machine without Postgres running. The
# opt-in flag keeps CI and local ``pytest -q`` quiet while letting the
# slice-5 operator run the round-trip.
_HAS_STATIC_CREDS = all(
    os.environ.get(k)
    for k in ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD")
)
_HAS_WORKSPACE_CREDS = all(
    os.environ.get(k)
    for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN")
)
_HAS_CREDS = os.environ.get("LAKEBASE_INTEGRATION") == "1" and (
    _HAS_STATIC_CREDS or _HAS_WORKSPACE_CREDS
)

pytestmark = pytest.mark.skipif(
    not _HAS_CREDS,
    reason="Set LAKEBASE_INTEGRATION=1 + LAKEBASE_HOST/USER/PASSWORD to run",
)


def _client_from_env() -> LakebaseClient:
    if not _HAS_STATIC_CREDS:
        _reset_client_for_tests()
        return get_lakebase_client()
    return LakebaseClient(
        host=os.environ["LAKEBASE_HOST"],
        port=int(os.environ.get("LAKEBASE_PORT", "5432")),
        database=os.environ.get("LAKEBASE_DATABASE") or "mip_app_state",
        user=os.environ["LAKEBASE_USER"],
        password=os.environ["LAKEBASE_PASSWORD"],
        sslmode=os.environ.get("LAKEBASE_SSLMODE", "require"),
    )


def _apply_schema(client: LakebaseClient) -> None:
    schema_path = Path(__file__).resolve().parents[2] / "lakebase" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    # psycopg executes multi-statement strings fine via execute(); each
    # ``CREATE ... IF NOT EXISTS`` is idempotent.
    client.execute(sql)


def test_lakebase_audit_round_trip() -> None:
    client = _client_from_env()
    _apply_schema(client)

    store = LakebaseAuditStore(client=client)
    req_id = str(uuid4())

    written = store.write(
        actor="integration-test@entrada.ai",
        action="view_borrower_360",
        entity_type="borrower",
        entity_id="B-48291",
        payload_json={"opportunity_score": 92, "request_id": req_id},
        evidence_ids=["ev-int-1"],
        event_type="VIEW_BORROWER",
        subject_clip="int-test-clip",
        request_id=req_id,
    )

    # Pull back, find our row by the unique request_id marker we wrote.
    events = store.list(limit=50)
    found = [e for e in events if e.request_id == req_id]
    assert len(found) == 1
    e = found[0]
    assert e.event_id == written.event_id
    assert e.event_type == "VIEW_BORROWER"
    assert e.actor == "integration-test@entrada.ai"
    assert re.fullmatch(r"clip_ref_[0-9a-f]{12}", e.subject_clip or "")
    assert e.payload_json.get("request_id") == req_id
    assert e.payload_json.get("opportunity_score") == 92

    # Cleanup -- UPDATE/DELETE are revoked on PUBLIC, but the service
    # account running this test is presumed to have DELETE via a
    # separate integration-test grant. If DELETE isn't granted, the
    # cleanup is best-effort and the row persists -- that's fine for
    # a dev Lakebase instance.
    with contextlib.suppress(Exception):
        client.execute(
            "DELETE FROM mip_app.action_audit WHERE request_id = %(r)s",
            {"r": req_id},
        )
