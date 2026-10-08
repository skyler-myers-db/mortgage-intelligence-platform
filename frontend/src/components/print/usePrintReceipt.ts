import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { createPrintReceipt, type PrintReceipt, type PrintReceiptRequest } from '../../lib/apiClients/printReceipt';
import { toast } from '../../lib/toast';
import { toastReceiptFailure } from './receiptFailureToast';

/**
 * Export PDF behind a server-owned receipt (audit responsive-08, W5d).
 *
 * The caller passes the request that describes the page ON SCREEN (null
 * before it has rows), plus an optional client-only `pageKey` for what the
 * request cannot carry (the explorer's page and the event ids on it). The
 * key is never sent; it only decides whether a receipt belongs to the page.
 *
 * `run()` asks the server for the PRINT_EXPORT ledger row FIRST; the print
 * dialog opens only once that row exists AND the same page is still mounted
 * and showing. On success the receipt is committed synchronously
 * (flushSync), so PrintProvenance already names the actor, the time and the
 * audit id when `window.print()` snapshots the page. If the page changed (a
 * pager step, another route, an unmount) while the receipt was in flight,
 * nothing prints: the row exists, so an info toast says so and links it, and
 * asks for Export PDF again on the page now shown. Nothing is re-read to
 * print: this hook never refetches, invalidates or prefetches a query, and
 * binds nothing to beforeprint / afterprint.
 *
 * On failure there is NO print: a 429 raises an error toast whose Try again
 * waits out the Retry-After, anything else an error toast in
 * describeApiError's words (receiptFailureToast). Try again asks for the
 * receipt of the page it failed on, and only while that page is still the
 * one shown. A second click while a receipt is pending is ignored.
 */
export const PRINT_RECEIPT_RECORDED = 'Print receipt recorded';
export const PRINT_NOT_RECORDED = 'Print not recorded';
export const PRINT_PAGE_CHANGED = 'Page changed before printing';
export const PRINT_PAGE_CHANGED_RECORDED =
  'The print receipt was recorded, but the page changed before the print dialog opened, so nothing printed. Use Export PDF again.';
export const PRINT_PAGE_CHANGED_UNRECORDED =
  'The page changed, so nothing was recorded or printed. Use Export PDF again.';

export interface PrintReceiptControl {
  /** True while the receipt request is in flight. */
  pending: boolean;
  /**
   * The receipt written for exactly the page on screen (same surface,
   * borrower, filters, row count and page key), else null: a page that
   * changed since its Export PDF prints as an unaudited copy rather than
   * under another page's receipt.
   */
  receipt: PrintReceipt | null;
  /** Export PDF for the page on screen; a no-op while nothing is shown. */
  run: () => void;
}

/** One page's identity: the request's fields in a fixed order, filters sorted, then the page key. */
function requestKey(request: PrintReceiptRequest, pageKey: string): string {
  const filters = Object.entries(request.filters ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([request.surface, request.borrower_id ?? null, filters, request.row_count, pageKey]);
}

export function usePrintReceipt(shown: PrintReceiptRequest | null, pageKey = ''): PrintReceiptControl {
  const shownKey = shown ? requestKey(shown, pageKey) : null;
  const [pending, setPending] = useState(false);
  const [printed, setPrinted] = useState<{ key: string; receipt: PrintReceipt } | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  // The committed page's key: what is on screen when a receipt resolves.
  const committedKey = useRef<string | null>(null);
  useLayoutEffect(() => {
    committedKey.current = shownKey;
  }, [shownKey]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const stillShowing = (key: string): boolean => mounted.current && committedKey.current === key;

  const request = (body: PrintReceiptRequest, key: string): void => {
    if (inFlight.current) return;
    if (!stillShowing(key)) {
      toast.info(PRINT_PAGE_CHANGED, { detail: PRINT_PAGE_CHANGED_UNRECORDED });
      return;
    }
    inFlight.current = true;
    setPending(true);
    createPrintReceipt(body).then(
      (receipt) => {
        inFlight.current = false;
        if (!stillShowing(key)) {
          setPending(false);
          toast.info(PRINT_PAGE_CHANGED, { detail: PRINT_PAGE_CHANGED_RECORDED, auditEventId: receipt.audit_event_id });
          return;
        }
        flushSync(() => {
          setPrinted({ key, receipt });
          setPending(false);
        });
        toast.info(PRINT_RECEIPT_RECORDED, { auditEventId: receipt.audit_event_id });
        window.print();
      },
      (error: unknown) => {
        inFlight.current = false;
        setPending(false);
        toastReceiptFailure(PRINT_NOT_RECORDED, error, 'the print receipt', () => request(body, key));
      },
    );
  };

  return {
    pending,
    receipt: printed !== null && printed.key === shownKey ? printed.receipt : null,
    run: () => {
      if (shown !== null && shownKey !== null) request(shown, shownKey);
    },
  };
}
