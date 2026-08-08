"""Shared assertions for growth co-pilot prompt refusals.

The guard battery used to raise one catch-all sentence, so the laundering
batteries pinned that prose ("reviewed, non-PII"). Refusals now name their
guard family (fair lending, instruction override, cross-lender, unavailable
source, PII, unreviewed criterion), and the stable thing to pin is the
machine code -- not the copy.

``GROWTH_REFUSAL_MESSAGE_RE`` is built from the reason vocabulary itself, so
re-wording a refusal can never silently stop these batteries from checking
that the text is refused. It is also *stricter* than the old substring: it
proves the rejection came from the growth guard rather than from any
incidental ValidationError.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from backend.schemas.growth_agent_refusal import (
    GROWTH_PROMPT_REFUSAL_REASONS,
    growth_prompt_refusal_from_errors,
)

# Matches any governed refusal reason. Use with ``pytest.raises(match=...)``
# when a battery only needs "this objective is refused".
GROWTH_REFUSAL_MESSAGE_RE = "|".join(
    re.escape(reason) for reason in GROWTH_PROMPT_REFUSAL_REASONS.values()
)


GROWTH_REFUSAL_CODES = frozenset(GROWTH_PROMPT_REFUSAL_REASONS)


def growth_refusal_code(exc: ValidationError) -> str | None:
    """Return the guard family behind a ValidationError, or None."""

    refusal = growth_prompt_refusal_from_errors(exc.errors())
    return refusal.code if refusal is not None else None


def assert_refused_with_audit(response: Any, *, code: str | None = None) -> str:
    """Assert a co-pilot refusal: 422, a governed code, and an audit row.

    Replaces the old "422 and the catch-all sentence is in the body" check.
    A refusal that reaches the lender without a compliance record is the
    defect this contract exists to prevent, so the audit event id is part of
    the assertion, not an optional extra.
    """

    assert response.status_code == 422, response.text
    body = response.json()
    reason = body.get("refusal_reason")
    assert reason in GROWTH_REFUSAL_CODES, body
    if code is not None:
        assert reason == code, body
    assert body.get("audit_event_id"), body
    return str(reason)


def assert_refusal_isolation(dependencies: Sequence[Any]) -> None:
    """Assert a refusal touched nothing but the audit ledger.

    Takes the ``isolated_growth_dependencies`` fixture tuple
    ``(sql, lakebase, audit_store)``: the first two must be untouched, and
    the audit store must hold the refusal record and nothing else.
    """

    *inert, audit_store = dependencies
    for dependency in inert:
        assert dependency.mock_calls == [], dependency
    assert_only_refusal_audit_writes(audit_store)


def assert_only_refusal_audit_events(audit_store: Any) -> None:
    """Same contract as :func:`assert_only_refusal_audit_writes`, for a real store.

    Used where the battery holds an ``InMemoryAuditStore`` and reads back
    ``list()`` rather than inspecting mock calls.
    """

    events = list(audit_store.list())
    assert events, "a refused co-pilot prompt must write an audit record"
    for event in events:
        assert event.action == "growth_agent.refused_prompt", event


def assert_only_refusal_audit_writes(audit_store: Any) -> None:
    """The audit store saw refusal records and nothing else.

    The batteries used to assert ``mock_calls == []`` -- no writes at all.
    That is now the wrong contract: the refusal itself must be recorded. What
    must still be absent is every *other* write (runs, monitors, drafts).
    """

    calls = list(audit_store.mock_calls)
    # ``mock_calls`` also records follow-on calls against the returned mock
    # (``write().event_id.__str__``); compare on the root attribute.
    roots = {str(call[0]).split(".", 1)[0].split("(", 1)[0] for call in calls}
    assert roots <= {"write"}, f"unexpected audit calls on a refused prompt: {sorted(roots)}"
    writes = [call for call in calls if call[0] == "write"]
    assert writes, "a refused co-pilot prompt must write an audit record"
    for call in writes:
        assert call.kwargs.get("action") == "growth_agent.refused_prompt", call
