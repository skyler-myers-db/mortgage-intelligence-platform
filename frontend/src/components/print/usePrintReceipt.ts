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
 * reads nothing on beforeprint / afterprint.
 *
 * One receipt, one paper (fix round 2): the receipt captions only the print
 * it was recorded for. A once-only `afterprint` listener, registered BEFORE
 * `window.print()` (Chromium fires afterprint inside the blocking call, so a
 * listener added after it returns would miss it), drops the caption when
 * that dialog closes. A later Ctrl/Cmd-P of the same page (or of this page
 * again after a pager round trip) wrote no PRINT_EXPORT row, so it prints as
 * unaudited. The listener only clears local state; it reads no data.
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
   * borrower, filters, row count and page key) until its print dialog
   * closes, else null: a page that changed since its Export PDF, or any
   * later print of it, prints as an unaudited copy rather than under a
   * receipt it never had.
   */
  receipt: PrintReceipt | null;
  /** Export PDF for the page on screen; a no-op while nothing is shown. */
  run: () => void;
}

/** The receipt committed for the print it was recorded for, with that page's key. */
interface Printed {
  key: string;
  receipt: PrintReceipt;
}

/** One page's identity: the request's fields in a fixed order, filters sorted, then the page key. */
function requestKey(request: PrintReceiptRequest, pageKey: string): string {
  const filters = Object.entries(request.filters ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([request.surface, request.borrower_id ?? null, filters, request.row_count, pageKey]);
}

export function usePrintReceipt(shown: PrintReceiptRequest | null, pageKey = ''): PrintReceiptControl {
  const shownKey = shown ? requestKey(shown, pageKey) : null;
  const [pending, setPending] = useState(false);
  const [printed, setPrinted] = useState<Printed | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  // Removes the pending once-only afterprint clear (a new print, an unmount).
  const disarmAfterPrint = useRef<(() => void) | null>(null);
  // The committed page's key: what is on screen when a receipt resolves.
  const committedKey = useRef<string | null>(null);
  useLayoutEffect(() => {
    committedKey.current = shownKey;
  }, [shownKey]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      disarmAfterPrint.current?.();
    };
  }, []);

  const stillShowing = (key: string): boolean => mounted.current && committedKey.current === key;

  /** Print `entry`'s page once: the dialog's own afterprint drops its caption. */
  const printOnce = (entry: Printed): void => {
    disarmAfterPrint.current?.();
    const clear = (): void => {
      disarmAfterPrint.current?.();
      setPrinted((current) => (current === entry ? null : current));
    };
    window.addEventListener('afterprint', clear, { once: true });
    disarmAfterPrint.current = () => {
      window.removeEventListener('afterprint', clear);
      disarmAfterPrint.current = null;
    };
    try {
      window.print();
    } catch {
      // No dialog opened: nothing may print later under this receipt either.
      clear();
    }
  };

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
        const entry: Printed = { key, receipt };
        flushSync(() => {
          setPrinted(entry);
          setPending(false);
        });
        toast.info(PRINT_RECEIPT_RECORDED, { auditEventId: receipt.audit_event_id });
        printOnce(entry);
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
