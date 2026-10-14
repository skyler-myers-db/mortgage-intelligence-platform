/**
 * @vitest-environment happy-dom
 *
 * The audited page CSV's click-time chunk (W5d, tables-10): it builds the
 * file of the rows on screen and the declaration the AUDIT_EXPORT receipt
 * verifies (the CSV hash and the event-id hash), and sends nothing. The
 * order (receipt first, then the download) is pinned on the rendered
 * explorer in AdminAuditExplorer.test.tsx.
 */
import { describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the module source under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import type { AuditEventRow } from '../../lib/apiTypes';

const download = vi.hoisted(() => vi.fn());
vi.mock('../../lib/csv', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/csv')>()),
  downloadCsvText: download,
}));

import { downloadPreparedCsv, prepareAuditPageCsv } from './AdminAuditExplorer.export';

const EVENTS: AuditEventRow[] = [1, 2].map((index) => ({
  event_id: `0f8fad5b-d9cb-469f-a165-70867728950${index}`,
  actor: 'approver@summit.example',
  action: 'lead_queue.export',
  entity_type: 'lead_queue',
  entity_id: `export-${index}`,
  payload_json: {},
  evidence_ids: [],
  created_at: '2026-07-14T11:10:00Z',
  event_type: 'LEAD_EXPORT',
  request_id: null,
  correlation_id: null,
}));
const FILTERS = { event_type: 'LEAD_EXPORT', exclude_own_ledger_reads: 'true' };
declare const process: { cwd(): string };

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

describe('prepareAuditPageCsv', () => {
  it('declares exactly the file it built: rows, ids in file order, both hashes and the filters', async () => {
    const prepared = await prepareAuditPageCsv(EVENTS, 1, FILTERS);

    expect(prepared.csv.split('\n')[0]).toMatch(/^event_id,created_at,/);
    expect(prepared.filename).toMatch(/^mip-audit-page-2-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(prepared.declaration).toEqual({
      row_count: 2,
      csv_sha256: await sha256(prepared.csv),
      event_ids: EVENTS.map((event) => event.event_id),
      event_ids_sha256: await sha256(JSON.stringify(EVENTS.map((event) => event.event_id))),
      filters: FILTERS,
    });
    expect(download).not.toHaveBeenCalled();
  });

  it('hands the prepared file to the browser only when asked', async () => {
    const prepared = await prepareAuditPageCsv(EVENTS, 0, {});
    downloadPreparedCsv(prepared);
    expect(download).toHaveBeenCalledWith(prepared.csv, prepared.filename);
  });

  it('imports no transport: a click-time chunk reaching lib/apiTransport splits the initial closure', () => {
    const source = readFileSync(join(process.cwd(), 'src', 'components', 'admin', 'AdminAuditExplorer.export.ts'), 'utf8') as string;
    const runtimeImports = [...source.matchAll(/^import (?!type )[^;]*from '([^']+)'/gm)].map((match) => match[1]);
    expect(runtimeImports).toEqual(['../../lib/apiClients/leadExport', '../../lib/csv', './AdminAuditExplorer.csv']);
  });
});
