/**
 * @vitest-environment happy-dom
 *
 * The audited page CSV (W5d, tables-10). The defect it closes: 'Page CSV'
 * downloaded the ledger page with no AUDIT_EXPORT row. Now the receipt is
 * written first and only a recorded row releases the file; a refused or
 * rate-limited receipt downloads nothing.
 *
 * Mutation check (lane report): moving `downloadCsvText` above the
 * `createAuditExportReceipt` await fails "downloads only after the receipt
 * is recorded" and "a refused receipt downloads nothing".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditEventRow } from '../../lib/apiTypes';
import { ApiError } from '../../lib/apiTransport';

const mocks = vi.hoisted(() => ({
  order: [] as string[],
  receipt: vi.fn(),
  download: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastWriteFailure: vi.fn(),
}));

vi.mock('../../lib/apiClients/auditExplorer', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/apiClients/auditExplorer')>()),
  createAuditExportReceipt: mocks.receipt,
}));
vi.mock('../../lib/csv', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/csv')>()),
  downloadCsvText: mocks.download,
}));
vi.mock('../../lib/toast', () => ({ toast: { success: mocks.toastSuccess, error: mocks.toastError, info: vi.fn() } }));
vi.mock('../mortgage/leadWriteFailureToast', () => ({ toastWriteFailure: mocks.toastWriteFailure }));

import { AUDIT_CSV_EXPORTED, AUDIT_CSV_NOT_EXPORTED, exportAuditPageCsv } from './AdminAuditExplorer.export';

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

beforeEach(() => {
  mocks.order.length = 0;
  Object.values(mocks).forEach((mock) => (typeof mock === 'function' ? mock.mockReset() : undefined));
  mocks.download.mockImplementation(() => mocks.order.push('download'));
});

afterEach(() => vi.restoreAllMocks());

describe('exportAuditPageCsv', () => {
  it('downloads only after the receipt is recorded, and declares what the file holds', async () => {
    mocks.receipt.mockImplementation(async () => {
      mocks.order.push('receipt');
      return { audit_event_id: 'a1b2c3d4-0000-4000-8000-000000000001' };
    });

    await exportAuditPageCsv({ events: EVENTS, page: 1, filters: FILTERS });

    expect(mocks.order).toEqual(['receipt', 'download']);
    const [declaration] = mocks.receipt.mock.calls[0] as [{ row_count: number; event_ids: string[]; event_ids_sha256: string; csv_sha256: string; filters: Record<string, string> }];
    expect(declaration.row_count).toBe(2);
    expect(declaration.event_ids).toEqual(EVENTS.map((event) => event.event_id));
    expect(declaration.event_ids_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(declaration.csv_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(declaration.filters).toEqual(FILTERS);
    const [csv, filename] = mocks.download.mock.calls[0] as [string, string];
    expect(csv.split('\n')[0]).toMatch(/^event_id,created_at,/);
    expect(filename).toMatch(/^mip-audit-page-2-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(mocks.toastSuccess).toHaveBeenCalledWith(AUDIT_CSV_EXPORTED, { auditEventId: 'a1b2c3d4-0000-4000-8000-000000000001' });
  });

  it('a refused receipt downloads nothing and says why', async () => {
    const refused = new ApiError('unavailable', { path: '/api/audit/export-receipt', status: 503, dependency: 'lakebase' });
    mocks.receipt.mockRejectedValue(refused);

    await exportAuditPageCsv({ events: EVENTS, page: 0, filters: {} });

    expect(mocks.download).not.toHaveBeenCalled();
    expect(mocks.toastWriteFailure).toHaveBeenCalledWith(AUDIT_CSV_NOT_EXPORTED, refused, 'the audit export receipt');
  });

  it('a 429 downloads nothing and offers Try again behind the Retry-After', async () => {
    mocks.receipt.mockRejectedValueOnce(new ApiError('rate limited', { path: '/api/audit/export-receipt', status: 429, retryAfterMs: 3000 }));

    await exportAuditPageCsv({ events: EVENTS, page: 0, filters: {} });

    expect(mocks.download).not.toHaveBeenCalled();
    const [title, options] = mocks.toastError.mock.calls[0] as [string, { retryAfterMs: number; action: { label: string } }];
    expect(title).toBe(AUDIT_CSV_NOT_EXPORTED);
    expect(options.retryAfterMs).toBe(3000);
    expect(options.action.label).toBe('Try again');
  });
});
