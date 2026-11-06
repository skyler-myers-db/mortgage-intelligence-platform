import { describe, expect, it } from 'vitest';
import { AUDIT_FREE_GET_ROUTES, isAuditFreeRead, logicalApiPath } from './auditFreeReads';

/**
 * The reviewed audit-free GET list (lib/auditFreeReads). The server side pins
 * each entry (tests/unit/test_audit_free_reads_registry.py); this pins the
 * matcher: the request paths the transport records, and every audited read
 * the app makes staying out.
 */
describe('logicalApiPath', () => {
  it('drops the query string and the version prefix', () => {
    expect(logicalApiPath('/api/v1/analytics/executive?states=CA&days=30')).toBe('/api/analytics/executive');
    expect(logicalApiPath('/api/v1/segments#x')).toBe('/api/segments');
    expect(logicalApiPath('/api/segments')).toBe('/api/segments');
    expect(logicalApiPath('/api/v10/segments')).toBe('/api/v10/segments');
  });
});

describe('isAuditFreeRead', () => {
  it('matches the reviewed reads, with or without the version and query string', () => {
    expect(isAuditFreeRead('/api/v1/analytics/executive?states=CA')).toBe(true);
    expect(isAuditFreeRead('/api/analytics/executive')).toBe(true);
    expect(isAuditFreeRead('/api/v1/borrowers/B-P5YP9ESW32R7Z/evidence')).toBe(true);
    expect(isAuditFreeRead('/api/v1/borrowers/search?q=B-P5Y')).toBe(true);
    expect(isAuditFreeRead('/api/v1/leads/count?segment=ITM')).toBe(true);
    expect(isAuditFreeRead('/api/v1/leads/B-P5YP9ESW32R7Z/assignment')).toBe(true);
  });

  it('never matches an audited read the app makes', () => {
    // visual.ts AUDITED_READS, GET: VIEW_LEADS, VIEW_BORROWER(_PROOF), VIEW_AUDIT_LEDGER, VIEW_REFUSAL_REPORT_TEXT.
    for (const path of [
      '/api/v1/leads?limit=5',
      '/api/v1/leads',
      '/api/v1/borrowers/B-P5YP9ESW32R7Z',
      '/api/v1/borrowers/B-P5YP9ESW32R7Z/proof',
      '/api/v1/audit/events/page?limit=25',
      '/api/v1/audit/events',
      '/api/v1/audit/rollups?period=week',
      '/api/v1/audit/facets',
      '/api/v1/audit/count',
      '/api/v1/audit/my-events',
      '/api/v1/audit/receipt/evt-1',
      '/api/v1/audit/refusal-reports',
      '/api/v1/audit/refusal-reports/r-1/question',
      '/api/v1/admin/operations',
    ]) {
      expect(isAuditFreeRead(path), path).toBe(false);
    }
  });

  it('is fail-closed: an unreviewed path, a longer path or a dot segment does not match', () => {
    expect(isAuditFreeRead('/api/v1/analytics/never-reviewed')).toBe(false);
    expect(isAuditFreeRead('/api/v1/analytics/executive/extra')).toBe(false);
    expect(isAuditFreeRead('/api/v1/segments/')).toBe(false);
    expect(isAuditFreeRead('/api/v1/borrowers/B-1/evidence/x')).toBe(false);
    // A template slot is one real segment: the URL parser would fold these into another route.
    expect(isAuditFreeRead('/api/v1/borrowers/../evidence')).toBe(false);
    expect(isAuditFreeRead('/api/v1/portfolio/%2E%2E')).toBe(false);
    expect(isAuditFreeRead('/api/v1/portfolio/.')).toBe(false);
    expect(isAuditFreeRead('')).toBe(false);
  });

  it('lists each route once, in its /api/ form, with lowercase snake slots', () => {
    expect(new Set(AUDIT_FREE_GET_ROUTES).size).toBe(AUDIT_FREE_GET_ROUTES.length);
    for (const route of AUDIT_FREE_GET_ROUTES) {
      expect(route, route).toMatch(/^\/api\/(?!v1\/)/);
      for (const segment of route.split('/').slice(1)) {
        expect(segment, route).toMatch(/^(?:[a-z0-9-]+|\{[a-z_]+\})$/);
      }
    }
  });
});
