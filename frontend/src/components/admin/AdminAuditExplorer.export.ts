/**
 * The audited page CSV of the audit explorer (W5d, tables-10; the
 * useLeadCsvExport pattern): the file and its declaration. Loaded on click
 * only (a dynamic import), so the audit-ledger closure never carries the CSV
 * builder or the hashing.
 *
 * Pure on purpose: this chunk imports no transport. A dynamic entry that
 * reached lib/apiTransport without the rest of its shared chunk split the
 * transport out of the initial closure (+0.7 KiB br of initial JS, measured
 * W5d; leadWriteFailureToast.ts records the same trap). The explorer POSTs
 * the declaration (createAuditExportReceipt) and calls downloadPreparedCsv
 * only once the AUDIT_EXPORT receipt resolves.
 */
import type { AuditEventRow } from '../../lib/apiTypes';
import type { AuditExportReceiptRequest } from '../../lib/apiClients/auditExplorer';
import { sha256Hex } from '../../lib/apiClients/leadExport';
import { downloadCsvText } from '../../lib/csv';
import { auditPageCsvFilename, buildAuditPageCsv } from './AdminAuditExplorer.csv';

export interface PreparedAuditCsv {
  csv: string;
  filename: string;
  /** What the explorer declares to POST /audit/export-receipt. */
  declaration: AuditExportReceiptRequest;
}

/** What the explorer declares about a CSV before it may download it. */
export async function buildAuditExportDeclaration(
  csv: string,
  events: readonly AuditEventRow[],
  filters: Record<string, string>,
): Promise<AuditExportReceiptRequest> {
  const eventIds = events.map((event) => event.event_id);
  const [csvSha256, eventIdsSha256] = await Promise.all([sha256Hex(csv), sha256Hex(JSON.stringify(eventIds))]);
  return {
    row_count: eventIds.length,
    csv_sha256: csvSha256,
    event_ids: eventIds,
    event_ids_sha256: eventIdsSha256,
    filters,
  };
}

/** The page's CSV (the rows on screen) and its declaration; nothing is sent. */
export async function prepareAuditPageCsv(
  events: readonly AuditEventRow[],
  page: number,
  filters: Record<string, string>,
): Promise<PreparedAuditCsv> {
  const csv = buildAuditPageCsv(events);
  return { csv, filename: auditPageCsvFilename(page), declaration: await buildAuditExportDeclaration(csv, events, filters) };
}

/** Hand the file to the browser: call only after the receipt resolved. */
export function downloadPreparedCsv(prepared: PreparedAuditCsv): void {
  downloadCsvText(prepared.csv, prepared.filename);
}
