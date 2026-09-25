"""The governed record's one commit point, and the cancelled end of a job.

Audit 2026-09-21 ``genie-03``. A job-backed Genie turn the owner stops
before its governed record exists must record nothing: no ``genie.run_query``
RUN_GENIE row, no action tokens, no session row (the submit's own
``genie.message_submitted`` RUN_GENIE row was written before the job
existed). ``mip_app.genie_completion_jobs.recorded_at``
is that record's ONE commit point. The runner sets it with a conditional
UPDATE that requires ``cancel_requested_at IS NULL``, immediately before the
audit write, token issuance and session recording; the cancel route sets
``cancel_requested_at`` with a conditional UPDATE that requires
``recorded_at IS NULL``. Postgres row locks serialize the two statements and
the table's ``genie_completion_jobs_cancel_or_record_chk`` makes both being
set impossible even under a code bug.
"""

from __future__ import annotations

from typing import Literal

from backend.services import genie_completion_jobs as jobs
from backend.services.lakebase import LakebaseClient, LakebaseError
from backend.services.resilience import DependencyDownError

_COMMIT_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET recorded_at = now(), updated_at = now()
 WHERE job_id = %(job_id)s::uuid AND status = 'running' AND lease_owner = %(lease_owner)s
   AND cancel_requested_at IS NULL AND recorded_at IS NULL
RETURNING job_id::text AS job_id
"""

_CANCEL_STATE_SQL = """
SELECT cancel_requested_at IS NOT NULL AS cancel_requested
  FROM mip_app.genie_completion_jobs
 WHERE job_id = %(job_id)s::uuid
"""

_END_CANCELLED_SQL = """
UPDATE mip_app.genie_completion_jobs
   SET status = 'cancelled', stage = 'cancelled', parts_done = NULL, parts_planned = NULL,
       result_json = NULL, finished_at = now(), updated_at = now()
 WHERE job_id = %(job_id)s::uuid AND status IN ('queued', 'running')
   AND lease_owner = %(lease_owner)s
   AND cancel_requested_at IS NOT NULL AND recorded_at IS NULL
RETURNING job_id::text AS job_id
"""

CommitOutcome = Literal["committed", "cancelled", "lost"]


def commit_governed_record(lakebase: LakebaseClient, job_id: str) -> CommitOutcome:
    """Set ``recorded_at`` for a running job this process owns.

    ``cancelled`` when a cancel was requested first (the record must not be
    written), ``lost`` when this process no longer runs the job. The one
    Lakebase statement the governed thread waits on, after all Genie work.
    A Lakebase failure is a 503 ``lakebase``: nothing is recorded.
    """

    params = {"job_id": job_id, "lease_owner": jobs.PROCESS_ID}
    try:
        if lakebase.fetchone(_COMMIT_SQL, params) is not None:
            return "committed"
        state = lakebase.fetchone(_CANCEL_STATE_SQL, params)
    except (LakebaseError, DependencyDownError) as exc:
        raise jobs._unavailable(exc) from exc
    return "cancelled" if state is not None and state.get("cancel_requested") is True else "lost"


def end_cancelled(lakebase: LakebaseClient, job_id: str) -> bool:
    """End a cancel-requested, unrecorded job this process runs as cancelled.

    False when nothing matched (another process owns it, or it already
    ended); the lease then lapses and a read expires it.
    """

    row = lakebase.fetchone(_END_CANCELLED_SQL, {"job_id": job_id, "lease_owner": jobs.PROCESS_ID})
    return row is not None


__all__ = ["CommitOutcome", "commit_governed_record", "end_cancelled"]
