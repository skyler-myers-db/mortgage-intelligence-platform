"""Red flags for the live source-readiness ledger (``GET /api/admin/sources``).

Moved verbatim out of ``tools/verify_live.py`` (W5e, file-size headroom for the
approve-probe and data-age changes): every core Cotality / UC gold source must
be live with a row count, ``last_updated`` and a fresh ``checked_at``; every
first-party source is live or a disclosed synthetic demo feed; the pending
Building Permits feed is never reported live.

Data-age decision (data-freshness design 2026-10-26, fail-closed since the
post-W5 round 2): ``checked_at`` stays fresh within
``FAIL_CLOSED_MAX_SOURCE_AGE_DAYS`` (3, W5e's threshold) unless the weekly
schedule is proven live. :func:`resolve_max_source_age_days` reads the jobs of
the operator's own profile (a read-only ``databricks jobs list``, the same CLI
profile whose OAuth token the probes use) and widens the threshold to
``SCHEDULED_REFRESH_MAX_SOURCE_AGE_DAYS``, the weekly cadence plus 1 day (8),
only when a ``mip_scheduled_refresh`` job exists with an ``UNPAUSED`` schedule
on the bundle's weekly cron (databricks.yml, Fridays 06:00 America/Chicago:
FRED -> gold -> lifecycle -> KPI). An absent job, a ``PAUSED`` one, another
cron, or a jobs list that cannot be read keeps 3. ``--max-source-age-days``
overrides both. The schedule lands only through a GO governed ``deploy.sh``
run; until one does, ``checked_at`` on dev moves only when gold is rebuilt (a
full governed ``deploy.sh`` run, or an operator-run interim refresh) and the
3-day flag fires between rebuilds. Silver stays on demand because the Cotality
eval share is static. Each stale flag prints the measured age against the
threshold, so a reader sees how stale, not only that it is.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: The threshold whenever the weekly schedule is not proven live (W5e).
FAIL_CLOSED_MAX_SOURCE_AGE_DAYS = 3
#: mip_scheduled_refresh's cadence and cron on the dev live install
#: (databricks.yml). tests/unit/test_refresh_schedule_contract.py pins the
#: cron, its time zone and this cadence together.
SCHEDULED_REFRESH_CADENCE_DAYS = 7
SCHEDULED_REFRESH_CRON = "0 0 6 ? * FRI *"
SCHEDULED_REFRESH_TIMEZONE = "America/Chicago"
SCHEDULED_REFRESH_MAX_SOURCE_AGE_DAYS = SCHEDULED_REFRESH_CADENCE_DAYS + 1
#: Every default in this module is the fail-closed one.
DEFAULT_MAX_SOURCE_AGE_DAYS = FAIL_CLOSED_MAX_SOURCE_AGE_DAYS
#: The job's bundle name, bare or with the ``mode: development`` prefix
#: (``[dev <user>] ``). Only the job id is ever reported, never the prefix.
_SCHEDULED_REFRESH_JOB_NAME = re.compile(r"(?:\[dev [^\]]+\] )?mip_scheduled_refresh")
_JOBS_LIST_TIMEOUT_S = 120


@dataclass(frozen=True)
class SourceAgeThreshold:
    """The data-age threshold verify_live applies, and why (printed in the report)."""

    days: int
    basis: str


def scheduled_refresh_threshold(list_jobs: Callable[[], Any]) -> SourceAgeThreshold:
    """8 days only when an UNPAUSED ``mip_scheduled_refresh`` on the weekly cron is read back; else 3.

    ``list_jobs`` returns the Jobs API list (``[{"job_id", "settings": {...}}]``).
    Any exception or unexpected shape is an unreadable list and keeps the
    fail-closed threshold.
    """

    fail_closed = FAIL_CLOSED_MAX_SOURCE_AGE_DAYS
    try:
        jobs = list_jobs()
    except Exception as exc:  # noqa: BLE001 - an unreadable jobs list fails closed
        return SourceAgeThreshold(fail_closed, f"fail-closed: the jobs list is unreadable ({type(exc).__name__})")
    if not isinstance(jobs, list):
        return SourceAgeThreshold(fail_closed, "fail-closed: the jobs list is unreadable (not a JSON list)")
    candidates = [
        job
        for job in jobs
        if isinstance(job, dict)
        and isinstance(job.get("settings"), dict)
        and _SCHEDULED_REFRESH_JOB_NAME.fullmatch(str(job["settings"].get("name") or ""))
    ]
    if not candidates:
        return SourceAgeThreshold(fail_closed, "fail-closed: no mip_scheduled_refresh job in the workspace")
    for job in candidates:
        schedule = job["settings"].get("schedule")
        if (
            isinstance(schedule, dict)
            and schedule.get("pause_status") == "UNPAUSED"
            and schedule.get("quartz_cron_expression") == SCHEDULED_REFRESH_CRON
            and schedule.get("timezone_id") == SCHEDULED_REFRESH_TIMEZONE
        ):
            return SourceAgeThreshold(
                SCHEDULED_REFRESH_MAX_SOURCE_AGE_DAYS,
                f"mip_scheduled_refresh job {job.get('job_id')} is UNPAUSED on the weekly cron (cadence + 1 day)",
            )
    statuses = sorted({_schedule_label(job["settings"].get("schedule")) for job in candidates})
    return SourceAgeThreshold(
        fail_closed,
        f"fail-closed: mip_scheduled_refresh is not UNPAUSED on the weekly cron ({', '.join(statuses)})",
    )


def _schedule_label(schedule: Any) -> str:
    if schedule is None:
        return "unscheduled"
    if not isinstance(schedule, dict):
        return "unreadable schedule"
    status = str(schedule.get("pause_status") or "no pause status")
    return "UNPAUSED on another cron" if status == "UNPAUSED" else status


def _cli_jobs_list(profile: str) -> Any:
    """The read-only ``databricks jobs list`` of ``profile`` (the CLI pages through every job)."""

    result = subprocess.run(
        ["databricks", "jobs", "list", "-p", profile, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=_JOBS_LIST_TIMEOUT_S,
    )
    return json.loads(result.stdout)


def resolve_max_source_age_days(profile: str) -> SourceAgeThreshold:
    """The default threshold for ``profile``'s workspace: see :func:`scheduled_refresh_threshold`."""

    return scheduled_refresh_threshold(lambda: _cli_jobs_list(profile))


