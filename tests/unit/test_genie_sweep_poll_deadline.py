"""Deep-sweep sub-analyses get a deadline sized for their real latency.

Live measurement 2026-08-10: the planned cross-population scans took 82s,
94s and 120s while the shallow sections took ~40s. The interactive 45s
polling deadline therefore timed out exactly the deepest sections, leaving
fewer than ``_MIN_PLANNED`` usable ones, so ``run_planned_sweep`` aborted and
the user got a single-turn answer.
"""

from __future__ import annotations

import inspect

from backend.services.repositories import databricks_genie_sweep as sweep


def test_sweep_deadline_exceeds_the_measured_deep_section_latency() -> None:
    # The slowest measured section was 120s; the per-section deadline must
    # clear it with headroom.
    assert sweep._SWEEP_POLL_TIMEOUT_S >= 150


def test_sweep_finishes_inside_the_apps_gateway_timeout() -> None:
    """Databricks Apps returns 504 at ~300s (live probe 2026-08-10: 300.7s).

    The whole sweep — fan-out plus synthesis plus the surrounding turn — has
    to land inside that or the user gets a gateway error instead of an answer.
    """

    assert sweep._SWEEP_WALL_BUDGET_S <= 240
    # A section that outruns the whole budget could never contribute.
    assert sweep._SWEEP_POLL_TIMEOUT_S <= sweep._SWEEP_WALL_BUDGET_S


def test_fan_out_covers_a_full_deep_plan_in_one_wave() -> None:
    # Deep plans ask for up to _MAX_PLANNED_DEEP; running them in one wave is
    # what keeps the wall budget achievable.
    assert sweep._SWEEP_MAX_WORKERS >= 8


def test_sub_analyses_are_asked_with_the_sweep_deadline() -> None:
    source = inspect.getsource(sweep.run_planned_sweep)
    assert "poll_timeout_s=_SWEEP_POLL_TIMEOUT_S" in source
    assert "allow_sweep=False" in source


def test_repository_and_clients_accept_the_override() -> None:
    from backend.services.genie_client import GenieClient, ResilientGenieClient
    from backend.services.repositories.databricks_genie import (
        DatabricksGenieRepository,
    )

    for func in (
        GenieClient.ask,
        ResilientGenieClient.ask,
        DatabricksGenieRepository.respond,
    ):
        assert "poll_timeout_s" in inspect.signature(func).parameters


def test_interactive_default_is_unchanged_when_no_override_is_given() -> None:
    from backend.services.genie_client import GenieClient

    assert inspect.signature(GenieClient.ask).parameters["poll_timeout_s"].default is None
    assert inspect.signature(GenieClient.__init__).parameters["timeout_s"].default == 45
