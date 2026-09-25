"""Closed vocabularies of a Genie completion job, and the stage sink.

Audit 2026-09-21 ``genie-01`` / ``delivery-04``. The governed completion of a
submitted live turn (verification, cross-check, rewrite, the deep-research
sweep, the output policy and the audited record) used to be one silent
blocking POST. It now runs as a server-side job whose stage the browser polls.
Every word the poll can carry is authored here, server-side, in a closed set:
no model text, no exception text, no question text.

The sink
--------
``report_stage`` is the one call the repository pipeline makes. It is a no-op
unless a completion runner installed a sink with :func:`stage_sink` AND the
call comes from the thread that installed it. Sweep sub-turns run on a worker
pool, so they never report, whatever that executor does with the context.
A report never changes the answer: the callback's failures are swallowed
here, and the runner's own callback is best-effort on top of that.

``"Answer ready"`` is deliberately absent from every label: that sentence
belongs to the client, at the moment it holds a renderable answer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum


class GenieJobStage(StrEnum):
    """Where a completion job is; the ``stage`` CHECK in lakebase/schema.sql."""

    QUEUED = "queued"
    COLLECTING = "collecting"
    REPAIRING = "repairing"
    VERIFYING = "verifying"
    CROSS_CHECKING = "cross_checking"
    REWRITING = "rewriting"
    PLANNING = "planning"
    RESEARCHING = "researching"
    SYNTHESIZING = "synthesizing"
    FINALIZING = "finalizing"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


class GenieJobStatus(StrEnum):
    """Lifecycle of a completion job; the ``status`` CHECK in lakebase/schema.sql."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class GenieJobFailureKind(StrEnum):
    """Why a job failed; the ``failure_kind`` CHECK in lakebase/schema.sql."""

    DEPENDENCY_DOWN = "dependency_down"
    UPSTREAM_ERROR = "upstream_error"
    INTERNAL = "internal"


#: Server-authored stage copy. The poll ships the label, never a free string.
GENIE_JOB_STAGE_LABELS: dict[GenieJobStage, str] = {
    GenieJobStage.QUEUED: "Queued for governed completion",
    GenieJobStage.COLLECTING: "Collecting Genie's finished turn",
    GenieJobStage.REPAIRING: "Asking Genie to regenerate governed SQL",
    GenieJobStage.VERIFYING: "Verifying the answer against its rows",
    GenieJobStage.CROSS_CHECKING: "Cross-checking against the governed metric framing",
    GenieJobStage.REWRITING: "Rewriting the summary from the verified figures",
    GenieJobStage.PLANNING: "Planning governed sub-analyses",
    GenieJobStage.RESEARCHING: "Running governed sub-analyses",
    GenieJobStage.SYNTHESIZING: "Writing the cross-section summary from verified results",
    GenieJobStage.FINALIZING: "Applying the output policy and recording the answer",
    GenieJobStage.DONE: "Governed answer recorded",
    GenieJobStage.FAILED: "Genie could not complete this question",
    GenieJobStage.EXPIRED: "This Genie answer expired before it was collected",
}

#: Canned hints per failure family. Exception text never reaches the wire.
GENIE_JOB_FAILURE_HINTS: dict[GenieJobFailureKind, str] = {
    GenieJobFailureKind.DEPENDENCY_DOWN: (
        "A governed data service was unavailable while this answer was being "
        "verified. Ask the question again in a moment."
    ),
    GenieJobFailureKind.UPSTREAM_ERROR: (
        "Genie returned a response that could not be completed. Retry, or "
        "rephrase the question."
    ),
    GenieJobFailureKind.INTERNAL: (
        "The governed completion stopped unexpectedly. Ask the question again."
    ),
}

GENIE_JOB_EXPIRED_HINT = (
    "This answer is no longer available here. Check History, or ask the question again."
)

StageCallback = Callable[[GenieJobStage, int | None, int | None], None]


@dataclass(frozen=True)
class _SinkBinding:
    callback: StageCallback
    owner_thread: int


_STAGE_SINK: ContextVar[_SinkBinding | None] = ContextVar("mip_genie_stage_sink", default=None)


@contextmanager
def stage_sink(callback: StageCallback) -> Iterator[None]:
    """Route :func:`report_stage` calls made on THIS thread to ``callback``."""

    token = _STAGE_SINK.set(_SinkBinding(callback, threading.get_ident()))
    try:
        yield
    finally:
        _STAGE_SINK.reset(token)


def report_stage(
    stage: GenieJobStage,
    parts_done: int | None = None,
    parts_planned: int | None = None,
) -> None:
    """Report where the completion is; a no-op outside the owning runner thread."""

    binding = _STAGE_SINK.get()
    if binding is None or binding.owner_thread != threading.get_ident():
        return
    try:
        binding.callback(stage, parts_done, parts_planned)
    except Exception:  # noqa: BLE001 - progress reporting never fails an answer
        return


__all__ = [
    "GENIE_JOB_EXPIRED_HINT",
    "GENIE_JOB_FAILURE_HINTS",
    "GENIE_JOB_STAGE_LABELS",
    "GenieJobFailureKind",
    "GenieJobStage",
    "GenieJobStatus",
    "StageCallback",
    "report_stage",
    "stage_sink",
]
