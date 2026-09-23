import { describe, expect, it } from 'vitest';
import type { AuditEventRow } from '../../lib/apiTypes';
import { AUDIT_PAGE_CSV_HEADER, auditPageCsvFilename, buildAuditPageCsv } from './AdminAuditExplorer.csv';

const ROW: AuditEventRow = {
  event_id: 'evt-1',
  actor: 'approver@summit-mortgage.example',
  action: 'lead_queue.export',
  entity_type: 'lead_queue',
  entity_id: 'export-1',
  payload_json: {},
  evidence_ids: ['ev-1', 'ev-2'],
  created_at: '2026-07-14T11:10:00Z',
  event_type: 'LEAD_EXPORT',
  request_id: 'req-1',
  correlation_id: 'corr-1',
};

describe('audit explorer page CSV', () => {
  it('writes the header row, then one line per event on the page', () => {
    const lines = buildAuditPageCsv([ROW]).trimEnd().split('\n');
    expect(lines[0]).toBe(AUDIT_PAGE_CSV_HEADER.join(','));
    expect(lines[0]).toBe(
      'event_id,created_at,event,event_code,action,actor,entity_type,entity_id,correlation_id,request_id,evidence_ids',
    );
    expect(lines[1]).toBe(
      'evt-1,2026-07-14T11:10:00Z,Lead list exported,LEAD_EXPORT,lead_queue.export,'
      + 'approver@summit-mortgage.example,lead_queue,export-1,corr-1,req-1,ev-1 ev-2',
    );
    expect(lines).toHaveLength(2);
  });

  it('neutralises formula cells and quotes separators through the shared gate', () => {
    const csv = buildAuditPageCsv([{ ...ROW, entity_id: '=HYPERLINK("x")', actor: 'a,b', request_id: null }]);
    const line = csv.trimEnd().split('\n')[1];
    expect(line).toContain('"\'=HYPERLINK(""x"")"');
    expect(line).toContain('"a,b"');
    expect(line).toContain(',corr-1,,ev-1 ev-2');
  });

  it('names the file after the page and the local day', () => {
    expect(auditPageCsvFilename(1, new Date(2026, 6, 4, 23, 30))).toBe('mip-audit-page-2-2026-07-04.csv');
  });
});
