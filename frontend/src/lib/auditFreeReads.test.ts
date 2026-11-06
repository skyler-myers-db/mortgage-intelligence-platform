import { describe, expect, it } from 'vitest';
import { AUDIT_FREE_GET_ROUTES, AUDIT_FREE_POST_READS, isAuditFreeRead, logicalApiPath } from './auditFreeReads';

/**
 * The reviewed audit-free read list (lib/auditFreeReads), per method. The
 * server side pins each entry (tests/unit/test_audit_free_reads_registry.py);
 * this pins the matcher: the methods and request paths the transport records,
 * and every audited read the app makes staying out.
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
  it('matches the reviewed GET reads, with or without the version and query string', () => {
    expect(isAuditFreeRead('/api/v1/analytics/executive?states=CA', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/analytics/executive', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/v1/analytics/executive', 'get')).toBe(true);
    expect(isAuditFreeRead('/api/v1/borrowers/B-P5YP9ESW32R7Z/evidence', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/v1/borrowers/search?q=B-P5Y', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/v1/leads/count?segment=ITM', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/v1/leads/B-P5YP9ESW32R7Z/assignment', 'GET')).toBe(true);
  });

  it('matches the ledger reads that write no row: the Console activity and the Admin operations panel', () => {
    // list_my_events and get_operations only list the ledger (backend/api/audit.py, admin.py).
    expect(isAuditFreeRead('/api/v1/audit/my-events?limit=8', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/v1/admin/operations', 'GET')).toBe(true);
  });

  it('matches a reviewed POST read only as a POST, and a GET entry only as a GET', () => {
    // Ask Genie's starters (lib/genieStartQuery): audit-exempt, read with a POST.
    expect(isAuditFreeRead('/api/v1/genie/start', 'POST')).toBe(true);
    expect(isAuditFreeRead('/api/v1/genie/start', 'GET')).toBe(false);
    // POST /workspace/saved-views writes SAVE_QUEUE_VIEW in its INSERT; only the GET is listed.
    expect(isAuditFreeRead('/api/v1/workspace/saved-views', 'GET')).toBe(true);
    expect(isAuditFreeRead('/api/v1/workspace/saved-views', 'POST')).toBe(false);
    expect(isAuditFreeRead('/api/v1/campaigns', 'POST')).toBe(false);
    expect(isAuditFreeRead('/api/v1/analytics/executive', 'DELETE')).toBe(false);
    // An error the transport did not build names no method: never audit-free.
    expect(isAuditFreeRead('/api/v1/analytics/executive', null)).toBe(false);
    expect(isAuditFreeRead('/api/v1/analytics/executive', '')).toBe(false);
  });

  it('never matches an audited read the app makes', () => {
    // visual.ts AUDITED_READS: VIEW_LEADS, VIEW_BORROWER(_PROOF), VIEW_AUDIT_LEDGER, VIEW_REFUSAL_REPORT_TEXT,
    // and the approval-surface POSTs (RECOMMEND_OFFER, DRAFT_OUTREACH, PROPERTY_LOOKUP).
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
      '/api/v1/audit/receipt/evt-1',
      '/api/v1/audit/refusal-reports',
      '/api/v1/audit/refusal-reports/r-1/question',
    ]) {
      expect(isAuditFreeRead(path, 'GET'), path).toBe(false);
      expect(isAuditFreeRead(path, 'POST'), path).toBe(false);
    }
    for (const path of ['/api/v1/offers/recommend', '/api/v1/outreach/draft', '/api/v1/lookup/property-loan']) {
      expect(isAuditFreeRead(path, 'POST'), path).toBe(false);
    }
  });

  it('is fail-closed: an unreviewed path, a longer path, a dot segment or an encoded separator does not match', () => {
    expect(isAuditFreeRead('/api/v1/analytics/never-reviewed', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/analytics/executive/extra', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/segments/', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/borrowers/B-1/evidence/x', 'GET')).toBe(false);
    // A template slot is one real segment: the URL parser would fold these into another route.
    expect(isAuditFreeRead('/api/v1/borrowers/../evidence', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/portfolio/%2E%2E', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/portfolio/.', 'GET')).toBe(false);
    // The server decodes an encoded / or \ before it routes: the slot would span two segments.
    expect(isAuditFreeRead('/api/v1/borrowers/B-1%2Fproof/evidence', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/borrowers/B-1%2fx/evidence', 'GET')).toBe(false);
    expect(isAuditFreeRead('/api/v1/borrowers/B-1%5Cx/evidence', 'GET')).toBe(false);
    expect(isAuditFreeRead('', 'GET')).toBe(false);
  });

  it('lists each route once, in its /api/ form, with lowercase snake slots', () => {
    expect(new Set(AUDIT_FREE_GET_ROUTES).size).toBe(AUDIT_FREE_GET_ROUTES.length);
    expect(new Set(AUDIT_FREE_POST_READS).size).toBe(AUDIT_FREE_POST_READS.length);
    for (const route of [...AUDIT_FREE_GET_ROUTES, ...AUDIT_FREE_POST_READS]) {
      expect(route, route).toMatch(/^\/api\/(?!v1\/)/);
      for (const segment of route.split('/').slice(1)) {
        expect(segment, route).toMatch(/^(?:[a-z0-9-]+|\{[a-z_]+\})$/);
      }
    }
  });
});
