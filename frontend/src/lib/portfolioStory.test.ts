import { describe, expect, it } from 'vitest';
import type { PortfolioPreview } from '../types';
import { buildPortfolioStory } from './portfolioStory';

function preview(o: Partial<PortfolioPreview> = {}): PortfolioPreview {
  return {
    marketable_population: 5_156_184,
    high_intent_leads: 111_726,
    top_tier_opportunities: 3_878,
    offers_recommended: 4_467_395,
    avg_score: 71,
    data_refreshed_at: '2026-06-12T04:16:35Z',
    day_zero: false,
    approved_count: 8,
    in_outreach_count: 3,
    trends: {},
    trend_status: 'live',
    ...o,
  } as unknown as PortfolioPreview;
}

describe('buildPortfolioStory', () => {
  it('composes a grounded, fully-verified current-state summary', () => {
    const s = buildPortfolioStory(preview());
    expect(s.available).toBe(true);
    const prose = s.sentences.join(' ');
    // Locale-grouped counts and the derived in-the-money share all appear.
    expect(prose).toContain('5,156,184');
    expect(prose).toContain('111,726');
    expect(prose).toContain('2.2%'); // 111,726 / 5,156,184 = 2.167% → 2.2%
    expect(prose).toContain('3,878');
    expect(prose).toContain('4,467,395');
    // Every figure verifies and the stray-number scan is clean despite commas.
    expect(s.allVerified).toBe(true);
    expect(s.unverifiedTokens).toEqual([]);
    expect(s.asOf).toBe('2026-06-12T04:16:35Z');
  });

  it('grounds each figure to the right gold-table source drawer', () => {
    const s = buildPortfolioStory(preview());
    const byLabel = Object.fromEntries(s.claims.map((c) => [c.label, c.sourceKey]));
    expect(byLabel['Marketable population']).toBe('population');
    expect(byLabel['In-the-money leads']).toBe('itm');
    expect(byLabel['In-the-money share']).toBe('itm');
    expect(byLabel['Top-tier opportunities']).toBe('leadScore');
    expect(byLabel['Offers recommended']).toBe('nbo');
    expect(s.claims.every((c) => c.verified)).toBe(true);
  });

  it('renders a whole-percent share without a decimal when >= 10%', () => {
    const s = buildPortfolioStory(preview({ marketable_population: 1_000, high_intent_leads: 250 }));
    expect(s.sentences.join(' ')).toContain('25%');
    expect(s.allVerified).toBe(true);
  });

  it('omits the top-tier clause when that count is null, still fully verified', () => {
    const s = buildPortfolioStory(preview({ top_tier_opportunities: null }));
    expect(s.sentences.join(' ')).not.toContain('top-tier opportunity bar');
    expect(s.claims.find((c) => c.label === 'Top-tier opportunities')).toBeUndefined();
    expect(s.allVerified).toBe(true);
  });

  it('omits the offers sentence when offers_recommended is null', () => {
    const s = buildPortfolioStory(preview({ offers_recommended: null }));
    expect(s.sentences.join(' ')).not.toContain('recommended next-best offer');
    expect(s.sentences).toHaveLength(2);
  });

  it('is unavailable on day-zero, null preview, or an empty book', () => {
    expect(buildPortfolioStory(preview({ day_zero: true })).available).toBe(false);
    expect(buildPortfolioStory(null).available).toBe(false);
    expect(buildPortfolioStory(undefined).available).toBe(false);
    expect(buildPortfolioStory(preview({ marketable_population: 0 })).available).toBe(false);
  });
});
