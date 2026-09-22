import { describe, expect, it } from 'vitest';
import {
  AUDIT_FILTER_PARAMS,
  EMPTY_AUDIT_FILTERS,
  auditDayBoundary,
  auditEventPageQuery,
  auditFilterDraftError,
  auditFiltersKey,
  hasActiveAuditFilters,
  normalizeAuditEntity,
  parseAuditDay,
  parseAuditExplorerFilters,
  serializeAuditExplorerFilters,
  type AuditExplorerFilters,
} from './AdminAuditExplorer.params';

const FULL: AuditExplorerFilters = {
  entity: 'B-ABC123',
  action: 'outreach.approve',
  eventType: 'LEAD_EXPORT',
  actor: 'approver@summit-mortgage.example',
  since: '2026-07-01',
  until: '2026-07-14',
  correlationId: 'corr-fixture-3',
  eventId: 'evt-fixture-0003',
};

describe('audit explorer URL state', () => {
  it('round-trips every filter through the URL', () => {
    const params = serializeAuditExplorerFilters(FULL);
    expect(Object.fromEntries(params)).toEqual({
      audit_entity: 'B-ABC123',
      audit_action: 'outreach.approve',
      audit_event_type: 'LEAD_EXPORT',
      audit_actor: 'approver@summit-mortgage.example',
      audit_since: '2026-07-01',
      audit_until: '2026-07-14',
      audit_correlation_id: 'corr-fixture-3',
      audit_event_id: 'evt-fixture-0003',
    });
    expect(parseAuditExplorerFilters(params)).toEqual(FULL);
  });

  it('keeps the deep-link parameter name the rest of the product links to', () => {
    expect(AUDIT_FILTER_PARAMS.eventId).toBe('audit_event_id');
    expect(parseAuditExplorerFilters(new URLSearchParams('audit_event_id=evt-1')).eventId).toBe('evt-1');
  });

  it('omits empty filters and keeps unrelated page parameters', () => {
    const base = new URLSearchParams('tab=rules&audit_actor=old@x.example&audit_since=2026-01-01');
    const next = serializeAuditExplorerFilters({ ...EMPTY_AUDIT_FILTERS, eventType: 'VIEW_LEADS' }, base);
    expect(next.toString()).toBe('tab=rules&audit_event_type=VIEW_LEADS');
  });

  it('drops malformed values instead of sending them to the API', () => {
    const parsed = parseAuditExplorerFilters(new URLSearchParams({
      audit_entity: 'B-ABC 123',
      audit_action: 'drop table;',
      audit_event_type: 'lead export!',
      audit_actor: 'two people@x.example',
      audit_since: '2026-02-30',
      audit_until: 'yesterday',
      audit_correlation_id: 'someone@example.com',
      audit_event_id: 'evt/../../x',
    }));
    expect(parsed).toEqual(EMPTY_AUDIT_FILTERS);
    expect(hasActiveAuditFilters(parsed)).toBe(false);
  });

  it('normalizes borrower references and upper-cases event codes', () => {
    const parsed = parseAuditExplorerFilters(new URLSearchParams('audit_entity=b-abc123&audit_event_type=lead_export'));
    expect(parsed.entity).toBe('B-ABC123');
    expect(parsed.eventType).toBe('LEAD_EXPORT');
    expect(normalizeAuditEntity('b-abc 1')).toEqual({
      value: '',
      error: 'Borrower reference must use B- followed by letters and numbers only.',
    });
    expect(normalizeAuditEntity('11111111-1111-4111-8111-111111111111').value)
      .toBe('11111111-1111-4111-8111-111111111111');
  });

  it('accepts only real calendar days', () => {
    expect(parseAuditDay('2026-07-14')).toBe('2026-07-14');
    expect(parseAuditDay('2026-13-01')).toBe('');
    expect(parseAuditDay('2026-7-4')).toBe('');
    expect(parseAuditDay(null)).toBe('');
  });

  it('gives the same key to the same filter set regardless of parameter order', () => {
    const a = parseAuditExplorerFilters(new URLSearchParams('audit_actor=a@x.example&audit_event_type=APPROVE'));
    const b = parseAuditExplorerFilters(new URLSearchParams('audit_event_type=APPROVE&audit_actor=a@x.example'));
    expect(auditFiltersKey(a)).toBe(auditFiltersKey(b));
    expect(auditFiltersKey(a)).not.toBe(auditFiltersKey({ ...a, actor: 'b@x.example' }));
  });
});

describe('audit explorer API query', () => {
  it('maps a borrower entity to borrower_id and anything else to entity_id', () => {
    expect(auditEventPageQuery({ ...EMPTY_AUDIT_FILTERS, entity: 'B-ABC123' })).toMatchObject({
      borrower_id: 'B-ABC123',
      entity_id: null,
    });
    expect(auditEventPageQuery({ ...EMPTY_AUDIT_FILTERS, entity: 'approval-42' })).toMatchObject({
      borrower_id: null,
      entity_id: 'approval-42',
    });
  });

  it('sends every applied filter under the API parameter name', () => {
    const query = auditEventPageQuery(FULL);
    expect(query).toMatchObject({
      action: 'outreach.approve',
      event_type: 'LEAD_EXPORT',
      actor: 'approver@summit-mortgage.example',
      correlation_id: 'corr-fixture-3',
      event_id: 'evt-fixture-0003',
    });
    expect(query.since).toBe(auditDayBoundary('2026-07-01', false));
    expect(query.until).toBe(auditDayBoundary('2026-07-14', true));
    expect(auditEventPageQuery(EMPTY_AUDIT_FILTERS)).toEqual({
      entity_id: null,
      borrower_id: null,
      action: null,
      event_type: null,
      actor: null,
      since: null,
      until: null,
      correlation_id: null,
      event_id: null,
    });
  });

  it('treats the day window as whole local days, until inclusive', () => {
    const since = new Date(auditDayBoundary('2026-07-14', false) as string);
    const until = new Date(auditDayBoundary('2026-07-14', true) as string);
    expect([since.getFullYear(), since.getMonth(), since.getDate(), since.getHours()]).toEqual([2026, 6, 14, 0]);
    expect(until.getTime() - since.getTime()).toBe(24 * 60 * 60 * 1000 - 1);
  });

  it('refuses an inverted window and a malformed correlation id before applying', () => {
    expect(auditFilterDraftError({ ...EMPTY_AUDIT_FILTERS, since: '2026-07-14', until: '2026-07-01' }))
      .toMatch(/on or before/);
    expect(auditFilterDraftError({ ...EMPTY_AUDIT_FILTERS, correlationId: 'has space' }))
      .toMatch(/Correlation id/);
    expect(auditFilterDraftError(FULL)).toBeNull();
  });
});
