"""A refresh-consistent cache for the ranked Lead Queue pages (D-audit-reads-a).

``DatabricksLeadRepository`` caches every ranked page under ``lead_list:`` for
``MIP_CACHE_TTL_S``. A paged view binds page 0's gold refresh stamp, and a
later page whose rows come from another refresh answers 409, so the reader
restarts the view at page 0. Without this module the restart could be served
the CACHED page 0 of the old refresh, and every Load next answered 409 again
until that entry expired: up to the TTL after every gold refresh, for every
reader of that filter set in the process. A cached old page past 0 looped the
same way behind a fresh page 0.

The rule: every uncached read records the newest ``refreshed_at`` its rows
carry (the watermark, which only moves forward). A cached page whose rows
carry an OLDER stamp than the watermark is a miss and is dropped, so once any
read in this process has seen a refresh, no page of the previous one is
served again and the restart reads fresh rows. A page none of whose rows
carries a stamp cannot be compared and is served as before.

A source that lags another (borrower_360 refreshed, lead_population not yet
rebuilt) is read uncached until it catches up: a cache miss, never a stale
page and never a loop. The same holds for a gold restore to an older refresh
until the next refresh or a restart. The ``lead_list:`` key prefix stays the
caller's literal (gold-version TTL scans key on it); only the cached value
carries the stamp.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from backend.schemas.lead import LeadSummary
from backend.services.observability import emit
from backend.services.repositories.databricks_lead_order import LeadPage
from backend.services.resilience import TTLCache

log = logging.getLogger(__name__)


def page_refreshed_at(leads: list[LeadSummary]) -> datetime | None:
    """The newest row stamp of a page, as an aware UTC time; None when no row has one."""

    stamps = [lead.row_refreshed_at for lead in leads if lead.row_refreshed_at is not None]
    if not stamps:
        return None
    return max(stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC) for stamp in stamps)


def _copy(leads: list[LeadSummary]) -> list[LeadSummary]:
    return [lead.model_copy(deep=True) for lead in leads]


@dataclass(frozen=True)
class _CachedLeadPage:
    page: LeadPage
    refreshed_at: datetime | None


class LeadPageCache:
    """Pages under a TTL, never served once a newer gold refresh was read."""

    def __init__(self, cache: TTLCache, ttl_s: float) -> None:
        self._cache = cache
        self._ttl_s = ttl_s
        self._lock = Lock()
        self._watermark: datetime | None = None

    def get(self, key: str) -> LeadPage | None:
        if self._ttl_s <= 0:
            return None
        cached = self._cache.get(key)
        if not isinstance(cached, _CachedLeadPage):
            return None
        if self._superseded(cached.refreshed_at):
            self._cache.invalidate(key)
            emit(log, "lead_page_cache_superseded", outcome="miss")
            return None
        return cached.page.copy_with(_copy(cached.page.leads))

    def put(self, key: str, page: LeadPage) -> LeadPage:
        """Record an uncached read's stamp, cache the page, and return a copy."""

        refreshed_at = self.observe(page.leads)
        if self._ttl_s > 0:
            self._cache.set(key, _CachedLeadPage(page.copy_with(_copy(page.leads)), refreshed_at), self._ttl_s)
        return page.copy_with(_copy(page.leads))

    def observe(self, leads: list[LeadSummary]) -> datetime | None:
        """Advance the watermark from an uncached read's rows (the identity reads too)."""

        refreshed_at = page_refreshed_at(leads)
        if refreshed_at is not None:
            with self._lock:
                if self._watermark is None or refreshed_at > self._watermark:
                    self._watermark = refreshed_at
        return refreshed_at

    def _superseded(self, refreshed_at: datetime | None) -> bool:
        if refreshed_at is None:
            return False
        with self._lock:
            return self._watermark is not None and refreshed_at < self._watermark
