"""Shared doubles for the Genie completion-job suites (test-only).

A scripted repository (optionally held on a gate, or raising), an audit store
that records each write together with the correlation id bound when it was
written, and small request helpers for ``/message/complete`` and
``/message/status``. Used with ``tests/fixtures/genie_job_lakebase.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from backend.main import app
from backend.services.audit_store import get_audit_store
from backend.services.genie_answers import (
    GenieActionSuggestion,
    GenieMessageResponse,
    GenieProof,
)
from backend.services.genie_progress import (
    genie_question_binding_hash,
    genie_question_hash,
    mint_genie_progress_token,
)
from backend.services.lakebase import get_lakebase_client
from backend.services.observability import get_correlation_id
from backend.services.repositories.factory import get_genie_answer_repository
from tests.fixtures.genie_job_lakebase import FakeJobLakebase

ACTOR = "lo@example.com"
HEADERS = {"X-Forwarded-Email": ACTOR}
CONV = "conv-job-1"
MSG = "msg-job-1"
QUESTION = "How many borrowers are currently in the money by state?"


def token(*, actor: str = ACTOR, conversation_id: str = CONV, message_id: str = MSG, question: str = QUESTION) -> str:
    return mint_genie_progress_token(
        actor=actor,
        conversation_id=conversation_id,
        message_id=message_id,
        question_hash=genie_question_binding_hash(question),
    )


def open_cohort_action() -> GenieActionSuggestion:
    return GenieActionSuggestion(
        id="open-cohort",
        label="Open this cohort in Lead Queue",
        action_type="open_cohort",
        description="Navigate into the lead queue with this Genie result audited.",
        route="/lead-queue",
        borrower_ids=[],
        criteria={
            "source": "genie",
            "source_assets": ["mip.gold.borrower_360"],
            "visualization_kind": "metric",
            "row_count": 1,
            "sql_hash": "aggregate",
        },
    )


def answer(question: str = QUESTION, *, conversation_id: str = CONV, message_id: str = MSG) -> GenieMessageResponse:
    return GenieMessageResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        question=question,
        question_hash=genie_question_hash(question),
        answer="There are 124,946 borrowers in the money.",
        source="genie",
        trusted_assets=["mip.gold.borrower_360"],
        row_count=1,
        genie_status="COMPLETED",
        proof=GenieProof(
            source_assets=["mip.gold.borrower_360"],
            row_count=1,
            trusted=True,
            conversation_id=conversation_id,
            message_id=message_id,
        ),
        table_rows=[{"in_the_money_borrowers": "124946"}],
        actions=[open_cohort_action()],
    )


class FakeRepo:
    """``respond_existing`` returns ``response`` (or raises ``error``).

    ``gate`` holds every call until the test sets it, so a test can observe
    the job while it runs. ``calls`` counts completions actually executed.
    """

    def __init__(
        self,
        *,
        response: GenieMessageResponse | Callable[[str], GenieMessageResponse] | None = None,
        error: BaseException | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.gate = gate
        self.calls: list[dict[str, str]] = []
        self.started = threading.Event()
        self._lock = threading.Lock()

    def respond(self, question: str, conversation_id: str | None = None) -> GenieMessageResponse:
        raise AssertionError("sync respond must not run on the completion path")

    def respond_existing(self, question: str, *, conversation_id: str, message_id: str) -> GenieMessageResponse:
        with self._lock:
            self.calls.append({"question": question, "conversation_id": conversation_id, "message_id": message_id})
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(10), "the test never released the held completion"
        if self.error is not None:
            raise self.error
        if callable(self.response):
            return self.response(question)
        return self.response if self.response is not None else answer(question)


class FakeAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self.writes: list[dict[str, Any]] = []
        self.fail = fail
        self._lock = threading.Lock()

    def write(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("audit store down (fake)")
        with self._lock:
            self.writes.append({**kwargs, "_correlation_id": get_correlation_id()})

    def run_query_rows(self) -> list[dict[str, Any]]:
        return [write for write in self.writes if write.get("action") == "genie.run_query"]


def install(monkeypatch: Any, *, repo: Any, audit: FakeAudit, lakebase: FakeJobLakebase) -> None:
    monkeypatch.setitem(app.dependency_overrides, get_genie_answer_repository, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, get_audit_store, lambda: audit)
    monkeypatch.setitem(app.dependency_overrides, get_lakebase_client, lambda: lakebase)


def complete_body(*, respond_async: bool = True, question: str = QUESTION, progress_token: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "conversation_id": CONV,
        "message_id": MSG,
        "progress_token": progress_token or token(question=question),
        "question": question,
    }
    if respond_async:
        body["respond_async"] = True
    return body


def post_complete(client: TestClient, *, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
    return client.post("/api/genie/message/complete", json=complete_body(**kwargs), headers=headers or HEADERS)


def post_status(
    client: TestClient,
    job_id: str,
    *,
    headers: dict[str, str] | None = None,
    question: str = QUESTION,
    **overrides: Any,
) -> Any:
    body = {
        "conversation_id": CONV,
        "message_id": MSG,
        "progress_token": token(question=question),
        "question": question,
        "job_id": job_id,
        **overrides,
    }
    return client.post("/api/genie/message/status", json=body, headers=headers or HEADERS)


def wait_for_job(lakebase: FakeJobLakebase, *, statuses: tuple[str, ...] = ("succeeded", "failed", "expired"), timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with lakebase._lock:
            rows = list(lakebase.rows.values())
        if len(rows) == 1 and rows[0]["status"] in statuses:
            return dict(rows[0])
        time.sleep(0.01)
    raise AssertionError(f"job never reached {statuses}: {lakebase.rows}")
