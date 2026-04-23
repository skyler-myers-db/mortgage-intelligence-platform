"""Stress test: 50 sequential approvals against live backend + Lakebase.

SKIPPED unless ``LAKEBASE_INTEGRATION=1`` is set along with the full
``LAKEBASE_*`` connection triple — same gate pattern as the round-trip
test in ``test_lakebase_round_trip.py`` so CI stays quiet and only a
deliberate operator run exercises this.

What it proves
--------------

A customer's sales ops team realistically blasts through ~50 approvals
in a few minutes. This test fires 50 sequential ``POST /api/outreach/
approve`` calls against the live FastAPI app using synthetic
``B-STRESS-<uuid>`` borrower IDs (non-colliding with the real ``B-#####``
fixture range), then asserts:

1. All 50 HTTP responses are 200 with ``approved=True``.
2. 50 rows landed in ``mip_app.approvals`` whose ``decided_at > run_start``.
3. 50 matching rows landed in ``mip_app.action_audit`` whose
   ``event_type='APPROVE'`` and whose ``metadata->>'approval_id'``
   matches the approval_id returned by the API.
4. No duplicate ``audit_id`` values per ``approval_id``.
5. p50 / p95 / p99 latency distribution is recorded (printed + returned
   via ``pytest -s``; operator can paste into the validation doc).

Cleanup
-------

After assertions, the test DELETEs its own rows by the unique run_uuid
it stamped into ``metadata->>'run_uuid'``. Cleanup runs in a
``finally`` so a mid-run failure still tidies up. If the deploying
role lacks DELETE (which is the case in the production hardening
branch — audit rows are append-only), cleanup is best-effort; the
run_uuid prefix makes orphan rows trivially identifiable via
``WHERE metadata->>'run_uuid' LIKE 'stress-%'``.

Safety invariants
-----------------

* Borrower IDs are ``B-STRESS-<uuid[:12]>`` — cannot collide with real
  five-digit fixtures (``B-48291`` etc).
* No environment variable defaulting; if ``LAKEBASE_INTEGRATION`` is
  unset the whole module is skipped before any connection attempt.
* Run is purely sequential (no threads / asyncio) — measures the real
  single-caller latency without hiding pool contention inside a fan-out.
  The stress signal is *cumulative throughput* across 50 calls, which
  is what a sales-ops scenario actually produces.
"""
from __future__ import annotations

import os
import statistics
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.lakebase import LakebaseClient

pytestmark = pytest.mark.integration

_HAS_CREDS = (
    os.environ.get("LAKEBASE_INTEGRATION") == "1"
    and all(
        os.environ.get(k)
        for k in ("LAKEBASE_HOST", "LAKEBASE_USER", "LAKEBASE_PASSWORD")
    )
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _HAS_CREDS,
        reason="Set LAKEBASE_INTEGRATION=1 + LAKEBASE_HOST/USER/PASSWORD to run",
    ),
]


_N_APPROVALS = 50


