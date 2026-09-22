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
    expect(prose).toContain('Primary offer: Refinance + home-equity review.');
  });

  // The common case. Pinned as a whole string, beside the investor golden
  // above: the 2026-09-21 audit (visual-v2) found every single-property story
  // opening with the fragment "This Chicago, IL homeowner." because only the
  // 41-property investor had a golden test.
  function singleProperty(overrides: Partial<Borrower360> = {}): Borrower360 {
    return dossier({
      related_property_count: 1,
      is_investor: false,
      current_rate: 5.75,
      rate_spread_bps: 88,
      ltv: 54,
      avm_value: 625_000,
      current_lien_balance: 340_000,
      // A label offerLanguage passes through verbatim (see the 5/1 ARM test).
      recommended_offer: 'Refi to 5/1 ARM',
      ...overrides,
    });
  }

  it('keeps the multi-property story as two sentences (golden)', () => {
    expect(buildBorrowerStory(dossier({ recommended_offer: 'Refi to 5/1 ARM' })).sentences).toEqual([
      'This Chicago, IL investor holds 41 properties via the owner graph.',
      'Carries a 10.04% rate, 356 bps above market, with 93% equity on a $42K lien.',
      'Primary offer: Refi to 5/1 ARM.',
    ]);
  });

  it('opens a single-property story with a full sentence, not a fragment (golden)', () => {
    const story = buildBorrowerStory(singleProperty());
    expect(story.sentences).toEqual([
      'This Chicago, IL homeowner carries a 5.75% rate, 88 bps above market, with 46% equity on a $340K lien.',
      'Primary offer: Refi to 5/1 ARM.',
    ]);
    // Same claims, same order, all verified: joining the sentences must not
    // touch claim registration or the number verifier.
    expect(story.claims.map((c) => [c.field, c.token, c.verified])).toEqual([
      ['current_rate', '5.75%', true],
      ['rate_spread_bps', '88 bps', true],
      ['ltv', '46% equity', true],
      ['current_lien_balance', '$340K', true],
    ]);
    expect(story.unverifiedTokens).toEqual([]);
    expect(story.allVerified).toBe(true);
  });

  it('never emits a verbless opening sentence, whichever figure leads', () => {
    const fragment = /^This (?:[A-Z][^.]*, [A-Z]{2} )?(?:investor|current customer|former customer|owner-occupant|homeowner)\.$/;
    const cases: Array<[Partial<Borrower360>, string]> = [
      [{}, 'This Chicago, IL homeowner carries a 5.75% rate, 88 bps above market, with 46% equity on a $340K lien.'],
      // No current rate on file: the spread leads and needs its own verb.
      [{ current_rate: 0 }, 'This Chicago, IL homeowner is 88 bps above market, with 46% equity on a $340K lien.'],
      // Free and clear: only equity is known.
      [
        { current_rate: 0, rate_spread_bps: 0, current_lien_balance: 0, ltv: 0 },
        'This Chicago, IL homeowner has 100% equity.',
      ],
      // Nothing economic to say: a plain sentence, no figure, nothing to verify.
      [
        { current_rate: 0, rate_spread_bps: 0, current_lien_balance: 0, avm_value: 0 },
        'This borrower is a homeowner in Chicago, IL.',
      ],
      [
        { current_rate: 0, rate_spread_bps: 0, avm_value: 0, is_owner_occupied: true, city: null as never },
        'This borrower is an owner-occupant.',
      ],
    ];
    for (const [overrides, expected] of cases) {
      const story = buildBorrowerStory(singleProperty(overrides));
      expect(story.sentences[0]).toBe(expected);
      expect(story.sentences[0]).not.toMatch(fragment);
      expect(story.allVerified).toBe(true);
    }
  });

  it('still flags a stray number in the locale of a figure-free single-property story', () => {
    const story = buildBorrowerStory(
      singleProperty({ current_rate: 0, rate_spread_bps: 0, avm_value: 0, city: 'Area 51' }),
    );
    expect(story.sentences[0]).toBe('This borrower is a homeowner in Area 51, IL.');
    expect(story.unverifiedTokens).toEqual(['51']);
    expect(story.allVerified).toBe(false);
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
    expect(story.sentences.join(' ')).toContain('Primary offer: Refi to 5/1 ARM.');
  });

  it('gates the equity claim when AVM is unavailable (no fabricated equity)', () => {
    const story = buildBorrowerStory(dossier({ avm_value: 0 }));
    expect(story.claims.some((c) => c.field === 'ltv')).toBe(false); // no equity claim
    expect(story.sentences.join(' ')).not.toContain('93% equity');
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

  it('catches a stray number even when it collides with a claim value (count, not set)', () => {
    // "District 41" + related_property_count 41: two "41"s in the prose, one
    // claimed — the excess must be flagged (a set-membership check would miss it).
    const story = buildBorrowerStory(dossier({ city: 'District 41', related_property_count: 41 }));
    expect(story.unverifiedTokens).toContain('41');
    expect(story.allVerified).toBe(false);
  });

  it('omits the rate-spread clause for a zero or negative (at/below-market) spread', () => {
    const zero = buildBorrowerStory(dossier({ rate_spread_bps: 0 }));
    expect(zero.sentences.join(' ')).not.toContain('bps above market');
    expect(zero.allVerified).toBe(true);
    const below = buildBorrowerStory(dossier({ rate_spread_bps: -120 }));
    expect(below.sentences.join(' ')).not.toContain('above market');
    expect(below.sentences.join(' ')).not.toContain('-120');
    expect(below.allVerified).toBe(true); // no backwards prose, no false flag
  });

  it('omits the locale prefix when city/state are absent (no "undefined")', () => {
    const story = buildBorrowerStory(dossier({ city: null as never, state: null as never }));
    const prose = story.sentences.join(' ');
    expect(prose).not.toContain('undefined');
    expect(prose).toMatch(/^This investor/);
  });

  it('degrades cleanly on a sparse dossier with no fabricated figures', () => {
    const story = buildBorrowerStory(dossier({
      related_property_count: 1, is_investor: false,
      current_rate: 0, rate_spread_bps: 0, current_lien_balance: 0, avm_value: 0,
    }));
    expect(story.claims).toEqual([]); // nothing to claim
    expect(story.unverifiedTokens).toEqual([]);
    expect(story.allVerified).toBe(true);
  });
});
