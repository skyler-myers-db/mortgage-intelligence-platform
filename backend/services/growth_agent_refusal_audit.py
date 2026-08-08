"""Compliance record for growth co-pilot prompts refused at the boundary.

The co-pilot guard battery lives in a pydantic validator, so a refused
objective is rejected by ``backend.main._request_validation_handler`` before
the route body ever runs. That left a fair-lending targeting attempt against
``/api/growth-agent/agent/run`` with **no** row in the audit trail -- the
first artifact a bank's model-risk reviewer asks for -- while the same prompt
on Ask Genie wrote ``genie.refused_prompt`` (persona audit, 2026-08-07).

This module is the growth-agent counterpart. It writes the same shape
(``action_type``/``refusal_reason``/``question_hash``) so one query over the
ledger returns refusals from both surfaces.

PII posture: the refused prompt is never read here. The guard hashes the
normalized objective when it raises, and only that truncated digest is
persisted -- consistent with the 2026-07-07 decision to strip pydantic
``input``/``ctx``/``url`` from 422 bodies.
"""

from __future__ import annotations

import logging
import re

from fastapi import Request

from backend.schemas.growth_agent_refusal import GrowthPromptRefusal
from backend.services.audit_store import get_audit_store, resolve_actor
from backend.services.genie_audit import audit_safe_letter_digest
from backend.services.observability import emit

log = logging.getLogger("backend.api.growth_agent.refusal_audit")

# Canonical (``/api/v1``) and compat (``/api``) mounts of the growth-agent
# router. Both carry the co-pilot surfaces whose guard battery raises
# ``GrowthPromptRefusalError``.
_GROWTH_AGENT_PATH_RE = re.compile(r"^/api(?:/v1)?/growth-agent(?:/|$)")


class GrowthPromptRefusalAuditError(RuntimeError):
    """The refusal record could not be persisted."""


def is_growth_agent_path(path: str) -> bool:
    return _GROWTH_AGENT_PATH_RE.match(path) is not None


def record_growth_prompt_refusal(request: Request, refusal: GrowthPromptRefusal) -> str:
    """Persist ``growth_agent.refused_prompt`` and return the event id.

    Raises ``GrowthPromptRefusalAuditError`` when the ledger write fails.
    Callers must fail closed on that: a refusal that leaves no record is the
    exact gap this module exists to close, so the caller surfaces a
    dependency error rather than a clean 422 with nothing written.
    """

    entity_id = f"growthprompt-{audit_safe_letter_digest(refusal.question_hash)}"
    factory = request.app.dependency_overrides.get(get_audit_store, get_audit_store)
    try:
        event = factory().write(
            actor=resolve_actor(request),
            action="growth_agent.refused_prompt",
            entity_type="growth_agent_prompt",
            entity_id=entity_id,
            # Keys come from the reviewed audit-metadata allowlist
            # (backend/services/audit_store.py::_ALLOWED_METADATA_KEYS) and
            # share the Genie refusal vocabulary so a compliance query on
            # refusal_reason spans both surfaces.
            payload_json={
                "action_type": "refused_prompt",
                "refusal_reason": refusal.code,
                "question_hash": refusal.question_hash,
                "route": request.url.path,
            },
        )
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed failure below
        emit(
            log,
            "growth_prompt_refusal_audit_failed",
            level=logging.ERROR,
            outcome="failed",
            # The attempt still reaches the operator log even when the
            # ledger is unreachable.
            refusal_reason=refusal.code,
            # ``entity_id``, not the hex digest: a 16-hex hash reads as
            # phone-shaped to the log scrubber and comes out redacted, which
            # is exactly when an operator needs to join it to the ledger.
            entity_id=entity_id,
            error_type=type(exc).__name__,
        )
        raise GrowthPromptRefusalAuditError("growth-agent refusal audit write failed") from exc
    event_id = str(event.event_id)
    emit(
        log,
        "growth_prompt_refused",
        level=logging.INFO,
        outcome="refused",
        refusal_reason=refusal.code,
        entity_id=entity_id,
    )
    return event_id
