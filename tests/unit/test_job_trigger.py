"""Unit tests for warehouse-first lifecycle sync and durable Jobs recovery.

We also assert that the approval HTTP path schedules the trigger via
``BackgroundTasks`` so the HTTP 200 response ships before the trigger
hits the Databricks API.

These tests use ``monkeypatch`` to substitute a stub WorkspaceClient
in place of the real ``databricks.sdk`` import; no network or auth is
required.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import job_trigger


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, ws: Any) -> None:
    """Inject a fake ``databricks.sdk`` module exposing ``WorkspaceClient``.

    ``trigger_lifecycle_sync`` imports the SDK lazily inside the
    function body, so swapping ``sys.modules`` at test time is
    sufficient; no reimport dance needed.
    """
    fake_mod = types.ModuleType("databricks.sdk")
    fake_mod.WorkspaceClient = lambda: ws  # type: ignore[attr-defined]
    # databricks.__path__ must exist so ``from databricks.sdk import``
    # resolves the child module. Build both the parent and child.
    parent = types.ModuleType("databricks")
    parent.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "databricks", parent)
    monkeypatch.setitem(sys.modules, "databricks.sdk", fake_mod)


@pytest.fixture(autouse=True)
def _reset_trigger_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the job-id cache between tests and force explicit job mode."""
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_MODE", "job")
    job_trigger._reset_for_tests()


def _stub_workspace(run_id: int = 12345, job_id: int = 42) -> MagicMock:
    """Build a MagicMock WorkspaceClient that resolves a lifecycle job.

    The shape matches what ``_resolve_job_id`` expects:
    ``workspace.jobs.list(name=...)`` returns an iterable of objects
    with ``.job_id`` and ``.settings.name``; ``workspace.jobs.run_now``
    returns an object with ``.run_id``.
    """
    job = MagicMock()
    job.job_id = job_id
    job.settings = MagicMock()
    job.settings.name = "mip_sync_lifecycle_state"

    ws = MagicMock()
    ws.jobs.list.return_value = [job]
    run = MagicMock()
    run.run_id = run_id
    ws.jobs.run_now.return_value = run
    return ws


def test_default_trigger_uses_warehouse_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default product mode runs the warehouse-backed mirror, not a job."""
    from backend.services import lifecycle_sync
    from backend.services.lifecycle_sync import LifecycleSyncResult

    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_MODE", "warehouse")
    calls: list[tuple[bool, bool]] = []

    def _fake_sync(
        *, record_funnel_snapshot: bool, prune_legacy_defaults: bool
    ) -> LifecycleSyncResult:
        calls.append((record_funnel_snapshot, prune_legacy_defaults))
        return LifecycleSyncResult(
            lakebase_rows=2,
            mirrored_rows=10,
            funnel_snapshot_rows=7,
        )

    monkeypatch.setattr(lifecycle_sync, "sync_lifecycle_state_via_warehouse", _fake_sync)

    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert calls == [(False, False)]


def test_trigger_fires_run_now_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """First call to ``trigger_lifecycle_sync`` issues one ``run_now``."""
    ws = _stub_workspace()
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert ws.jobs.run_now.call_count == 1
    # The job_id resolved by ``_resolve_job_id`` is what's passed to
    # run_now -- never the string name.
    kwargs = ws.jobs.run_now.call_args.kwargs
    assert kwargs == {"job_id": 42}


def test_trigger_uses_bound_job_id_env_before_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Databricks App resource binding supplies the job id directly.

    The app service principal may only have CAN_MANAGE_RUN on that specific
    job, so it should not need workspace-wide jobs.list visibility.
    """
    ws = _stub_workspace(job_id=42)
    _install_fake_sdk(monkeypatch, ws)
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_JOB_ID", "98765")

    job_trigger.trigger_lifecycle_sync(reason="approval")

    ws.jobs.list.assert_not_called()
    assert ws.jobs.run_now.call_args.kwargs == {"job_id": 98765}


def test_forced_job_mode_does_not_process_debounce_clustered_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No app replica may suppress another accepted event in process memory."""
    ws = _stub_workspace()
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")
    job_trigger.trigger_lifecycle_sync(reason="approval")
    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert ws.jobs.run_now.call_count == 3


def test_clustered_job_calls_reuse_only_job_id_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Job-id lookup is cached, but durable run submission is not throttled."""
    ws = _stub_workspace()
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")
    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert ws.jobs.run_now.call_count == 2
    assert ws.jobs.list.call_count == 1


def test_warehouse_failure_submits_durable_retriable_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed cheap MERGE becomes a workspace-durable Jobs run."""
    from backend.services import lifecycle_sync

    ws = _stub_workspace(run_id=7788, job_id=42)
    _install_fake_sdk(monkeypatch, ws)
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_MODE", "warehouse")
    monkeypatch.setattr(
        lifecycle_sync,
        "sync_lifecycle_state_via_warehouse",
        lambda **_: (_ for _ in ()).throw(RuntimeError("warehouse unavailable")),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        job_trigger,
        "emit",
        lambda _log, event, **fields: events.append((event, fields)),
    )

    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert ws.jobs.run_now.call_args.kwargs == {"job_id": 42}
    assert any(
        event == "lifecycle_sync_retry_queued"
        and fields["run_id"] == 7788
        and fields["job_id"] == 42
        for event, fields in events
    )


def test_double_failure_is_explicit_and_points_to_durable_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If warehouse and Jobs are down, the durable source and repair are named."""
    from backend.services import lifecycle_sync

    ws = _stub_workspace()
    ws.jobs.run_now.side_effect = RuntimeError("jobs unavailable")
    _install_fake_sdk(monkeypatch, ws)
    monkeypatch.setenv("MIP_LIFECYCLE_SYNC_MODE", "warehouse")
    monkeypatch.setattr(
        lifecycle_sync,
        "sync_lifecycle_state_via_warehouse",
        lambda **_: (_ for _ in ()).throw(RuntimeError("warehouse unavailable")),
    )
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        job_trigger,
        "emit",
        lambda _log, event, **fields: events.append((event, fields)),
    )

    job_trigger.trigger_lifecycle_sync(reason="approval")

    terminal = [fields for event, fields in events if event == "lifecycle_sync_retry_unavailable"]
    assert terminal == [
        {
            "level": job_trigger.logging.ERROR,
            "mode": "warehouse",
            "reason": "approval",
            "durable_source": "mip_app.approvals,mip_app.call_dispositions",
            "repair_command": "databricks bundle run mip_sync_lifecycle_state -t <target>",
        }
    ]