def _source_readiness_flags(rows: list[Any], *, max_age_days: int = DEFAULT_MAX_SOURCE_AGE_DAYS) -> list[str]:
    flags: list[str] = []
    by_name = {
        str(row.get("name")): row
        for row in rows
        if isinstance(row, dict) and row.get("name") is not None
    }
    core_sources = {
        "Cotality Public Records",
        "Voluntary Lien",
        "MMA Mortgage Analytics",
        "CLIP",
        "Owner Link",
        "AVM",
        "FRED Market Rates",
        "MLS Listings",
        "Cotality HELOC Propensity",
        "Cotality Refi Propensity",
        "UC Gold Borrower 360",
        "UC Gold Lead Scores",
        "UC Gold Lead Population",
        "UC Gold Segment Population",
        "UC Gold Borrower Dossier",
    }
    first_party_sources = {
        "First-party LOS / Applications",
        "First-party Servicing Portfolio",
        "First-party CRM / Campaigns",
        "First-party Customer Interactions",
        "First-party Product Balances",
    }
    for source_name in sorted(core_sources):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing core source `{source_name}`")
            continue
        if row.get("status") != "live":
            flags.append(f"admin.sources: {source_name} status={row.get('status')} expected live")
        if int(row.get("rows") or 0) <= 0:
            flags.append(f"admin.sources: {source_name} has no row count proof")
        if not row.get("last_updated"):
            flags.append(f"admin.sources: {source_name} missing last_updated")
        if not row.get("checked_at"):
            flags.append(f"admin.sources: {source_name} missing checked_at")
        elif (stale := _stale_checked_at_flag(source_name, row.get("checked_at"), max_age_days)) is not None:
            flags.append(stale)
    for source_name in sorted(first_party_sources):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing first-party source `{source_name}`")
            continue
        if row.get("status") not in {"live", "demo_synthetic"}:
            flags.append(
                f"admin.sources: {source_name} status={row.get('status')} expected live/demo_synthetic"
            )
        if bool(row.get("synthetic_demo")) and row.get("status") != "demo_synthetic":
            flags.append(f"admin.sources: {source_name} synthetic_demo not disclosed")
        if int(row.get("rows") or 0) <= 0:
            flags.append(f"admin.sources: {source_name} has no row count proof")
        if not row.get("last_updated"):
            flags.append(f"admin.sources: {source_name} missing last_updated")
        if not row.get("checked_at"):
            flags.append(f"admin.sources: {source_name} missing checked_at")
        elif (stale := _stale_checked_at_flag(source_name, row.get("checked_at"), max_age_days)) is not None:
            flags.append(stale)
    for source_name in ("Building Permits",):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing pending source `{source_name}`")
        elif row.get("status") == "live":
            flags.append(f"admin.sources: {source_name} cannot be live until the feed is loaded")
    return flags


def _checked_at_age(value: Any) -> timedelta | None:
    """How old a ``checked_at`` stamp is, or None when it is missing or unparseable."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    candidates = [
        raw,
        raw.replace(" ", "T"),
        raw.replace("Z", "+00:00"),
        raw.replace(" ", "T").replace("Z", "+00:00"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return datetime.now(UTC) - parsed.astimezone(UTC)
    return None


def _is_stale_checked_at(value: Any, *, max_age: timedelta = timedelta(days=DEFAULT_MAX_SOURCE_AGE_DAYS)) -> bool:
    age = _checked_at_age(value)
    return age is None or age > max_age


def _format_age_days(age: timedelta) -> str:
    days = age.total_seconds() / 86400
    return f"{days:.0f} d" if days >= 10 else f"{days:.1f} d"


def _stale_checked_at_flag(source_name: str, value: Any, max_age_days: int) -> str | None:
    """The stale flag with the measured age, or None while ``checked_at`` is fresh."""
    if not _is_stale_checked_at(value, max_age=timedelta(days=max_age_days)):
        return None
    age = _checked_at_age(value)
    measured = "unparseable" if age is None else _format_age_days(age)
    return f"admin.sources: {source_name} checked_at is stale ({measured} > {max_age_days} d)"
