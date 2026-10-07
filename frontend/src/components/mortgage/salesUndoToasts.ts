/**
 * Undo on the assign and distribute toasts (audit states-07 item 1) and the
 * sales writes' Retry-After countdowns (states-08 item 3).
 *
 * A successful assign or distribution raises today's success toast (the
 * count and the LEAD_ASSIGN / LEAD_DISTRIBUTE audit id) with one action,
 * Undo: a compensating, audited release the server may refuse. Undo exists
 * only for assignment and distribution, never for approve, reject or any
 * other decision. The Undo window is the success toast's lifetime (8 s,
 * paused on hover or focus, F8 reaches it).
 *
 * This module is static in the LeadTable chunk, so it is kept small: the
 * release itself, its copy and the 429 Retry toast live in salesUndoRun,
 * loaded when Undo is pressed or a write is rate-limited (a code-only warm-up
 * import runs while the toast is up). Undo is pessimistic: nothing on screen
 * changes before the 200 (salesUndoRun).
 *
 * TODO(W5e w5-lead-table-compile): fold toastSalesWriteFailure onto
 * leadWriteFailureToast's retry form once both lanes have merged.
 */
import type { QueryClient } from '@tanstack/react-query';
import { ApiError, clientFailureReason } from '../../lib/apiTransport';
import { toast } from '../../lib/toast';
import type { LeadAssignment } from '../../types';
import { toastWriteFailure } from './leadWriteFailureToast';

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

const loadRun = () => import('./salesUndoRun');

/** The run chunk did not load (a stale deploy): no request was sent. */
function runUnavailable(title: string): void {
  toast.error(title, { detail: "This action couldn't start. Reload the page, then try again." });
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
  loadRun().then(
    (module) => module.raiseRetryToast(title, error, error.retryAfterMs, subject, retry),
    () => toastWriteFailure(title, error, subject),
  );
}

function assignedTitle(count: number): string {
  return `${count} ${count === 1 ? 'lead' : 'leads'} assigned`;
}

const UNDO_FAILED = "Couldn't undo the assignment";

/** One lead to one loan officer: the success toast, with Undo. */
export function toastAssigned(ctx: SalesUndoContext, result: SalesAssignResult): void {
  const assignment = result.assignments[0];
  void loadRun().catch(() => undefined);
  toast.success(assignedTitle(result.assigned_count), {
    auditEventId: result.audit_event_id,
    action: assignment
      ? {
        label: 'Undo',
        onAction: () => {
          loadRun().then((module) => module.undoAssignment(ctx, assignment), () => runUnavailable(UNDO_FAILED));
        },
      }
      : null,
  });
}

/** A distribution run: the success toast, with Undo of that run's own rows. */
export function toastDistributed(ctx: SalesUndoContext, result: SalesAssignResult, distributionRequestId: string): void {
  void loadRun().catch(() => undefined);
  toast.success(assignedTitle(result.assigned_count), {
    auditEventId: result.audit_event_id,
    action: result.assigned_count > 0
      ? {
        label: 'Undo',
        onAction: () => {
          loadRun().then(
            (module) => module.undoDistribution(ctx, distributionRequestId, result.assigned_count),
            () => runUnavailable(UNDO_FAILED),
          );
        },
      }
      : null,
  });
}
