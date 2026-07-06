"""Pre-warm + refresh-ahead for the hero lead-queue read path.

2026-06-11 audit P1-6: ``/api/leads`` was the slowest call in the product
(3.6-6.6s measured live) on the two hero routes (Lead Queue, Segment
Intelligence) whenever the repository cache missed — cold app start, TTL
expiry between route visits, or per-worker caches. The repository cache
itself works (warm hits measure ~0.95s end-to-end, dominated by payload
transfer), so the fix is to make the DEFAULT page shape never be cold:

* ``warm_default_lead_page`` executes the exact repository calls the
  ``GET /api/leads`` route issues for a default request (no filters,
  default limit), populating the same singleton cache the route reads.
* ``backend.main`` runs it once at startup (after the warehouse warm). When
  ``settings.mip_leads_warm_interval_s`` is positive, it then re-runs below
  the ``mip_cache_ttl_s`` default (300s) so the entry is refreshed *ahead*
  of expiry and booth/demo loads always hit cache. Deployed Apps override
  that interval to 0 by default so idle workspaces can auto-stop.

Cache-key parity between this module and the route is pinned by
``tests/unit/test_lead_warm.py``: it warms through a counting fake SQL
client, then drives the real route, and asserts zero additional SQL
statements. If a route default drifts, that test fails loudly rather than
the warm silently heating the wrong key.

Layering note: this is a services-layer module; it must not import from
``backend.api``. The route's default limit (500) is therefore re-declared
here and the parity test asserts it equals ``backend.api.leads.DEFAULT_LEAD_LIMIT``.
"""

from __future__ import annotations

from typing import Any

from backend.schemas.portfolio import PortfolioCriteria

# Must equal backend.api.leads.DEFAULT_LEAD_LIMIT (test-pinned).
DEFAULT_LEAD_PAGE_LIMIT = 500

# Exact kwargs the route hands the repository for a filterless default
# request: every Query param at its declared default, csv filters parsed
# to None, and approval/outreach "any" normalised to None. NOTE the route
# is NOT criteria-free by default — `marketing_eligibility` defaults to
# "Eligible only" (fail-closed contactability), so the default request
# carries a PortfolioCriteria and lands on the borrower_360 path. That is
# exactly why the default page is the slowest query in the product and
# why this warmer exists.
LIST_DEFAULT_KWARGS: dict[str, Any] = {
    "portfolio_criteria": PortfolioCriteria(marketing_eligibility="Eligible only"),
    "segment": None,
    "portfolio_id": None,
    "limit": DEFAULT_LEAD_PAGE_LIMIT,
    "state": None,
    "zip_code": None,
    "county_fips": None,
    "state_codes": None,
    "zip_codes": None,
    "borrower_ids": None,
    "segment_codes": None,
    "segment_mode": "any",
    "target_lender_ref": None,
    "cohort_id": None,
    "funnel_stage": None,
    "approval_status": None,
    "outreach_status": None,
    "aged_days": None,
}

COUNT_DEFAULT_KWARGS: dict[str, Any] = {
    key: value for key, value in LIST_DEFAULT_KWARGS.items() if key != "limit"
}


def warm_default_lead_page(repository: Any | None = None) -> dict[str, int]:
    """Run the default-page list + count through the shared repository cache.

    Returns ``{"leads": <rows cached>, "total": <count cached>}``. Raises on
    failure — the caller (startup hook / re-warm loop) owns log-and-continue
    semantics so a warehouse blip never breaks boot or kills the loop.
    """
    if repository is None:
        from backend.services.repositories.factory import get_lead_repository

        repository = get_lead_repository()

    leads = repository.list(**LIST_DEFAULT_KWARGS)
    count_fn = getattr(repository, "count", None)
    total = count_fn(**COUNT_DEFAULT_KWARGS) if callable(count_fn) else len(leads)
    return {"leads": len(leads), "total": int(total)}