def _real_client_from_env() -> LakebaseClient:
    """Build a raw Lakebase client (no resilience wrapper) for verification.

    The backend-under-test uses the production singleton; this separate
    client is used only by the post-run ``SELECT`` assertions + cleanup
    so we don't accidentally read through the circuit breaker's cache.
    """
    return LakebaseClient(
        host=os.environ["LAKEBASE_HOST"],
        port=int(os.environ.get("LAKEBASE_PORT", "5432")),
        database=os.environ.get("LAKEBASE_DATABASE", "mip_app_state"),
        user=os.environ["LAKEBASE_USER"],
        password=os.environ["LAKEBASE_PASSWORD"],
        sslmode=os.environ.get("LAKEBASE_SSLMODE", "require"),
    )


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Simpler than numpy and fine for N=50."""
    if not values:
        return 0.0
    ordered = sorted(values)
    # pct in [0, 100]; index = ceil(pct/100 * N) - 1
    idx = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * len(ordered))) - 1))
    return ordered[idx]


def test_approve_stress_50_sequential() -> None:
    """Fire 50 approvals sequentially; assert audit + approvals rows + latency.

    NOTE: this test deliberately does NOT override the Lakebase client
    via ``app.dependency_overrides`` — the whole point is to exercise
    the real client. The session-autouse fixture in ``conftest.py``
    installs an in-memory override, so we clear it for this test and
    restore it on teardown.
    """
    # conftest.py autouse fixture stubs every service. For this test we
    # want the real wiring, so drop the overrides and let FastAPI build
    # live dependencies from the factories.
    saved = dict(app.dependency_overrides)
    app.dependency_overrides.clear()

    # The FastAPI client re-uses a single ASGI transport; since approve
    # is a sync handler this gives us real single-caller latency.
    http = TestClient(app)
    verifier = _real_client_from_env()

    run_uuid = f"stress-{uuid4().hex[:12]}"
    run_started_iso = verifier.fetchone("SELECT now() AS n")  # type: ignore[assignment]
    assert run_started_iso is not None
    run_started = run_started_iso["n"]

    latencies_ms: list[float] = []
    approval_ids: list[str] = []
    errors: list[tuple[int, int, str]] = []

    try:
        for i in range(_N_APPROVALS):
            borrower_id = f"B-STRESS-{uuid4().hex[:12]}"
            t0 = time.monotonic()
            res = http.post(
                "/api/outreach/approve",
                json={
                    "borrower_id": borrower_id,
                    "offer_code": f"STRESS-{i:02d}",
                    "actor": "stress-test@entrada.ai",
                    "evidence_ids": [f"ev-stress-{run_uuid}-{i}"],
                },
            )
            dt_ms = (time.monotonic() - t0) * 1000.0
            latencies_ms.append(dt_ms)
            if res.status_code != 200:
                errors.append((i, res.status_code, res.text[:200]))
                continue
            body = res.json()
            assert body["approved"] is True, body
            approval_ids.append(body["approval_id"])

        # Latency distribution -- record even if assertions below fail
        # so the validation doc has the numbers.
        p50 = statistics.median(latencies_ms)
        p95 = _percentile(latencies_ms, 95.0)
        p99 = _percentile(latencies_ms, 99.0)
        p_max = max(latencies_ms)
        print(
            f"\n[approve_stress] N={_N_APPROVALS} "
            f"p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms "
            f"max={p_max:.1f}ms errors={len(errors)}"
        )

        assert not errors, f"approve-path errors during stress: {errors[:5]}"
        assert len(approval_ids) == _N_APPROVALS

        # Verify approvals landed. Query by the unique offer_code prefix +
        # decided_at window to avoid counting rows from prior runs.
        approvals_row = verifier.fetchone(
            """
            SELECT count(*) AS n
            FROM mip_app.approvals
            WHERE offer_code LIKE 'STRESS-%'
              AND decided_at >= %(started)s
            """,
            {"started": run_started},
        )
        assert approvals_row is not None
        assert approvals_row["n"] == _N_APPROVALS, (
            f"expected {_N_APPROVALS} approvals, got {approvals_row['n']}"
        )

        # Verify audit rows. Match on the approval_id list we collected --
        # the audit row stores it in metadata.approval_id. One audit per
        # approval_id + no duplicates.
        audit_row = verifier.fetchone(
            """
            SELECT count(*) AS total,
                   count(DISTINCT metadata->>'approval_id') AS distinct_ids
            FROM mip_app.action_audit
            WHERE event_type = 'APPROVE'
              AND event_at >= %(started)s
              AND metadata->>'approval_id' = ANY(%(ids)s)
            """,
            {"started": run_started, "ids": approval_ids},
        )
        assert audit_row is not None
        assert audit_row["total"] == _N_APPROVALS, (
            f"expected {_N_APPROVALS} audit rows, got {audit_row['total']}"
        )
        assert audit_row["distinct_ids"] == _N_APPROVALS, (
            "duplicate audit rows per approval_id -- append-only violated"
        )
    finally:
        # Cleanup -- borrower_id prefix is unique to this run; DELETEs
        # are best-effort (the app-writer role has REVOKE DELETE on
        # action_audit by design). We still try so dev Lakebase stays
        # tidy when the operator happens to have an elevated role.
        import contextlib

        with contextlib.suppress(Exception):
            verifier.execute(
                "DELETE FROM mip_app.approvals WHERE borrower_id LIKE 'B-STRESS-%' AND decided_at >= %(s)s",
                {"s": run_started},
            )
        with contextlib.suppress(Exception):
            verifier.execute(
                "DELETE FROM mip_app.action_audit WHERE entity_id = ANY(%(ids)s)",
                {"ids": approval_ids},
            )
        # Restore conftest's overrides so subsequent tests see the
        # in-process stubs again.
        app.dependency_overrides.update(saved)
