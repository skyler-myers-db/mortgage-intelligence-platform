"""Unit tests for the ``backend.services.job_trigger`` module.

Covers three invariants called out in the 2026-04-22 lifecycle-sync
rework:

* ``trigger_lifecycle_sync`` calls ``WorkspaceClient.jobs.run_now``
  exactly once per approval (when outside the debounce window).
* A burst of approvals inside the debounce window coalesces into a
  single ``run_now`` call.
* Any exception raised by the SDK (auth error, network error, missing
  job) is swallowed — the caller never sees it.

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
def _reset_trigger_state() -> None:
    """Clear the module-level debounce + job-id cache between tests."""
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


def test_trigger_debounces_clustered_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two approvals inside the debounce window produce ONE run_now."""
    ws = _stub_workspace()
    _install_fake_sdk(monkeypatch, ws)

    job_trigger.trigger_lifecycle_sync(reason="approval")
    job_trigger.trigger_lifecycle_sync(reason="approval")
    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert ws.jobs.run_now.call_count == 1


def test_trigger_refires_after_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    """A call past the debounce window fires a second run_now."""
    ws = _stub_workspace()
    _install_fake_sdk(monkeypatch, ws)

    # Fake monotonic clock: first call at t=0, second at t=120 (past
    # the 60-s debounce). We patch ``time.monotonic`` *inside* the
    # job_trigger module so other imports keep the real clock.
    clock = iter([0.0, 120.0])
    monkeypatch.setattr(job_trigger.time, "monotonic", lambda: next(clock))

    job_trigger.trigger_lifecycle_sync(reason="approval")
    job_trigger.trigger_lifecycle_sync(reason="approval")

    assert ws.jobs.run_now.call_count == 2


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

    We spy on ``trigger_lifecycle_sync`` at the import site the router
    uses (``backend.api.outreach``). BackgroundTasks runs after the
    response is committed but inside TestClient it runs synchronously,
    so asserting ``called`` is safe.
    """
    from backend.api import outreach as outreach_mod

    calls: list[dict[str, Any]] = []

    def _spy(*, reason: str = "approval") -> None:
        calls.append({"reason": reason})

    monkeypatch.setattr(outreach_mod, "trigger_lifecycle_sync", _spy)

    client = TestClient(app)
    resp = client.post(
        "/api/outreach/approve",
        json={
            "borrower_id": "B-48291",
            "offer_code": "HELOC-STD",
            "actor": "anonymous",
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(calls) == 1
    assert calls[0]["reason"] == "approval"
