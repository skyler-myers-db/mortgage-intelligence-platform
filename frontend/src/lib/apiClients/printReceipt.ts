/**
 * Audited print: the receipt Export PDF waits for (audit responsive-08, W5d).
 *
 * `POST /api/print-receipt` writes one server-owned PRINT_EXPORT ledger row
 * and answers it; only then does the page open the print dialog on the DOM it
 * already rendered. The request names the surface and what the page holds,
 * never its contents. Borrower 360 prints one borrower; the audit explorer
 * prints one page of the ledger, fingerprinted (keyed) from its filters.
 */
import { postJson } from '../apiTransport';

export type PrintSurface = 'borrower_360' | 'audit_ledger';

export interface PrintReceiptRequest {
  surface: PrintSurface;
  /** Borrower 360 only: the masked borrower id of the printed dossier. */
  borrower_id?: string | null;
  /** Audit explorer only: the query parameters the printed page was read with. */
  filters?: Record<string, string>;
  /** Rows on the printed page (exactly 1 for a dossier). */
  row_count: number;
}

export interface PrintReceipt {
  audit_event_id: string;
  event_type: 'PRINT_EXPORT';
  actor: string;
  surface: PrintSurface;
  row_count: number;
  borrower_id: string | null;
  filter_fingerprint: string | null;
  recorded_at: string;
}

/** Write the PRINT_EXPORT row; the print dialog opens only after this resolves. */
export function createPrintReceipt(body: PrintReceiptRequest): Promise<PrintReceipt> {
  return postJson<PrintReceipt, PrintReceiptRequest>('/api/print-receipt', body);
}
