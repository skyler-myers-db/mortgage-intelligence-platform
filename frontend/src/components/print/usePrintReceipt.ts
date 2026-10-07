import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { createPrintReceipt, type PrintReceipt, type PrintReceiptRequest } from '../../lib/apiClients/printReceipt';
import { toast } from '../../lib/toast';
import { toastReceiptFailure } from './receiptFailureToast';

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
 * waits out the Retry-After, anything else an error toast in
 * describeApiError's words (receiptFailureToast). A second click while a
 * receipt is pending is ignored.
 */
export const PRINT_RECEIPT_RECORDED = 'Print receipt recorded';
export const PRINT_NOT_RECORDED = 'Print not recorded';

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
      (receipt) => {
        inFlight.current = false;
        flushSync(() => {
          setPrinted({ key: requestKey(request), receipt });
          setPending(false);
        });
        toast.info(PRINT_RECEIPT_RECORDED, { auditEventId: receipt.audit_event_id });
        window.print();
      },
      (error: unknown) => {
        inFlight.current = false;
        setPending(false);
        toastReceiptFailure(PRINT_NOT_RECORDED, error, 'the print receipt', () => run(request));
      },
    );
  };

  return {
    pending,
    receiptFor: (request) => (printed?.key === requestKey(request) ? printed.receipt : null),
    run,
  };
}
