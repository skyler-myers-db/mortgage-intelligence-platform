import { describe, expect, it } from 'vitest';
import { AUDIT_FILTER_PARAMS } from '../components/admin/AdminAuditExplorer.params';
import { auditCorrelationHref, auditEventHref, legacyLedgerRedirect } from './auditLinks';

/**
 * The explorer moved from /admin-config to /audit-ledger (D-audit-reads-c3).
 * Only the path changed: the parameters and the `#audit` hash are the same,
 * and an old link that carries any explorer parameter is sent on unchanged.
 */
describe('audit explorer links', () => {
  it('point at the ledger page with the same parameters and hash', () => {
    expect(auditEventHref('evt-0001')).toBe('/audit-ledger?audit_event_id=evt-0001#audit');
    expect(auditCorrelationHref('corr-1')).toBe('/audit-ledger?audit_correlation_id=corr-1#audit');
  });

  it('redirects an old link carrying any explorer parameter, search verbatim', () => {
    expect(legacyLedgerRedirect('?audit_event_id=evt-0001')).toBe('/audit-ledger?audit_event_id=evt-0001#audit');
    expect(legacyLedgerRedirect('audit_actor=a%40b.example')).toBe('/audit-ledger?audit_actor=a%40b.example#audit');
    for (const param of Object.values(AUDIT_FILTER_PARAMS)) {
      expect(legacyLedgerRedirect(`?${param}=x`), param).toBe(`/audit-ledger?${param}=x#audit`);
    }
  });

  it('leaves a plain Admin link (no explorer parameter) on Admin', () => {
    expect(legacyLedgerRedirect('')).toBeNull();
    expect(legacyLedgerRedirect('?tab=rules')).toBeNull();
  });
});
