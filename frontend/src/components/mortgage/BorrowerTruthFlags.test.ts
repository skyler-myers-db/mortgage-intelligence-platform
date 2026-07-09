import { describe, expect, it } from 'vitest';
import type { LeadSummary } from '../../types';
import { ownerCaveatLabels, truthFlagLabels } from './BorrowerTruthFlags';

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

  it('shows confirmed negatives for live listing and HELOC-intent signals', () => {
    const labels = truthFlagLabels(baseBorrower).map((flag) => flag.label);

    expect(labels).toContain('No listing trigger');
    expect(labels).toContain('No HELOC intent');
    expect(labels).not.toContain('Listing feed pending');
    expect(labels).not.toContain('Permit feed pending');
  });

  it('surfaces HELOC propensity separately from filed permits', () => {
    const labels = truthFlagLabels({
      ...baseBorrower,
      has_permit: false,
      has_heloc_propensity_trigger: true,
      heloc_propensity_score: 812,
    }).map((flag) => flag.label);

    expect(labels).toContain('HELOC intent');
    expect(labels).not.toContain('Permit activity');
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

describe('ownerCaveatLabels', () => {
  it('renders no caveats for a default single-owner resolved lead', () => {
    expect(ownerCaveatLabels(baseBorrower)).toEqual([]);
    // A borrower with owner_count=1 and no entity/unresolved signal is also clean.
    expect(
      ownerCaveatLabels({ ...baseBorrower, owner_count: 1, primary_owner_entity_type: 'individual' }),
    ).toEqual([]);
  });

  it('renders a neutral multi-owner caveat when owner_count > 1', () => {
    const caveats = ownerCaveatLabels({ ...baseBorrower, owner_count: 3 });
    const multi = caveats.find((c) => c.label === 'Multi-owner (3)');
    expect(multi).toBeDefined();
    expect(multi?.variant).toBe('neutral');
  });

  it('renders neutral trust-held and llc/entity-held caveats by entity type', () => {
    expect(
      ownerCaveatLabels({ ...baseBorrower, primary_owner_entity_type: 'trust' }).map((c) => c.label),
    ).toContain('Trust-held');
    expect(
      ownerCaveatLabels({ ...baseBorrower, primary_owner_entity_type: 'llc' }).map((c) => c.label),
    ).toContain('LLC/entity-held');
  });

  it('renders a warning suppression caveat when has_unresolved_owner is true', () => {
    const caveats = ownerCaveatLabels({
      ...baseBorrower,
      has_unresolved_owner: true,
      primary_owner_entity_type: 'unresolved',
      marketing_eligible: false,
      suppression_reason: 'unresolved_owner',
    });
    const suppressed = caveats.find((c) => c.label === 'Owner unresolved — outreach suppressed');
    expect(suppressed).toBeDefined();
    expect(suppressed?.variant).toBe('warning');
  });
});
