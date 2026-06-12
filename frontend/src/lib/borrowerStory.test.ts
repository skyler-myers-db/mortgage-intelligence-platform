import { describe, expect, it } from 'vitest';
import type { Borrower360 } from '../types';
import { buildBorrowerStory } from './borrowerStory';

function dossier(overrides: Partial<Borrower360> = {}): Borrower360 {
  return {
    borrower_id: 'B-1ABCDEFGHIJK2',
    city: 'Chicago',
    state: 'IL',
    zip: '60617',
    segment_codes: ['investor', 'itm'],
    equity_estimate: 420_000,
    rate_spread_bps: 356,
    opportunity_score: 88,
    confidence: 80,
    recommended_offer: 'Refinance + HELOC',
    why_now: 'Rate spread and equity both clear the bar.',
    is_investor: true,
    related_property_count: 41,
    avm_value: 600_000,
    current_lien_balance: 42_000,
    current_rate: 10.04,
    ltv: 7,
    clip_id: 'clip_x',
    owner_link_id: 'owner_x',
    ...overrides,
  } as unknown as Borrower360;
}

describe('buildBorrowerStory', () => {
  it('composes the audit-shaped narrative grounded in the dossier', () => {
    const story = buildBorrowerStory(dossier());
    const prose = story.sentences.join(' ');
    expect(prose).toContain('Chicago, IL investor');
    expect(prose).toContain('41 properties');
    expect(prose).toContain('10.04%'); // current rate
    expect(prose).toContain('356 bps above market');
    expect(prose).toContain('93% equity'); // 100 - ltv(7)
    expect(prose).toContain('Refinance + HELOC is the next-best move');
  });

  it('verifies every numeric claim against its source field', () => {
    const story = buildBorrowerStory(dossier());
    expect(story.allVerified).toBe(true);
    expect(story.unverifiedTokens).toEqual([]);
    const byField = Object.fromEntries(story.claims.map((c) => [c.field, c]));
    expect(byField.related_property_count.token).toBe('41 properties');
    expect(byField.related_property_count.verified).toBe(true);
    expect(byField.rate_spread_bps.token).toBe('356 bps');
    expect(byField.ltv.token).toBe('93% equity'); // equity derived from LTV
    expect(byField.current_rate.token).toBe('10.04%');
    expect(story.claims.every((c) => c.verified)).toBe(true);
  });

  it('does NOT flag digits inside the offer label as un-grounded', () => {
    // "5/1 ARM" carries digits that are a product name, not a borrower claim.
    const story = buildBorrowerStory(dossier({ recommended_offer: 'Refi to 5/1 ARM' }));
    expect(story.unverifiedTokens).toEqual([]);
    expect(story.allVerified).toBe(true);
    expect(story.sentences.join(' ')).toContain('Refi to 5/1 ARM is the next-best move');
  });

  it('gates the equity claim when AVM is unavailable (no fabricated equity)', () => {
    const story = buildBorrowerStory(dossier({ avm_value: 0 }));
    expect(story.claims.some((c) => c.field === 'ltv')).toBe(false); // no equity claim
    expect(story.sentences.join(' ')).not.toContain('equity');
    expect(story.allVerified).toBe(true); // remaining claims still verify
  });

  it('omits the property-count clause for a single-property owner', () => {
    const story = buildBorrowerStory(dossier({ related_property_count: 1, is_investor: false }));
    expect(story.sentences[0]).not.toContain('properties');
    expect(story.claims.some((c) => c.field === 'related_property_count')).toBe(false);
  });

  it('flags an un-grounded number if one ever reaches the claim-bearing prose', () => {
    // A city containing a digit is the realistic way a stray number lands in
    // s1; the verifier must catch it (defends the "evidence-verified" badge).
    const story = buildBorrowerStory(dossier({ city: 'Area 51' }));
    expect(story.unverifiedTokens).toContain('51');
    expect(story.allVerified).toBe(false);
  });
});
