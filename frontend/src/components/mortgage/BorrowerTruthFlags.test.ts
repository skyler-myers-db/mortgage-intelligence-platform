import { describe, expect, it } from 'vitest';
import type { LeadSummary } from '../../types';
import { truthFlagLabels } from './BorrowerTruthFlags';

const baseBorrower: LeadSummary = {
  borrower_id: 'B-102FL7THC6Q3L',
  display_name: 'Owner 1234abcd',
  city: 'Chicago',
  state: 'IL',
  zip: '60614',
  clip: 'clip_ref_0123abcd4567',
  segment_codes: ['itm'],
  equity_estimate: 100000,
  rate_spread_bps: 100,
  opportunity_score: 80,
  confidence: 80,
  recommended_offer: 'Refinance',
  why_now: 'Rate spread is positive.',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  is_owner_occupied: true,
  is_investor: false,
  is_current_customer: false,
  is_former_customer: false,
  is_competitor_lien: false,
  related_property_count: 1,
  current_lien_balance: 100000,
  second_pos_amount: 0,
  has_permit: false,
  listed_for_sale: false,
  current_lender_ref: 'Competitor B',
};

describe('truthFlagLabels', () => {
  it('keeps former-customer and competitor-lien signals independent', () => {
    const labels = truthFlagLabels({
      ...baseBorrower,
      is_former_customer: true,
      is_competitor_lien: true,
    }).map((flag) => flag.label);

    expect(labels).toContain('Former customer');
    expect(labels).toContain('Competitor lien');
  });

  it('does not claim pending listing and permit feeds are confirmed negatives', () => {
    const labels = truthFlagLabels(baseBorrower).map((flag) => flag.label);

    expect(labels).toContain('Listing feed pending');
    expect(labels).toContain('Permit feed pending');
    expect(labels).not.toContain('No listing trigger');
    expect(labels).not.toContain('No permit trigger');
  });

  it('surfaces borrower-dossier absentee and corporate-owner signals when present', () => {
    const labels = truthFlagLabels({
      ...baseBorrower,
      is_absentee: true,
      is_corporate_owner: true,
    } as LeadSummary & { is_absentee: boolean; is_corporate_owner: boolean }).map((flag) => flag.label);

    expect(labels).toContain('Absentee owner');
    expect(labels).toContain('Corporate owner');
  });
});
