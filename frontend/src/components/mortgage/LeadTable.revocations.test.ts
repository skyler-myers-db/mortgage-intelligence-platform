/**
 * flow-v2: the session's revokes applied to the queue's rows (LeadTable.logic).
 * The shell reads every row through applyRevocations, so eligibility, the
 * approval cell, the CSV export and Triage all see a revoked borrower as
 * pending; its in-session receipt hides unless a newer decision set an
 * override.
 */
import { describe, expect, it } from 'vitest';
import type { LeadSummary } from '../../types';
import { buildLeadCsv } from './LeadTable.csv';
import { applyRevocations, isLeadApprovalEligible, withoutRevokedReceipts } from './LeadTable.logic';

function lead(borrowerId: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 90,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    marketing_eligible: true,
    approval_status: 'approved',
    outreach_status: 'queued',
  } as unknown as LeadSummary;
}

const ROWS = [lead('B-0000000000AP1'), lead('B-0000000000AP2')];

describe('applyRevocations', () => {
  it('returns the SAME array when nothing is revoked, or no revoked id is loaded', () => {
    expect(applyRevocations(ROWS, [])).toBe(ROWS);
    expect(applyRevocations(ROWS, ['B-0000000000ZZ9'])).toBe(ROWS);
  });

  it('reads a revoked row as pending with no outreach, and leaves the others as they are', () => {
    const rows = applyRevocations(ROWS, ['B-0000000000AP1']);
    expect(rows).not.toBe(ROWS);
    expect([rows[0].approval_status, rows[0].outreach_status]).toEqual(['pending', 'none']);
    expect(rows[1]).toBe(ROWS[1]);
    expect(ROWS[0].approval_status).toBe('approved');
    expect(isLeadApprovalEligible(rows[0].approval_status, undefined, rows[0])).toBe(true);
  });

  it('exports a revoked row as pending in the CSV', () => {
    const csv = buildLeadCsv(applyRevocations(ROWS, ['B-0000000000AP1']), {});
    const line = csv.split('\n').find((row) => row.startsWith('B-0000000000AP1'));
    expect(line).toContain(',pending,none,');
    expect(csv.split('\n').find((row) => row.startsWith('B-0000000000AP2'))).toContain(',approved,queued,');
  });
});

describe('withoutRevokedReceipts', () => {
  const receipts = { 'B-0000000000AP1': { auditEventId: 'a1' }, 'B-0000000000AP2': { auditEventId: 'a2' } };

  it('is the same record when nothing is revoked', () => {
    expect(withoutRevokedReceipts(receipts, [], {})).toBe(receipts);
  });

  it("hides a revoked borrower's receipt, unless a newer local decision set an override", () => {
    expect(Object.keys(withoutRevokedReceipts(receipts, ['B-0000000000AP1'], {}))).toEqual(['B-0000000000AP2']);
    expect(withoutRevokedReceipts(receipts, ['B-0000000000AP1'], { 'B-0000000000AP1': 'approved' })).toBe(receipts);
  });
});
