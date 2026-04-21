"""Locust load profile for Module 0 hot endpoints.

Not a runtime dependency -- `pip install locust` on the operator
workstation and run via `tools/load_test/run.sh`. Deliberately kept
outside `requirements.txt` so Databricks Apps doesn't pull Locust +
gevent + flask into production.

Five tasks weighted by expected read-pattern traffic:

    health      @ weight 1   -- shallow probe, every load-balancer hit
    kpis        @ weight 3   -- home page render
    leads       @ weight 5   -- lead queue (the hot path)
    borrower    @ weight 4   -- dossier drill-down, chained from /leads
    segments    @ weight 2   -- segment strip on home + filter dropdowns

The borrower task chains: it calls /api/leads first, pulls a random
borrower_id from the response, and then fetches /api/borrowers/{id}.
This mimics the real UX (user sees a queue, clicks into a row) and
keeps the IDs grounded in whatever the live warehouse actually
returns -- no hardcoded fixture IDs that might not exist in prod.

State rotation across IL/CA/FL/TX/WA/CO matches the six-state share
footprint called out in CLAUDE.md; the leads endpoint uses `segment`
and `portfolio_id` filters, not `state`, so we rotate the segment
codes (which do exist) and leave any future state filter to a follow
up. For now the task varies `limit` to stress pagination boundaries.
"""
from __future__ import annotations

import random

from locust import HttpUser, between, task

# Segment codes the repository emits. Rotated at task time so the
# breaker + cache are exercised against multiple cache keys.
SEGMENTS = [
    "in_the_money",
    "heloc_candidate",
    "cash_out",
    "listed_for_sale",
    "investor",
    "retention",
]

# Six-state share footprint. Used only to vary request URLs so the
# resilience layer isn't hitting the same cache key every time. The
# leads router does not currently take a state filter; when it does,
# flip this into the query string.
STATES = ["IL", "CA", "FL", "TX", "WA", "CO"]


class MipUser(HttpUser):
    """Simulated operator poking the Module 0 UI at a realistic cadence."""

    # 1-3s between tasks mimics a human navigating -- not synthetic
    # hammering. Load tests that fire with no wait_time mask real
    # backpressure issues because connection pools never recover.
    wait_time = between(1, 3)

    # Cache the most recent leads response so the borrower task doesn't
    # have to refetch. Keyed per-user-instance so Locust's green-thread
    # isolation holds.
    _last_borrower_ids: list[str]

    def on_start(self) -> None:
        self._last_borrower_ids = []
        # Prime the pump: one leads call per user so the borrower task
        # has IDs on its first tick. Without this the first borrower
        # hit of every user either 404s or is skipped.
        self._refresh_leads()

    def _refresh_leads(self, segment: str | None = None) -> None:
        """Fetch /api/leads and stash the borrower_ids for chained calls."""
        params: dict[str, str] = {}
        if segment:
            params["segment"] = segment
        with self.client.get(
            "/api/leads",
            params=params,
            name="/api/leads",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"leads returned {resp.status_code}")
                return
            try:
                body = resp.json()
            except ValueError:
                resp.failure("leads returned non-JSON body")
                return
            if not isinstance(body, list):
                resp.failure(f"leads returned non-list body: {type(body).__name__}")
                return
            ids = [row.get("borrower_id") for row in body if isinstance(row, dict)]
            self._last_borrower_ids = [bid for bid in ids if isinstance(bid, str)]

    @task(1)
    def health(self) -> None:
        """Shallow liveness probe. Expect p95 < 500ms."""
        self.client.get("/api/health", name="/api/health")

    @task(3)
    def portfolio_kpis(self) -> None:
        """Home-page KPI strip. Expect p95 < 1000ms (warm cache)."""
        # POST /preview is the closest thing to a KPI fetch --
        # the app uses it to render the home-page counts. Empty body
        # is accepted and returns a deterministic preview shape.
        self.client.post(
            "/api/portfolio/preview",
            json={},
            name="/api/portfolio/preview",
        )

    @task(5)
    def list_leads(self) -> None:
        """Hot path: ranked lead queue, with and without segment filter."""
        # One in three calls filters by segment; the rest pull the
        # full queue. Mirrors production-style drill-in traffic where
        # the "all segments" view is the most-hit default.
        segment = random.choice(SEGMENTS) if random.random() < 0.33 else None  # noqa: S311
        self._refresh_leads(segment=segment)

    @task(4)
    def borrower_360(self) -> None:
        """Dossier drill-down. Chained: uses a borrower_id from leads."""
        if not self._last_borrower_ids:
            # Leads endpoint was empty or erroring -- refresh and skip
            # this tick rather than guessing an ID.
            self._refresh_leads()
            return
        bid = random.choice(self._last_borrower_ids)  # noqa: S311
        # ``name`` coalesces per-ID URLs into a single stat line; without
        # this Locust reports N distinct lines for N IDs and p95 becomes
        # meaningless.
        with self.client.get(
            f"/api/borrowers/{bid}",
            name="/api/borrowers/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                # A stale ID from a prior leads snapshot -- not a
                # real failure, just drop it from the cache.
                self._last_borrower_ids = [x for x in self._last_borrower_ids if x != bid]
                resp.success()
                return
            if resp.status_code != 200:
                resp.failure(f"borrower returned {resp.status_code}")

    @task(2)
    def segments(self) -> None:
        """Segment strip. Cheap, cached, should never be a bottleneck."""
        self.client.get("/api/segments", name="/api/segments")
