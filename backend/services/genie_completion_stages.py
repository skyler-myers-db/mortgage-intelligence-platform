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

The one exception is a cancel (audit 2026-09-21 ``genie-03``). A runner may
install a ``cancelled`` predicate: after the callback, on the owning thread
only, a report raises :class:`GenieTurnCancelled` when it is true, so a
stopped turn ends at its next stage boundary instead of spending more Genie
and SQL calls. It derives from ``BaseException`` (the
``asyncio.CancelledError`` precedent), so no broad ``except Exception`` on the
governed path can turn it into a disclosed gap, a degraded answer or a
rescue. The runner also installs a ``commit`` hook:
:func:`commit_governed_record` is the governed record's one commit point,
called once before any audit write, token issuance or session recording;
its errors are never swallowed.

Verified sections (audit 2026-09-21 ``genie-01`` phase 1b): a runner may
also install a ``sections`` callback. :func:`report_sections` hands it the
current snapshot of the deep sweep's sections that passed their own checks,
on the owning thread only, like :func:`report_stage`. Its failures are
swallowed, it is never a cancel point and it never changes the answer.

``"Answer ready"`` is deliberately absent from every label: that sentence
belongs to the client, at the moment it holds a renderable answer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from backend.services.cooperative_cancel import CooperativeCancel


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
    CANCELLED = "cancelled"


class GenieJobStatus(StrEnum):
    """Lifecycle of a completion job; the ``status`` CHECK in lakebase/schema.sql."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


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
    GenieJobStage.CANCELLED: "Stopped at your request before the answer was recorded",
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

#: A cancelled job is not a failure: this app will not verify or record the
#: answer. It never claims that Genie's own message was cancelled.
GENIE_JOB_CANCELLED_HINT = (
    "You stopped this question before its answer was recorded. Ask it again to get an answer."
)

StageCallback = Callable[[GenieJobStage, int | None, int | None], None]
#: Receives the plan-ordered snapshot of verified sections so far.
SectionsCallback = Callable[[list[dict[str, Any]]], None]


class GenieTurnCancelled(CooperativeCancel):
    """The owner stopped this turn before its governed record existed.

    A ``BaseException`` on purpose (through ``CooperativeCancel``): the
    governed path has broad ``except Exception`` handlers (a failed sweep
    theme becomes a disclosed gap, a failed planner falls through to the
    single turn) that must never turn a cancel into more Genie work, and the
    resilience and observability layers pass it through as a stop, never a
    dependency failure. Only the completion runner catches it.
    """


@dataclass(frozen=True)
class _SinkBinding:
    callback: StageCallback
    owner_thread: int
    cancelled: Callable[[], bool] | None = None
    commit: Callable[[], None] | None = None
    sections: SectionsCallback | None = None


_STAGE_SINK: ContextVar[_SinkBinding | None] = ContextVar("mip_genie_stage_sink", default=None)
#: The cancel predicate of the work running in this context (W5c genie-03):
#: set on the runner's thread by ``stage_sink`` and on each sweep sub-turn's
#: pool thread by ``cancel_scope``, so a non-owner thread can stop too.
_CANCEL_SCOPE: ContextVar[Callable[[], bool] | None] = ContextVar("mip_genie_cancel_scope", default=None)


@contextmanager
def cancel_scope(predicate: Callable[[], bool] | None) -> Iterator[None]:
    """Make ``predicate`` this context's cancel predicate for the block.

    Reset in ``finally``: a pooled thread never carries it into its next task.
    """

    token = _CANCEL_SCOPE.set(predicate)
    try:
        yield
    finally:
        _CANCEL_SCOPE.reset(token)


@contextmanager
def stage_sink(
    callback: StageCallback,
    *,
    cancelled: Callable[[], bool] | None = None,
    commit: Callable[[], None] | None = None,
    sections: SectionsCallback | None = None,
) -> Iterator[None]:
    """Route :func:`report_stage` calls made on THIS thread to ``callback``.

    ``cancelled`` makes each report a cancel point (and is this thread's
    cancel scope); ``commit`` is the hook :func:`commit_governed_record`
    calls; ``sections`` receives :func:`report_sections` snapshots.
    """

    token = _STAGE_SINK.set(
        _SinkBinding(callback, threading.get_ident(), cancelled, commit, sections)
    )
    try:
        with cancel_scope(cancelled) if cancelled is not None else nullcontext():
            yield
    finally:
        _STAGE_SINK.reset(token)


def _owned_binding() -> _SinkBinding | None:
    binding = _STAGE_SINK.get()
    if binding is None or binding.owner_thread != threading.get_ident():
        return None
    return binding


def _true(predicate: Callable[[], bool] | None) -> bool:
    if predicate is None:
        return False
    try:
        return predicate() is True
    except Exception:  # noqa: BLE001 - an unreadable predicate is not a cancel
        return False


def _cancel_requested(binding: _SinkBinding) -> bool:
    return _true(binding.cancelled)


def cancel_probe() -> Callable[[], bool] | None:
    """The owning runner thread's cancel predicate, or None (elsewhere, or
    no runner). The sweep hands it to each sub-turn's ``cancel_scope``."""

    binding = _owned_binding()
    return binding.cancelled if binding is not None else None


