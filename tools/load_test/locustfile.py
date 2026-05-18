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

Write-path tasks are available but deliberately opt-in via
``MIP_LOAD_TEST_WRITE=1``. They exercise governed outreach approval,
portfolio creation, and Genie action confirmation through the real API
contracts. Keeping them off by default prevents casual staging runs from
polluting Lakebase with approvals, campaigns, cohorts, or audit rows.

The borrower task chains: it calls /api/leads first, pulls a random
borrower_id from the response, and then fetches /api/borrowers/{id}.
This mimics the real UX (user sees a queue, clicks into a row) and
keeps the IDs grounded in whatever the live warehouse actually
returns -- no hardcoded fixture IDs that might not exist in prod.

The leads endpoint uses `segment` and `portfolio_id` filters, not `state`,
so this profile rotates segment codes and varies `limit` to stress
pagination boundaries without pinning a geography footprint.
"""
from __future__ import annotations

import os
import random
from uuid import uuid4

from locust import HttpUser, between, task

API_PREFIX = os.environ.get("MIP_API_PREFIX", "/api/v1").strip() or "/api/v1"
API_PREFIX = "/" + API_PREFIX.strip("/")


def _api_path(path: str) -> str:
    return f"{API_PREFIX}/{path.lstrip('/')}"


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


LOAD_TEST_WRITE_ENABLED = _env_enabled("MIP_LOAD_TEST_WRITE")
BORROWER_POOL_SIZE = max(1, int(os.environ.get("MIP_LOAD_TEST_BORROWER_POOL_SIZE", "50")))
GENIE_LOAD_QUESTION = os.environ.get(
    "MIP_LOAD_TEST_GENIE_QUESTION",
    "Break down the In-the-Money segment by state.",
)

# Segment codes the repository emits. Rotated at task time so the
# breaker + cache are exercised against multiple cache keys.
SEGMENTS = [
    "itm",
    "listed",
    "permit",
    "investor",
    "equity",
    "retention",
]

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
        # Attach the bearer token for every request if one is present in
        # the env. Databricks Apps traverses an OAuth proxy; a workspace
        # Bearer token short-circuits the redirect. Local runs against a
        # naked uvicorn don't need this header — Locust sends an empty
        # header rather than skipping if the env var isn't set.
        bearer = os.environ.get("MIP_BEARER_TOKEN", "").strip()
        if bearer:
            self.client.headers.update({"Authorization": f"Bearer {bearer}"})
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
            _api_path("leads"),
            params=params,
            name=f"{API_PREFIX}/leads",
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
            self._last_borrower_ids = [
                bid for bid in ids if isinstance(bid, str)
            ][:BORROWER_POOL_SIZE]

    @task(1)
    def health(self) -> None:
        """Shallow liveness probe. Expect p95 < 500ms."""
        self.client.get(_api_path("health"), name=f"{API_PREFIX}/health")

    @task(3)
    def portfolio_kpis(self) -> None:
        """Home-page KPI strip. Expect p95 < 1000ms (warm cache)."""
        # POST /preview is the closest thing to a KPI fetch --
        # the app uses it to render the home-page counts. Empty body
        # is accepted and returns a deterministic preview shape.
        self.client.post(
            _api_path("portfolio/preview"),
            json={},
            name=f"{API_PREFIX}/portfolio/preview",
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
            _api_path(f"borrowers/{bid}"),
            name=f"{API_PREFIX}/borrowers/{{id}}",
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
        self.client.get(_api_path("segments"), name=f"{API_PREFIX}/segments")

    def outreach_approve(self) -> None:
        """Opt-in governed write path: draft then approve one borrower."""
        if not self._last_borrower_ids:
            self._refresh_leads()
            if not self._last_borrower_ids:
                return
        bid = random.choice(self._last_borrower_ids)  # noqa: S311
        request_id = str(uuid4())
        draft_payload = {
            "borrower_id": bid,
            "channel": "email",
            "variant_name": "load_test",
        }
        with self.client.post(
            _api_path("outreach/draft"),
            json=draft_payload,
            name=f"{API_PREFIX}/outreach/draft",
            catch_response=True,
        ) as draft_resp:
            if draft_resp.status_code != 200:
                draft_resp.failure(f"draft returned {draft_resp.status_code}")
                return
            try:
                draft = draft_resp.json()
            except ValueError:
                draft_resp.failure("draft returned non-JSON body")
                return
            body = draft.get("body")
            offer_code = draft.get("offer_code")
            channel = draft.get("channel") or "email"
            if not isinstance(body, str) or not body.strip():
                draft_resp.failure("draft body missing")
                return
            if not isinstance(offer_code, str) or not offer_code.strip():
                draft_resp.failure("draft offer_code missing")
                return
        approve_payload = {
            "borrower_id": bid,
            "offer_code": offer_code,
            "channel": channel,
            "variant_name": "load_test",
            "rationale": "Concurrent load-test approval path.",
            "draft_body": body,
            "request_id": request_id,
        }
        with self.client.post(
            _api_path("outreach/approve"),
            json=approve_payload,
            name=f"{API_PREFIX}/outreach/approve",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"approve returned {resp.status_code}")

    def portfolio_create(self) -> None:
        """Opt-in Lakebase campaign write path."""
        payload = {
            "name": f"Load test portfolio {uuid4().hex[:10]}",
            "criteria": {
                "states": [random.choice(["CA", "CO", "FL", "IL", "TX"])],  # noqa: S311
                "min_equity_pct": 25,
            },
            "suppression_policy": {"source": "load_test", "max_contacts": 50},
            "message_variants": [
                {
                    "variant_name": "load_test_email",
                    "channel": "email",
                    "subject": "Review current mortgage options",
                    "body": "Governed load-test campaign variant.",
                    "weight_pct": 100,
                }
            ],
        }
        with self.client.post(
            _api_path("portfolio/create"),
            json=payload,
            name=f"{API_PREFIX}/portfolio/create",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"portfolio create returned {resp.status_code}")

    def genie_confirm(self) -> None:
        """Opt-in Genie action write path: ask, then confirm one emitted action."""
        with self.client.post(
            _api_path("genie/message"),
            json={"question": GENIE_LOAD_QUESTION},
            name=f"{API_PREFIX}/genie/message",
            catch_response=True,
        ) as message_resp:
            if message_resp.status_code != 200:
                message_resp.failure(f"genie message returned {message_resp.status_code}")
                return
            try:
                body = message_resp.json()
            except ValueError:
                message_resp.failure("genie message returned non-JSON body")
                return
            actions = body.get("actions") if isinstance(body, dict) else None
            if not isinstance(actions, list) or not actions:
                message_resp.failure("genie message returned no confirmable actions")
                return
            confirmable = [
                item
                for item in actions
                if isinstance(item, dict)
                and item.get("confirmation_token")
                and item.get("request_id")
                and item.get("action_type")
            ]
            action = next(
                (
                    item
                    for item in confirmable
                    if item.get("action_type") == "create_draft_campaign"
                ),
                confirmable[0] if confirmable else None,
            )
            if action is None:
                message_resp.failure("genie message returned actions without tokens")
                return
        payload = {
            "action_type": action.get("action_type"),
            "conversation_id": body.get("conversation_id"),
            "message_id": body.get("message_id"),
            "question_hash": body.get("question_hash"),
            "borrower_ids": action.get("borrower_ids") or [],
            "criteria": action.get("criteria") or {},
            "route": action.get("route"),
            "request_id": action.get("request_id"),
            "confirmed": True,
            "confirmation_token": action.get("confirmation_token"),
        }
        with self.client.post(
            _api_path("genie/actions"),
            json=payload,
            name=f"{API_PREFIX}/genie/actions",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"genie action returned {resp.status_code}")


if LOAD_TEST_WRITE_ENABLED:
    # Locust's @task decorator builds a class-level task list. Appending
    # these methods only when the env gate is set keeps default runs
    # exactly read-only while giving write paths weight 1 each.
    MipUser.tasks.extend([  # type: ignore[attr-defined]
        MipUser.outreach_approve,
        MipUser.portfolio_create,
        MipUser.genie_confirm,
    ])
