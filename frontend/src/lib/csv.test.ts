import { describe, expect, it } from 'vitest';
import { buildAuditPageCsv } from '../components/admin/AdminAuditExplorer.csv';
import { buildLeadCsv } from '../components/mortgage/LeadTable.csv';
import type { AuditEventRow } from './apiTypes';
import type { LeadSummary } from '../types';
import { csvEscape } from './csv';

/**
 * The shared formula-injection gate (audit 2026-09-21 `genie-06`, review
 * carryover). A spreadsheet can drop a leading TAB or CR and then read the
 * `=` behind it as a formula, and a bare CR is a line break to most readers,
 * so both joined the gate. The gate is shared by the Lead Queue, audit
 * explorer and Genie answer exports; the last two tests read the first two
 * builders as they ship.
 */
describe('csvEscape', () => {
  it.each([
    ['=SUM(A1)', "'=SUM(A1)"],
    ['+1', "'+1"],
    ['-1', "'-1"],
    ['@cmd', "'@cmd"],
    ['\t=1', "'\t=1"],
  ])('prefixes a leading formula trigger: %j', (raw, escaped) => {
    expect(csvEscape(raw)).toBe(escaped);
  });

  it('prefixes a leading carriage return and quotes the cell it breaks', () => {
    expect(csvEscape('\r=1')).toBe('"\'\r=1"');
  });

  it.each([
    ['a,b', '"a,b"'],
    ['say "hi"', '"say ""hi"""'],
    ['line\nbreak', '"line\nbreak"'],
    ['line\rbreak', '"line\rbreak"'],
    ['line\r\nbreak', '"line\r\nbreak"'],
  ])('quotes a separator or a line ending: %j', (raw, escaped) => {
    expect(csvEscape(raw)).toBe(escaped);
  });

  it.each(['plain', 'B-0123456789ABC', '12.5', 'a=b', 'mid\ttab', ''])('passes a safe cell through unchanged: %j', (raw) => {
    expect(csvEscape(raw)).toBe(raw);
  });
});

const LEAD: LeadSummary = {
  borrower_id: 'B-TEST1',
  display_name: 'Owner masked',
  city: 'Chicago',
  state: 'IL',
  zip: '60617',
  clip: 'clip_ref_public',
  segment_codes: ['itm'],
  equity_estimate: 350000,
  rate_spread_bps: 325,
  opportunity_score: 82,
  confidence: 77,
  recommended_offer: 'Refinance + HELOC',
  why_now: 'Market rate comparison',
  evidence_ids: ['EV1'],
  approval_status: 'pending',
  is_owner_occupied: true,
  is_investor: false,
  is_current_customer: false,
  is_former_customer: true,
  is_competitor_lien: true,
  current_lender_ref: 'Competitor A',
  current_lien_balance: 120000,
  second_pos_amount: 0,
  has_permit: false,
  listed_for_sale: false,
  related_property_count: 1,
  marketing_eligible: true,
  consent_status: 'opt_in',
  suppression_reason: null,
  last_touch_at: null,
  eligible_recontact_at: null,
};

const AUDIT_ROW: AuditEventRow = {
  event_id: 'evt-1',
  actor: 'approver@summit-mortgage.example',
  action: 'lead_queue.export',
  entity_type: 'lead_queue',
  entity_id: 'export-1',
  payload_json: {},
  evidence_ids: [],
  created_at: '2026-07-14T11:10:00Z',
  event_type: 'LEAD_EXPORT',
  request_id: 'req-1',
  correlation_id: 'corr-1',
};

describe('the exports that share the gate', () => {
  it('a Lead Queue row neutralises a tab- or CR-led value and keeps a CR inside one cell', () => {
    const csv = buildLeadCsv(
      [{ ...LEAD, current_lender_ref: '\t=HYPERLINK("x")', city: 'North\rSide' }],
      {},
      { generatedAt: '2026-05-10T12:00:00.000Z' },
    );
    expect(csv).toContain(',"\'\t=HYPERLINK(""x"")",');
    expect(csv).toContain(',"North\rSide",');
  });

  it('an audit explorer row neutralises a tab- or CR-led value and keeps a CR inside one cell', () => {
    const csv = buildAuditPageCsv([{ ...AUDIT_ROW, entity_id: '\r=cmd', actor: 'a\rb' }]);
    const line = csv.split('\n')[1];
    expect(line).toContain(',"a\rb",');
    expect(line).toContain(',"\'\r=cmd",');
  });
});
