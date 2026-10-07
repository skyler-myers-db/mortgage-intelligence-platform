/**
 * The Undo itself and the 429 Retry toast (audit states-07 item 1, states-08
 * item 3), LAZY: salesUndoToasts loads this module when Undo is pressed or a
 * write is rate-limited, so neither the copy nor the release mutations ride
 * in the LeadTable chunk.
 *
 *   - Pessimistic: nothing on screen changes before the 200. The rows are
 *     re-applied from the response (a restored row back to its officer, a
 *     released row with no restore to unassigned) through `onReverted`.
 *   - One release per Undo: the single assignment's id, or the run's own
 *     distribution request id (captured before the intent settled). Its
 *     request id is minted for the intent `release:<id>`, so a Retry after a
 *     429 sends the same id and the server replays instead of writing twice.
 *   - The Undo's own toast offers no Undo.
 *   - A 409 shows CLIENT copy keyed by the response's closed `reason`, never
 *     the server message (describeApiError never shows server detail).
 */
import type { RestoreSkippedReason } from '../../lib/apiClients/salesLoop';
import { ApiError } from '../../lib/apiTransport';
import { releaseAssignmentWrite, releaseDistributionWrite } from '../../lib/mutations/salesLoop';
import { toast } from '../../lib/toast';
import type { LeadAssignment } from '../../types';
import { toastWriteFailure } from './leadWriteFailureToast';
import type { SalesUndoContext } from './salesUndoToasts';

const UNDO_FAILED = "Couldn't undo the assignment";

/** Client copy for the release 409s, keyed by the server's closed reason. */
export const UNDO_CONFLICT_COPY: Readonly<Record<string, string>> = {
  assignment_started: 'The loan officer has already started on this lead; reassign instead of undoing.',
  assignment_not_active: 'This assignment is no longer the active one; refresh before undoing.',
  assignee_inactive: 'The assigned loan officer is no longer active; refresh before undoing.',
  request_id_conflict: 'This undo was already used for another assignment; refresh before undoing.',
};

const RESTORE_SKIPPED_COPY: Readonly<Record<RestoreSkippedReason, string>> = {
  previous_assignee_inactive: 'The previous loan officer is no longer active, so the lead is unassigned.',
  previous_assignee_out_of_scope: 'The previous loan officer is outside your team, so the lead is unassigned.',
  lead_not_approved: 'The lead is no longer approved, so it was not handed back.',
  lead_not_contactable: 'The lead is no longer contactable, so it was not handed back.',
};

async function failureSentence(error: ApiError, subject: string): Promise<string | null> {
  const module: typeof import('../ui/AsyncFailure') | undefined = await import('../ui/AsyncFailure');
  return module ? module.failureSentence(error, subject) : null;
}

/** A rate-limited write: describeApiError's body, and a Retry held behind the countdown. */
export function raiseRetryToast(title: string, error: ApiError, retryAfterMs: number, subject: string, retry: () => void): void {
  const raise = (detail: string | null) => {
    toast.error(title, { detail, action: { label: 'Retry', onAction: retry }, retryAfterMs });
  };
  failureSentence(error, subject).then(
    (sentence) => raise(sentence || error.message || null),
    () => raise(error.message || null),
  );
}

function undoFailure(error: unknown, retry: () => void): void {
  if (!(error instanceof ApiError)) {
    toastWriteFailure(UNDO_FAILED, error, 'the undo');
    return;
  }
  const copy = error.status === 409 && typeof error.reason === 'string' ? UNDO_CONFLICT_COPY[error.reason] : undefined;
  if (copy) {
    toast.error(UNDO_FAILED, { detail: copy });
    return;
  }
  if (error.status === 429 && error.retryAfterMs !== null && error.retryAfterMs > 0) {
    raiseRetryToast(UNDO_FAILED, error, error.retryAfterMs, 'the undo', retry);
    return;
  }
  toastWriteFailure(UNDO_FAILED, error, 'the undo');
}

function officer(assignment: LeadAssignment): string {
  return assignment.assigned_to_label || 'the previous loan officer';
}

export function undoAssignment(ctx: SalesUndoContext, assignment: LeadAssignment): void {
  const intent = `release:${assignment.assignment_id}`;
  const run = (): void => {
    const requestId = ctx.requestIdFor(intent);
    releaseAssignmentWrite(ctx.queryClient, {
      borrowerId: assignment.borrower_id,
      assignmentId: assignment.assignment_id,
      requestId,
    }).then(
      (result) => {
        ctx.settle(intent);
        ctx.onReverted([result.released], result.restored ? [result.restored] : []);
        const detail = result.restored
          ? `Back with ${officer(result.restored)}.`
          : result.restore_skipped_reason
            ? RESTORE_SKIPPED_COPY[result.restore_skipped_reason]
            : 'The lead is unassigned.';
        toast.success('Assignment undone', { detail, auditEventId: result.audit_event_id });
      },
      (error: unknown) => undoFailure(error, run),
    );
  };
  run();
}

export function undoDistribution(ctx: SalesUndoContext, distributionRequestId: string, assignedCount: number): void {
  const intent = `release:${distributionRequestId}`;
  const run = (): void => {
    const requestId = ctx.requestIdFor(intent);
    releaseDistributionWrite(ctx.queryClient, { distributionRequestId, requestId }).then(
      (result) => {
        ctx.settle(intent);
        ctx.onReverted(result.released, result.restored);
        const parts = [
          result.restored_count > 0 ? `${result.restored_count} handed back` : null,
          result.restore_skipped_count > 0 ? `${result.restore_skipped_count} left unassigned` : null,
          result.skipped_started_count > 0 ? `${result.skipped_started_count} already started` : null,
          result.skipped_not_active_count > 0 ? `${result.skipped_not_active_count} no longer active` : null,
        ].filter((part): part is string => part !== null);
        toast.success(`${result.released_count} of ${assignedCount} assignments undone`, {
          detail: parts.length > 0 ? parts.join(' · ') : null,
          auditEventId: result.audit_event_ids[0] ?? null,
        });
      },
      (error: unknown) => undoFailure(error, run),
    );
  };
  run();
}
