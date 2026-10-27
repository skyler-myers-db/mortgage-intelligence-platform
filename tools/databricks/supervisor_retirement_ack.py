"""Explicit operator acknowledgement before a non-runtime Supervisor is retired.

The blue/green Supervisor cutover replaces the canonical-name Supervisor
whenever its creator is not the configured agent-runtime service principal
(``agentic_supervisor_endpoint.plan_supervisor_agent``), pins it in the signed
cutover journal (``cutover_agent_runtime_supervisor.pin_journal``) and, after
the green release gates, permanently deletes it and its serving endpoint
(``retired_serving_query_groups.retire_pinned_supervisor``). For a Supervisor a
person (or another service principal) created, that deletion cannot be undone,
so it needs the operator to name the exact Supervisor first:

    export MIP_RETIRE_HUMAN_SUPERVISOR=<supervisor id>

The value is the immutable Supervisor id itself, never a boolean, so a stale
or blanket value can never acknowledge a different Supervisor. A runtime-owned
Supervisor needs no acknowledgement: replacing it is the reviewed blue/green
path. ``deploy_prestop_probes`` checks this read-only before step 0a stops the
App; ``pin_journal`` re-checks it before the destructive tuple becomes durable;
``retire_pinned_supervisor`` checks it again right before it deletes a pinned
Supervisor that still exists, so a journal pinned by earlier code, resumed in a
later run or recovered as stale is never retired unacknowledged.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from tools.databricks.agent_runtime_access import assert_runtime_creator

ACKNOWLEDGEMENT_ENV = "MIP_RETIRE_HUMAN_SUPERVISOR"
_BOOLEAN_LIKE = frozenset(
    {"1", "0", "true", "false", "yes", "no", "y", "n", "on", "off", "accept", "ok"}
)


class UnacknowledgedSupervisorRetirement(RuntimeError):
    """A non-runtime Supervisor would be deleted without the operator naming it."""


def supervisor_retirement_acknowledgement(environ: Mapping[str, str] | None = None) -> str:
    """Return the operator's acknowledged Supervisor id, or ``""`` when unset."""

    source = os.environ if environ is None else environ
    return str(source.get(ACKNOWLEDGEMENT_ENV) or "").strip()


def is_runtime_created(creator: object, *, runtime_application_id: str) -> bool:
    """Classify the creator exactly as the Supervisor planner does."""

    try:
        assert_runtime_creator(
            creator,
            application_id=runtime_application_id,
            resource="Supervisor agent",
        )
    except RuntimeError:
        return False
    return True


def supervisor_retirement_refusal(
    *,
    supervisor_id: str,
    creator: object,
    runtime_application_id: str,
    acknowledgement: str,
    endpoint: str = "",
    canonical_name: str = "",
) -> str | None:
    """Return why retiring this Supervisor is refused, or ``None`` when allowed."""

    if is_runtime_created(creator, runtime_application_id=runtime_application_id):
        return None
    supervisor = str(supervisor_id or "").strip()
    if not supervisor:
        return "the canonical Supervisor has no immutable id; manual governance review is required"
    ack = str(acknowledgement or "").strip()
    if ack == supervisor:
        return None
    creator_text = str(getattr(creator, "value", creator) or "").strip() or "<missing>"
    endpoint_text = f" and its serving endpoint {endpoint!r}" if endpoint else ""
    lines = [
        f"Supervisor {supervisor!r} was created by {creator_text!r}, not by the configured "
        f"agent-runtime service principal {runtime_application_id.strip()!r}.",
        "The deploy would replace it and then permanently delete the Supervisor"
        f"{endpoint_text}; this cannot be undone.",
    ]
    if ack.casefold() in _BOOLEAN_LIKE:
        lines.append(f"{ACKNOWLEDGEMENT_ENV} must be the Supervisor id itself, not a boolean.")
    elif ack:
        lines.append(f"{ACKNOWLEDGEMENT_ENV} names {ack!r}, not this Supervisor.")
    lines.append(
        f"To accept the deletion: export {ACKNOWLEDGEMENT_ENV}={supervisor} and re-run."
    )
    if canonical_name:
        lines.append(
            "To keep it: rename it off the canonical name "
            f"{canonical_name!r} to a name outside the reserved replacement names (a "
            "renamed Supervisor is neither pinned nor deleted) and "
            "remove the App service principal's access to its serving endpoint, which the "
            "global serving audit would otherwise refuse."
        )
    return " ".join(lines)


def require_supervisor_retirement_acknowledgement(
    supervisor_id: str,
    creator: object,
    runtime_application_id: str,
    acknowledgement: str | None = None,
) -> None:
    """Fail closed unless a non-runtime Supervisor's retirement was acknowledged."""

    refusal = supervisor_retirement_refusal(
        supervisor_id=supervisor_id,
        creator=creator,
        runtime_application_id=runtime_application_id,
        acknowledgement=(
            supervisor_retirement_acknowledgement()
            if acknowledgement is None
            else acknowledgement
        ),
    )
    if refusal is not None:
        raise UnacknowledgedSupervisorRetirement(refusal)
