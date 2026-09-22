/**
 * CSV of the audit explorer's current page (audit flow-04 phase 1 /
 * tables-10). The rows are the ones on screen, fetched by an admin through
 * the admin-gated page read; no second query is made and nothing is widened.
 * Cells go through the shared formula-injection gate (lib/csv.ts).
 */
import type { AuditEventRow } from '../../lib/apiTypes';
import { csvEscape, downloadCsvText } from '../../lib/csv';
import { auditEventCode, auditEventLabel } from './AdminAuditExplorer.labels';

export const AUDIT_PAGE_CSV_HEADER = [
  'event_id',
  'created_at',
  'event',
  'event_code',
  'action',
  'actor',
  'entity_type',
  'entity_id',
  'correlation_id',
  'request_id',
  'evidence_ids',
] as const;

function cell(value: string | null | undefined): string {
  return csvEscape(value ?? '');
}

/** The page's rows as CSV text: a header row, then one line per event. */
export function buildAuditPageCsv(events: readonly AuditEventRow[]): string {
  const lines = [AUDIT_PAGE_CSV_HEADER.join(',')];
  for (const event of events) {
    lines.push([
      event.event_id,
      event.created_at,
      auditEventLabel(event),
      auditEventCode(event),
      event.action,
      event.actor,
      event.entity_type,
      event.entity_id,
      event.correlation_id,
      event.request_id,
      (event.evidence_ids ?? []).join(' '),
    ].map(cell).join(','));
  }
  return `${lines.join('\n')}\n`;
}

/** `mip-audit-page-2-2026-07-14.csv` (1-based page, local capture day). */
export function auditPageCsvFilename(page: number, now: Date = new Date()): string {
  const day = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('-');
  return `mip-audit-page-${page + 1}-${day}.csv`;
}

export function downloadAuditPageCsv(events: readonly AuditEventRow[], page: number): void {
  downloadCsvText(buildAuditPageCsv(events), auditPageCsvFilename(page));
}