def test_trigger_swallows_sdk_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any SDK exception is caught; caller sees a clean return."""
    ws = MagicMock()
    ws.jobs.list.side_effect = RuntimeError("simulated auth failure")
    # Fallback list call also raises -- belt-and-suspenders.
    _install_fake_sdk(monkeypatch, ws)

    # Must not raise.
    job_trigger.trigger_lifecycle_sync(reason="approval")

    # And since the resolve failed, run_now was never called.
    ws.jobs.run_now.assert_not_called()


def test_trigger_swallows_runnow_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failures from ``run_now`` itself are swallowed as well."""
    ws = _stub_workspace()
    ws.jobs.run_now.side_effect = RuntimeError("workspace unreachable")
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")
    # The approval endpoint would have already returned 200 to the
    # client at this point -- the test just verifies we didn't raise.


def test_trigger_missing_sdk_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """If databricks-sdk isn't installed, the trigger is a soft no-op."""
    monkeypatch.setitem(sys.modules, "databricks.sdk", None)

    # ImportError on the `from databricks.sdk import WorkspaceClient`
    # line is caught by the broad except inside the function. No raise.
    job_trigger.trigger_lifecycle_sync(reason="approval")


def test_trigger_unresolved_job_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the job name doesn't exist in the workspace, skip silently."""
    ws = MagicMock()
    ws.jobs.list.return_value = []
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")

    ws.jobs.run_now.assert_not_called()


def test_approval_endpoint_schedules_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/outreach/approve adds the trigger to BackgroundTasks.

    We spy on ``enqueue_lifecycle_trigger`` at the import site the
    router uses (``backend.api.outreach``). BackgroundTasks runs after
    the response is committed but inside TestClient it runs
    synchronously, so asserting ``called`` is safe.
    """
    from backend.api import outreach as outreach_mod

    calls: list[dict[str, Any]] = []

    def _spy(background: Any, *, reason: str = "approval") -> None:
        calls.append({"reason": reason})

    monkeypatch.setattr(outreach_mod, "enqueue_lifecycle_trigger", _spy)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "heloc",
            "actor": "anonymous",
            "draft_subject": "Your mortgage review",
            "draft_body": "Governed approval body. Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out.",
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert calls[0]["reason"] == "approval"


def test_job_id_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-05: cached job_id must re-resolve after ``_JOB_ID_TTL_SECONDS``.

    Without the TTL, a bundle redeploy that changes ``job_id`` would
    leave the process hitting the old id forever and the error path is
    swallowed silently. We fire three triggers across the TTL boundary
    and assert ``jobs.list`` was called twice (fresh resolve + post-TTL
    re-resolve), not once.
    """
    ws = _stub_workspace(job_id=42)
    _install_fake_sdk(monkeypatch, ws)

    # Shared mutable clock for the resolver's TTL bookkeeping.
    now = [0.0]
    monkeypatch.setattr(job_trigger.time, "monotonic", lambda: now[0])
    ttl = job_trigger._JOB_ID_TTL_SECONDS

    job_trigger.trigger_lifecycle_sync(reason="approval")
    # Advance past the job-id TTL.
    now[0] = ttl + 1.0
    job_trigger.trigger_lifecycle_sync(reason="approval")

    # run_now fired twice (one per trigger call) AND ``jobs.list`` was
    # consulted twice because the cache expired between calls.
    assert ws.jobs.run_now.call_count == 2
    assert ws.jobs.list.call_count == 2


def test_run_now_404_invalidates_job_id_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """R5-05: a 404 / not-found from ``run_now`` must drop the cached
    job_id so the next trigger re-resolves by name.

    Scenario: operator redeploys the bundle, the old job_id no longer
    exists. Without invalidation every subsequent approval would hit
    the dead id forever (404 swallowed by the outer except).
    """
    # First call: resolve returns job_id=42 but run_now raises a
    # 404-shaped error. Cache must be cleared.
    ws = _stub_workspace(job_id=42)

    class _ResourceDoesNotExist(Exception):
        pass

    # Spoof a 404 error class whose name matches the stale-detector.
    _ResourceDoesNotExist.__name__ = "ResourceDoesNotExist"
    ws.jobs.run_now.side_effect = _ResourceDoesNotExist("job 42 not found")
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")
    # Cache cleared by _invalidate_cached_job_id -- the second call must
    # re-list. Swap run_now to succeed this time to confirm the flow.
    ws.jobs.run_now.side_effect = None
    run = MagicMock()
    run.run_id = 777
    ws.jobs.run_now.return_value = run
    job_trigger.trigger_lifecycle_sync(reason="approval")

    # Two run_now attempts (first failed, second succeeded) AND two
    # list calls because the cache was invalidated by the 404.
    assert ws.jobs.run_now.call_count == 2
    assert ws.jobs.list.call_count == 2
