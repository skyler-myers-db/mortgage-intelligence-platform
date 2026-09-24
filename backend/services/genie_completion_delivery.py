"""What a Genie completion job stores, and what its owner is served.

Audit 2026-09-21 ``genie-01``, integrator corrections 1-2. The stored
``result_json`` is QUESTION-FREE and DE-AUTHORIZED, mirroring
``history_payload_json``:

* ``question`` is blanked. The envelope keeps one boolean saying whether the
  answer's question was the turn's own prompt of record, so delivery can put
  the prompt back from the status request, which hash-checks it against the
  progress token first. No column or JSON value ever holds the prompt.
* every action keeps its ``request_id`` but loses ``confirmation_token``.
  Delivery re-signs the actions for the polling actor with
  ``issue_response_action_tokens`` (same request ids, so a retried action
  still dedupes). A leaked row authorizes nothing.

Delivery is idempotent: every poll of a succeeded job returns the same
answer, with freshly signed tokens, until ``expires_at``.
"""

from __future__ import annotations

from typing import Any

from backend.services.genie_actions import issue_response_action_tokens
from backend.services.genie_answers import GenieCompletionJobStatus, GenieMessageResponse
from backend.services.genie_completion_jobs import GenieCompletionJob
from backend.services.genie_completion_stages import (
    GENIE_JOB_EXPIRED_HINT,
    GENIE_JOB_FAILURE_HINTS,
    GENIE_JOB_STAGE_LABELS,
    GenieJobFailureKind,
    GenieJobStage,
    GenieJobStatus,
)

_RESULT_VERSION = 1


def deauthorized_result(response: GenieMessageResponse, question: str) -> dict[str, Any]:
    """The job row's ``result_json``: no question text, no live authorization."""

    payload = response.model_dump(mode="json")
    question_bound = bool(question) and payload.get("question") == question
    payload["question"] = ""
    for action in payload.get("actions") or []:
        if isinstance(action, dict):
            action.pop("confirmation_token", None)
    return {"v": _RESULT_VERSION, "question_bound": question_bound, "response": payload}


def delivered_response(
    result: dict[str, Any],
    *,
    question: str,
    actor: str,
    live_campaign_run_marker: str | None,
) -> GenieMessageResponse | None:
    """Re-hydrate a stored answer for its (already verified) owner."""

    if result.get("v") != _RESULT_VERSION or not isinstance(result.get("response"), dict):
        return None
    response = GenieMessageResponse.model_validate(result["response"])
    if result.get("question_bound") is True:
        response.question = question
    issue_response_action_tokens(
        response,
        actor=actor,
        live_campaign_run_marker=live_campaign_run_marker,
    )
    return response


def job_status(
    job: GenieCompletionJob,
    *,
    question: str,
    actor: str,
    live_campaign_run_marker: str | None,
) -> GenieCompletionJobStatus:
    """The wire status of ``job``; the answer only once it succeeded."""

    status = job.status
    stage = job.stage
    failure_kind = job.failure_kind
    response: GenieMessageResponse | None = None
    if status is GenieJobStatus.SUCCEEDED:
        response = (
            delivered_response(
                job.result_json,
                question=question,
                actor=actor,
                live_campaign_run_marker=live_campaign_run_marker,
            )
            if job.result_json is not None
            else None
        )
        if response is None:
            # A succeeded row without a readable answer cannot be served.
            status, stage = GenieJobStatus.FAILED, GenieJobStage.FAILED
            failure_kind = GenieJobFailureKind.INTERNAL
    error_hint: str | None = None
    if status is GenieJobStatus.FAILED:
        error_hint = GENIE_JOB_FAILURE_HINTS[failure_kind or GenieJobFailureKind.INTERNAL]
    elif status is GenieJobStatus.EXPIRED:
        error_hint = GENIE_JOB_EXPIRED_HINT
    return GenieCompletionJobStatus(
        job_id=job.job_id,
        status=status,
        stage=stage,
        stage_label=GENIE_JOB_STAGE_LABELS[stage],
        parts_done=job.parts_done,
        parts_planned=job.parts_planned,
        terminal=status in _TERMINAL,
        failed=status in _FAILED,
        error_hint=error_hint,
        response=response,
    )


_TERMINAL = frozenset({GenieJobStatus.SUCCEEDED, GenieJobStatus.FAILED, GenieJobStatus.EXPIRED})
_FAILED = frozenset({GenieJobStatus.FAILED, GenieJobStatus.EXPIRED})


__all__ = ["deauthorized_result", "delivered_response", "job_status"]
