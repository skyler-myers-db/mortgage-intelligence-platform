/**
 * The audited page CSV of the audit explorer (W5d, tables-10; the
 * useLeadCsvExport pattern). Loaded on click only (a dynamic import), so the
 * audit-ledger closure never carries the hashing and receipt code.
 *
 * Order: build the CSV of the rows on screen, hash it and the event-id list
 * in the browser, POST /audit/export-receipt and WAIT; only a recorded
 * AUDIT_EXPORT row releases the download. A 429 offers 'Try again' behind
 * the Retry-After; any other failure is described and downloads nothing.
 * Administrators only: the control is not rendered for a read-only auditor
 * (the export receipt route stays AdminDep).
 */
import type { AuditEventRow } from '../../lib/apiTypes';
import { buildAuditExportDeclaration, createAuditExportReceipt } from '../../lib/apiClients/auditExplorer';
import { ApiError } from '../../lib/apiTransport';
import { downloadCsvText } from '../../lib/csv';
import { toast } from '../../lib/toast';
import { toastWriteFailure } from '../mortgage/leadWriteFailureToast';
import { auditPageCsvFilename, buildAuditPageCsv } from './AdminAuditExplorer.csv';

export const AUDIT_CSV_EXPORTED = 'Audit page exported';
export const AUDIT_CSV_NOT_EXPORTED = 'Audit page not exported';

export interface AuditPageCsvExport {
  events: readonly AuditEventRow[];
  /** 0-based page index (the filename is 1-based). */
  page: number;
  /** The explorer query the rows were read with (fingerprinted server-side). */
  filters: Record<string, string>;
}

/** Resolves once the flow ends: downloaded, or refused with a toast and no file. */
export async function exportAuditPageCsv(request: AuditPageCsvExport): Promise<void> {
  const csv = buildAuditPageCsv(request.events);
  try {
    const declaration = await buildAuditExportDeclaration(csv, request.events, request.filters);
    const receipt = await createAuditExportReceipt(declaration);
    downloadCsvText(csv, auditPageCsvFilename(request.page));
    toast.success(AUDIT_CSV_EXPORTED, { auditEventId: receipt.audit_event_id });
  } catch (error: unknown) {
    if (error instanceof ApiError && error.status === 429) {
      toast.error(AUDIT_CSV_NOT_EXPORTED, {
        detail: 'Too many requests right now. Try again when the countdown ends.',
        action: { label: 'Try again', onAction: () => void exportAuditPageCsv(request) },
        retryAfterMs: error.retryAfterMs,
      });
      return;
    }
    toastWriteFailure(AUDIT_CSV_NOT_EXPORTED, error, 'the audit export receipt');
  }
}
