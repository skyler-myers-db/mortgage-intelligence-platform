import type { PrintReceipt } from '../../lib/apiClients/printReceipt';
import { formatTimestamp } from '../../lib/time';
import { useApp } from '../AppContext';

/**
 * The provenance block printed paper carries (audit responsive-08, W5d;
 * deviation:print-export). `hidden` on screen; print.css shows it on paper.
 *
 * It names what was printed (the surface line), who recorded the copy and
 * when, with the PRINT_EXPORT audit receipt id, and the lender by NAME only:
 * never the LenderMark, which stays off every print surface (report 12.4 #9).
 * A print taken without Export PDF (Ctrl/Cmd-P) has no receipt and says so.
 */
export const UNAUDITED_PRINT = 'Unaudited print: use Export PDF for a recorded copy.';

export interface PrintProvenanceProps {
  /** What is on the page: 'Borrower 360 · B-…' or 'Audit ledger page N · R rows'. */
  subject: string;
  receipt: PrintReceipt | null;
}

export function PrintProvenance({ subject, receipt }: PrintProvenanceProps) {
  const { lender } = useApp();
  return (
    <div className="print-provenance" hidden data-testid="print-provenance">
      <p className="print-provenance__subject">
        {subject}
        {lender ? ` · ${lender}` : ''}
      </p>
      <p className="print-provenance__receipt">
        {receipt
          ? `Exported by ${receipt.actor} on ${formatTimestamp(receipt.recorded_at)} · Audit receipt ${receipt.audit_event_id}`
          : UNAUDITED_PRINT}
      </p>
    </div>
  );
}
