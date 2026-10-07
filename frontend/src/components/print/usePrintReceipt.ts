import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ApiError } from '../../lib/apiTransport';
import { createPrintReceipt, type PrintReceipt, type PrintReceiptRequest } from '../../lib/apiClients/printReceipt';
import { toast } from '../../lib/toast';
import { toastWriteFailure } from '../mortgage/leadWriteFailureToast';

/**
 * Export PDF behind a server-owned receipt (audit responsive-08, W5d).
 *
 * `run(request)` asks the server for the PRINT_EXPORT ledger row FIRST; the
 * print dialog opens only once that row exists. On success the receipt is
 * committed synchronously (flushSync), so PrintProvenance already names the
 * actor, the time and the audit id when `window.print()` snapshots the page
 * the reader is looking at. Nothing is re-read to print: this hook never
 * refetches, invalidates or prefetches a query, and binds nothing to
 * beforeprint / afterprint.
 *
 * On failure there is NO print: a 429 raises an error toast whose Try again
 * waits out the Retry-After (the Toaster's shared RetryClock), anything else
 * an error toast in describeApiError's words (toastWriteFailure). A second
 * click while a receipt is pending is ignored.
 */
export const PRINT_RECEIPT_RECORDED = 'Print receipt recorded';
export const PRINT_NOT_RECORDED = 'Print not recorded';
const RATE_LIMITED_DETAIL = 'Too many requests right now. Try again when the countdown ends.';

export interface PrintReceiptControl {
  /** True while the receipt request is in flight. */
  pending: boolean;
  /**
   * The receipt written for exactly this request (same surface, borrower,
   * filters and row count), else null: a page that changed since its Export
   * PDF prints as an unaudited copy rather than under another page's receipt.
   */
  receiptFor: (request: PrintReceiptRequest) => PrintReceipt | null;
  run: (request: PrintReceiptRequest) => void;
}

/** One request's identity: its fields in a fixed order, filters sorted. */
function requestKey(request: PrintReceiptRequest): string {
  const filters = Object.entries(request.filters ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([request.surface, request.borrower_id ?? null, filters, request.row_count]);
}

export function usePrintReceipt(): PrintReceiptControl {
  const [pending, setPending] = useState(false);
  const [printed, setPrinted] = useState<{ key: string; receipt: PrintReceipt } | null>(null);
  const inFlight = useRef(false);

  const run = (request: PrintReceiptRequest): void => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    createPrintReceipt(request).then(
      (recorded) => {
        inFlight.current = false;
        flushSync(() => {
          setPrinted({ key: requestKey(request), receipt: recorded });
          setPending(false);
        });
        toast.info(PRINT_RECEIPT_RECORDED, { auditEventId: recorded.audit_event_id });
        window.print();
      },
      (error: unknown) => {
        inFlight.current = false;
        setPending(false);
        if (error instanceof ApiError && error.status === 429) {
          toast.error(PRINT_NOT_RECORDED, {
            detail: RATE_LIMITED_DETAIL,
            action: { label: 'Try again', onAction: () => run(request) },
            retryAfterMs: error.retryAfterMs,
          });
          return;
        }
        toastWriteFailure(PRINT_NOT_RECORDED, error, 'the print receipt');
      },
    );
  };

  const receiptFor = (request: PrintReceiptRequest): PrintReceipt | null => (
    printed !== null && printed.key === requestKey(request) ? printed.receipt : null
  );

  return { pending, receiptFor, run };
}
