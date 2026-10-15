/**
 * The failure toast of a receipt the user waited on (W5d: the PRINT_EXPORT
 * receipt before Export PDF, the AUDIT_EXPORT receipt before the explorer's
 * page CSV). Nothing printed or downloaded; the toast says why.
 *
 * - A 429 offers 'Try again', held behind the Retry-After countdown (the
 *   Toaster's shared RetryClock).
 * - A server failure reads describeApiError's body, loaded on the failure
 *   only through AsyncFailure's existing lazy chunk (as leadWriteFailureToast
 *   does; a static import would add the vocabulary to both routes).
 * - A client-side failure (offline, unreachable, an ended session) already
 *   carries that body as its message; anything else gets a fixed sentence,
 *   never a raw error message.
 *
 * Toast text is shown to the signed-in person only; it is never telemetry.
 */
import { ApiError, clientFailureReason } from '../../lib/apiTransport';
import { toast } from '../../lib/toast';

export const RECEIPT_RATE_LIMITED = 'Too many requests right now. Try again when the countdown ends.';
export const RECEIPT_NOT_RECORDED = 'Nothing was recorded. Try again.';

/** Awaited inside a function so a failed stylesheet preload rejects here (see leadWriteFailureToast). */
async function failureSentence(error: ApiError, subject: string): Promise<string | null> {
  const module: typeof import('../ui/AsyncFailure') | undefined = await import('../ui/AsyncFailure');
  return module ? module.failureSentence(error, subject) : null;
}

export function toastReceiptFailure(title: string, error: unknown, subject: string, retry: () => void): void {
  if (!(error instanceof ApiError)) {
    toast.error(title, { detail: RECEIPT_NOT_RECORDED });
    return;
  }
  if (error.status === 429) {
    toast.error(title, {
      detail: RECEIPT_RATE_LIMITED,
      action: { label: 'Try again', onAction: retry },
      retryAfterMs: error.retryAfterMs,
    });
    return;
  }
  if (clientFailureReason(error) !== null) {
    toast.error(title, { detail: error.message || RECEIPT_NOT_RECORDED });
    return;
  }
  failureSentence(error, subject).then(
    (sentence) => toast.error(title, { detail: sentence || RECEIPT_NOT_RECORDED }),
    () => toast.error(title, { detail: RECEIPT_NOT_RECORDED }),
  );
}
