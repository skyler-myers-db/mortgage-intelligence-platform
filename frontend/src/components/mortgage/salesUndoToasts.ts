/**
 * Undo on the assign and distribute toasts (audit states-07 item 1) and the
 * sales writes' Retry-After countdowns (states-08 item 3).
 *
 * A successful assign or distribution raises today's success toast (the
 * count and the LEAD_ASSIGN / LEAD_DISTRIBUTE audit id) with one action,
 * Undo. Undo is a compensating, audited write the server may refuse:
 *
 *   - Pessimistic: nothing on screen changes before the 200. The rows are
 *     re-applied from the response (a restored row back to its officer, a
 *     released row with no restore to unassigned) through `onReverted`.
 *   - One release per Undo: the single assignment's id, or the run's own
 *     distribution request id (captured before the intent settled). Its
 *     request id is minted for the intent `release:<id>`, so a Retry after a
 *     429 sends the same id and the server replays instead of writing twice.
 *   - The Undo's own toast offers no Undo. Undo exists only for assignment
 *     and distribution: never for approve, reject or any other decision.
 *   - A 409 shows CLIENT copy keyed by the response's closed `reason`, never
 *     the server message (describeApiError never shows server detail).
 *
 * The release client and mutations are lazy (lib/mutations/salesLoop): this
 * module only `import()`s them when Undo is pressed, so the LeadTable chunk
 * carries this file and nothing else. The Undo window is the success
 * toast's lifetime (8 s, paused on hover or focus, F8 reaches it).
 *
 * TODO(W5e w5-lead-table-compile): fold toastSalesWriteFailure onto
 * leadWriteFailureToast's retry form once both lanes have merged.
 */
import type { QueryClient } from '@tanstack/react-query';
import { ApiError, clientFailureReason } from '../../lib/apiTransport';
import type { RestoreSkippedReason } from '../../lib/apiClients/salesLoop';
import { toast } from '../../lib/toast';
import type { LeadAssignment } from '../../types';
import { toastWriteFailure } from './leadWriteFailureToast';

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

/** What the Undo needs from the surface that raised the toast. */
export interface SalesUndoContext {
  queryClient: QueryClient;
  /** The intent's request id, minted once and reused until it settles. */
  requestIdFor: (fingerprint: string) => string;
  settle: (fingerprint: string) => void;
  /** Re-apply the rows from the 200: released rows, and those handed back. */
  onReverted: (released: LeadAssignment[], restored: LeadAssignment[]) => void;
}

export interface SalesAssignResult {
  assigned_count: number;
  assignments: LeadAssignment[];
  audit_event_id: string | null;
}

const loadSalesLoop = () => import('../../lib/mutations/salesLoop');

async function failureSentence(error: ApiError, subject: string): Promise<string | null> {
  const module: typeof import('../ui/AsyncFailure') | undefined = await import('../ui/AsyncFailure');
  return module ? module.failureSentence(error, subject) : null;
}

/** A 429 that names a wait: its Retry is held behind the toast's countdown. */
export function isCountdown429(error: unknown): error is ApiError & { retryAfterMs: number } {
  return error instanceof ApiError
    && error.status === 429
    && error.retryAfterMs !== null
    && error.retryAfterMs > 0
    && clientFailureReason(error) === null;
}

/**
 * Run `attempt`, handing it the way to run itself again (a 429's Retry).
 * Module-level on purpose: a hook-local function that names itself inside its
 * own closure is a React Compiler bailout (hoisted function reference).
 */
export function withRetry<T>(attempt: (retry: () => void) => T): T {
  const retry = (): void => {
    void attempt(retry);
  };
  return attempt(retry);
}

/**
 * A sales write that failed. A 429 that names a wait keeps a Retry, held
 * disabled behind the toast's countdown; `retry` re-runs the SAME intent (its
 * request id is not settled, so the server replays). Anything else is the
 * queue's ordinary write-failure toast.
 */
export function toastSalesWriteFailure(title: string, error: unknown, subject: string, retry: () => void): void {
  if (!isCountdown429(error)) {
    toastWriteFailure(title, error, subject);
    return;
  }
  const retryAfterMs = error.retryAfterMs;
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
    // The release chunk did not load (a stale deploy): no request was sent.
    toast.error(UNDO_FAILED, { detail: "The undo couldn't start. Reload the page, then try again." });
    return;
  }
  const copy = error.status === 409 && typeof error.reason === 'string'
    ? UNDO_CONFLICT_COPY[error.reason]
    : undefined;
  if (copy) {
    toast.error(UNDO_FAILED, { detail: copy });
    return;
  }
  toastSalesWriteFailure(UNDO_FAILED, error, 'the undo', retry);
}

function officer(assignment: LeadAssignment): string {
  return assignment.assigned_to_label || 'the previous loan officer';
}

function undoAssignment(ctx: SalesUndoContext, assignment: LeadAssignment): void {
  const intent = `release:${assignment.assignment_id}`;
  const run = (): void => {
    const requestId = ctx.requestIdFor(intent);
    loadSalesLoop()
      .then((module) => module.releaseAssignmentWrite(ctx.queryClient, {
        borrowerId: assignment.borrower_id,
        assignmentId: assignment.assignment_id,
        requestId,
      }))
      .then(
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

function undoDistribution(ctx: SalesUndoContext, distributionRequestId: string, assignedCount: number): void {
  const intent = `release:${distributionRequestId}`;
  const run = (): void => {
    const requestId = ctx.requestIdFor(intent);
    loadSalesLoop()
      .then((module) => module.releaseDistributionWrite(ctx.queryClient, { distributionRequestId, requestId }))
      .then(
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

function assignedTitle(count: number): string {
  return `${count} ${count === 1 ? 'lead' : 'leads'} assigned`;
}

/** One lead to one loan officer: the success toast, with Undo. */
export function toastAssigned(ctx: SalesUndoContext, result: SalesAssignResult): void {
  const assignment = result.assignments[0];
  // Warm the release chunk (code only, never data) while the toast is up.
  void loadSalesLoop().catch(() => undefined);
  toast.success(assignedTitle(result.assigned_count), {
    auditEventId: result.audit_event_id,
    action: assignment ? { label: 'Undo', onAction: () => undoAssignment(ctx, assignment) } : null,
  });
}

/** A distribution run: the success toast, with Undo of that run's own rows. */
export function toastDistributed(ctx: SalesUndoContext, result: SalesAssignResult, distributionRequestId: string): void {
  void loadSalesLoop().catch(() => undefined);
  toast.success(assignedTitle(result.assigned_count), {
    auditEventId: result.audit_event_id,
    action: result.assigned_count > 0
      ? { label: 'Undo', onAction: () => undoDistribution(ctx, distributionRequestId, result.assigned_count) }
      : null,
  });
}
