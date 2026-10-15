/**
 * "More like this" patterns (wow-power-2, W5e item 4a): reviewed attributes
 * only. Pinned over a fixture sweep: the output keys are a closed set, and
 * sub-state geography, listing, propensity and other proxy fields never
 * change the output.
 */
import { describe, expect, it } from 'vitest';
import type { LeadSummary } from '../types';
import { LEAD_PATTERN_KEYS, leadPattern } from './leadPattern';

function lead(overrides: Partial<LeadSummary> = {}): LeadSummary {
  return {
    borrower_id: 'B-PATTERN000001',
    display_name: 'Owner',
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    clip: 'clip_1',
    segment_codes: ['itm', 'equity'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 84,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: [],
    approval_status: 'pending',
    is_current_customer: false,
    is_former_customer: true,
    is_competitor_lien: true,
    ...overrides,
  } as LeadSummary;
}

const SWEEP: Array<Partial<LeadSummary>> = [
  {},
  { opportunity_score: 100, recommended_offer_code: 'heloc' },
  { opportunity_score: 0, recommended_offer_code: 'cash_out', state: 'TX' },
  { opportunity_score: 9, recommended_offer_code: 'purchase', segment_codes: ['listed'] },
  { recommended_offer_code: 'retention', is_current_customer: true },
  { recommended_offer_code: 'refi_plus_heloc', is_former_customer: false, is_competitor_lien: false },
  { recommended_offer_code: 'unknown_code', segment_codes: [] },
  { segment_codes: ['refi_propensity'], state: 'zz' },
];

describe('leadPattern', () => {
  it('maps the reviewed attributes to Lead Queue filter params', () => {
    const { params, chips } = leadPattern(lead());
    expect(params.toString()).toBe(
      'segment=itm&product=Refi&state=IL&lender_relationship=Former+customer&min_opportunity_score=80&max_opportunity_score=89',
    );
    expect(chips.map((chip) => chip.label)).toEqual(['SEGMENT', 'PRODUCT', 'STATE', 'RELATIONSHIP', 'SCORE']);
    expect(chips.find((chip) => chip.label === 'SCORE')?.value).toBe('80–89');
  });

  it('maps every offer code it knows, and adds no product for refi_plus_heloc or an unknown code', () => {
    const product = (code: string) => leadPattern(lead({ recommended_offer_code: code })).params.get('product');
    expect(['refi', 'heloc', 'cash_out', 'purchase', 'retention'].map(product)).toEqual(['Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention']);
    expect(product('refi_plus_heloc')).toBeNull();
    expect(product('zyrplax')).toBeNull();
  });

  it('picks the first true relationship, in order, and omits none', () => {
    const relationship = (overrides: Partial<LeadSummary>) => leadPattern(lead(overrides)).params.get('lender_relationship');
    expect(relationship({ is_current_customer: true })).toBe('Current customer');
    expect(relationship({ is_current_customer: false })).toBe('Former customer');
    expect(relationship({ is_former_customer: false })).toBe('Competitor customer');
    expect(relationship({ is_former_customer: false, is_competitor_lien: false })).toBeNull();
  });

  it('clamps the score band to 0..100', () => {
    const band = (score: number) => {
      const { params } = leadPattern(lead({ opportunity_score: score }));
      return [params.get('min_opportunity_score'), params.get('max_opportunity_score')];
    };
    expect(band(100)).toEqual(['100', '100']);
    expect(band(0)).toEqual(['0', '9']);
    expect(band(57)).toEqual(['50', '59']);
  });

  it.each(SWEEP)('carries only the closed key set, and proxy fields never change it (%o)', (overrides) => {
    const base = leadPattern(lead(overrides));
    for (const key of base.params.keys()) expect(LEAD_PATTERN_KEYS).toContain(key);
    const proxies = leadPattern(lead({
      ...overrides,
      borrower_id: 'B-PATTERN999999',
      zip: '75201',
      city: 'Dallas',
      clip: 'clip_9',
      listed_for_sale: true,
      listing_price: 999999,
      heloc_propensity_score: 0.99,
      refi_propensity_score: 0.01,
      has_permit: true,
      is_investor: true,
      related_property_count: 7,
      equity_estimate: 1,
      rate_spread_bps: -500,
      confidence: 1,
      display_name: 'Someone Else',
    }));
    expect(proxies.params.toString()).toBe(base.params.toString());
    expect(base.params.toString()).not.toMatch(/B-|zip|city|county|cbsa/);
  });
});
