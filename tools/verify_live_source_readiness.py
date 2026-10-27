"""Red flags for the live source-readiness ledger (``GET /api/admin/sources``).

Moved verbatim out of ``tools/verify_live.py`` (W5e, file-size headroom for the
approve-probe and data-age changes): every core Cotality / UC gold source must
be live with a row count, ``last_updated`` and a fresh ``checked_at``; every
first-party source is live or a disclosed synthetic demo feed; the pending
Building Permits feed is never reported live.

Data-age decision (data-freshness design 2026-10-26, replacing W5e's): the dev
live install refreshes weekly through ``mip_scheduled_refresh`` (databricks.yml,
Fridays 06:00 America/Chicago: FRED -> gold -> lifecycle -> KPI), so
``checked_at`` stays fresh within ``DEFAULT_MAX_SOURCE_AGE_DAYS``, that cadence
plus 1 day (8), unless the operator passes ``--max-source-age-days``; another
install passes its own cadence plus 1. Silver stays on demand because the
Cotality eval share is static. The schedule lands only through a GO governed
``deploy.sh`` run. Until one does, ``checked_at`` on dev moves only when gold
is rebuilt (a full governed ``deploy.sh`` run, or an operator-run interim
refresh; one ran on 2026-10-27) and ages between rebuilds, so the flag fires
once the last rebuild is older than the threshold. Each stale flag prints the
measured age against the threshold, so a reader sees how stale, not only that
it is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

#: mip_scheduled_refresh's cadence on the dev live install (databricks.yml,
#: Fridays 06:00 America/Chicago). tests/unit/test_refresh_schedule_contract.py
#: pins the cron and this constant together.
SCHEDULED_REFRESH_CADENCE_DAYS = 7
DEFAULT_MAX_SOURCE_AGE_DAYS = SCHEDULED_REFRESH_CADENCE_DAYS + 1


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