def cooperative_cancel_point() -> None:
    """Raise :class:`GenieTurnCancelled` if this work was stopped (W5c).

    On the owning runner thread the sink's predicate decides; on any other
    thread (a sweep sub-turn) the context's ``cancel_scope``. It never
    reports a stage and never writes; without a runner it does nothing.
    """

    binding = _owned_binding()
    if binding is not None:
        if _cancel_requested(binding):
            raise GenieTurnCancelled()
        return
    if _true(_CANCEL_SCOPE.get()):
        raise GenieTurnCancelled()


def report_stage(
    stage: GenieJobStage,
    parts_done: int | None = None,
    parts_planned: int | None = None,
) -> None:
    """Report where the completion is; a no-op outside the owning runner thread.

    Raises :class:`GenieTurnCancelled` after the report when the runner's
    ``cancelled`` predicate is true (owner thread only). On any other thread
    it reports nothing but is still a cancel point: a sweep sub-turn's own
    stage boundaries stop it under its ``cancel_scope``.
    """

    binding = _owned_binding()
    if binding is None:
        cooperative_cancel_point()
        return
    # Progress reporting never fails an answer: a failing sink is swallowed.
    with suppress(Exception):
        binding.callback(stage, parts_done, parts_planned)
    if _cancel_requested(binding):
        raise GenieTurnCancelled()


def report_sections(snapshot: list[dict[str, Any]]) -> None:
    """Hand the verified-sections snapshot to the runner; owner thread only.

    A no-op without a sink, off the owning thread or without a ``sections``
    callback. Callback errors are swallowed, there is no cancel check, and
    nothing here changes the answer.
    """

    binding = _owned_binding()
    if binding is None or binding.sections is None:
        return
    with suppress(Exception):
        binding.sections(snapshot)


def commit_governed_record() -> None:
    """The governed record's one commit point (see the module docstring).

    A no-op without a sink, off the owning thread or without a commit hook
    (the job-less inline path). Otherwise the hook runs and its errors,
    ``GenieTurnCancelled`` included, propagate.
    """

    binding = _owned_binding()
    if binding is None or binding.commit is None:
        return
    binding.commit()


__all__ = [
    "GENIE_JOB_CANCELLED_HINT",
    "GENIE_JOB_EXPIRED_HINT",
    "GENIE_JOB_FAILURE_HINTS",
    "GENIE_JOB_STAGE_LABELS",
    "GenieJobFailureKind",
    "GenieJobStage",
    "GenieJobStatus",
    "GenieTurnCancelled",
    "SectionsCallback",
    "StageCallback",
    "cancel_probe",
    "cancel_scope",
    "commit_governed_record",
    "cooperative_cancel_point",
    "report_sections",
    "report_stage",
    "stage_sink",
]
