"""Red flags for the live source-readiness ledger (``GET /api/admin/sources``).

Moved verbatim out of ``tools/verify_live.py`` (W5e, file-size headroom for the
approve-probe and data-age changes): every core Cotality / UC gold source must
be live with a row count, ``last_updated`` and a fresh ``checked_at``; every
first-party source is live or a disclosed synthetic demo feed; the pending
Building Permits feed is never reported live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def _source_readiness_flags(rows: list[Any]) -> list[str]:
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
        elif _is_stale_checked_at(row.get("checked_at")):
            flags.append(f"admin.sources: {source_name} checked_at is stale")
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
        elif _is_stale_checked_at(row.get("checked_at")):
            flags.append(f"admin.sources: {source_name} checked_at is stale")
    for source_name in ("Building Permits",):
        row = by_name.get(source_name)
        if row is None:
            flags.append(f"admin.sources: missing pending source `{source_name}`")
        elif row.get("status") == "live":
            flags.append(f"admin.sources: {source_name} cannot be live until the feed is loaded")
    return flags


def _is_stale_checked_at(value: Any, *, max_age: timedelta = timedelta(days=3)) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
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
        return datetime.now(UTC) - parsed.astimezone(UTC) > max_age
    return True
