"""The Apps API behaviour spike S7 measured, which every fake Apps API in tests/ models.

Spike S7 ran on a throwaway App (2026-11-06 and 2026-11-10, Databricks CLI
v1.10.0, the SDK pinned in ``uv.lock``; the cutover's spike results, S7-1a,
S7-1b, S7-1c, S7-2 and S7-3). What it measured:

* ``apps start`` ALWAYS creates a NEW deployment (SNAPSHOT mode, 3 of 3
  starts) whose ``/src/`` snapshot is the current bytes of the App's
  ``default_source_code_path``, with the env-name set of the App's newest
  deployment.
* The start response and an ``apps.get`` right after it show neither
  ``active_deployment`` nor ``pending_deployment``; the compute is STARTING.
* The compute turns ACTIVE after 96-108 s (``COMPUTE_START_S``). Only then
  does the new id show: as ``pending_deployment`` for about 1 s
  (``PENDING_VISIBLE_S``; 0.5 s polls saw it once, 3 s and 5 s polls never;
  no ``active_deployment`` meanwhile), then as ``active_deployment``
  IN_PROGRESS, then SUCCEEDED or FAILED 3-6 s
  later (``TERMINAL_AFTER_S``, the trivial App). ``list_deployments`` shows
  it from the same moment.
* A STOPPED App's GET omits ``active_deployment`` (and
  ``pending_deployment``) entirely.
* A FAILED deployment DOES become active, takes the App down (nothing falls
  back) and moves ``default_source_code_path`` to its folder; a start on that
  App reruns the failed folder and env set.
* ``default_source_code_path`` is not settable: ``create-update`` rejects the
  mask, ``update`` returns the App and ignores the field.

A fake models the delay in reads, not seconds: ``HIDDEN_READS`` GET or
``list_deployments`` calls after a start see nothing of it, the next GET
sees it pending once, and every later read sees it active.
``tests/unit/test_fake_apps_measured.py`` drives each fake through these
and fails when one answers the way the code hoped instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

COMPUTE_START_S = (96, 108)
PENDING_VISIBLE_S = 1
TERMINAL_AFTER_S = (3, 6)
STOP_S = (40, 45)
# Reads after a start that show nothing of its deployment: more than one, so
# "the start response, then one GET" never sees it, as measured.
HIDDEN_READS = 3
# The create-update error text the spike captured for the default-source mask.
CREATE_UPDATE_MASKS = frozenset(
    {
        "description",
        "budget_policy_id",
        "usage_policy_id",
        "resources",
        "user_api_scopes",
        "compute_size",
        "compute_min_instances",
        "compute_max_instances",
        "compute_autoscaling",
        "git_repository",
        "git_source",
        "telemetry_export_destinations",
        "forward_user_access_token",
        "compatibility_flags",
        "compute_generation",
    }
)


class InvalidUpdateMask(ValueError):
    """``apps create-update`` with a mask outside the allowed set (``default_source_code_path``)."""


def check_create_update_mask(update_mask: str) -> None:
    unknown = {item.strip() for item in update_mask.split(",")} - CREATE_UPDATE_MASKS
    if unknown:
        raise InvalidUpdateMask("Invalid update mask")


@dataclass
class StartVisibility:
    """One start's deployment as the API shows it, read by read (spike S7).

    The first ``hidden`` reads (GETs and listings alike) see nothing. From
    then on a listing shows it, the first GET shows it as pending and every
    later GET as the active deployment. ``never`` is failure injection, not a
    measured shape: the compute turns ACTIVE but the deployment never shows.
    """

    deployment_id: str
    hidden: int = HIDDEN_READS
    never: bool = False
    phase: str = "hidden"  # "hidden", "listed" or "pending" (one GET), then "active"

    def read(self, *, is_get: bool) -> str:
        """Advance one read and return the phase it sees ("compute_only" for ``never``)."""

        if self.hidden > 0:
            self.hidden -= 1
            return "hidden"
        if self.never:
            return "compute_only"
        if self.phase == "hidden":
            self.phase = "pending" if is_get else "listed"
            return self.phase
        if is_get:
            self.phase = "active" if self.phase in {"pending", "active"} else "pending"
        return self.phase


class VirtualClock:
    """A monotonic clock that ``sleep`` advances, so a start's polls take no real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def virtual_start_clock(monkeypatch: Any) -> VirtualClock:
    """Patch ``app_start_deployment``'s clock: its start polls (every 5 s) cost no real time."""

    from tools.databricks import app_start_deployment

    clock = VirtualClock()
    monkeypatch.setattr(
        app_start_deployment, "time", SimpleNamespace(sleep=clock.sleep, monotonic=clock.monotonic)
    )
    return clock
