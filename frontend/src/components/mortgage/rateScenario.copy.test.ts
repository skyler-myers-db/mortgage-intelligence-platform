import { describe, expect, it } from 'vitest';
import { LEAD_BOUND_LIMITS, parseLeadBound } from '../../routes/lead-queue.filters';
import { largestChanges, scenarioCohortLink, scenarioHeadline, scenarioValueText } from './rateScenario.copy';

describe('rateScenario.copy', () => {
  it('states today at step 0', () => {
    expect(
      scenarioHeadline({ step: 0, ratePct: 6.3, inTheMoney: 991_931, today: 991_931, contactable: 40_100, scopeName: null }),
    ).toBe("At today's 30-year par rate (6.30%), 991,931 borrowers clear this refresh's refi screen; 40,100 of them are contactable.");
    expect(scenarioValueText({ step: 0, ratePct: 6.3, inTheMoney: 991_931 })).toBe(
      "Par rate 6.30%, today's rate: 991,931 borrowers in the money.",
    );
  });

  it('states a fall as more than today', () => {
    expect(
      scenarioHeadline({ step: -50, ratePct: 5.8, inTheMoney: 1_204_331, today: 991_931, contactable: 46_100, scopeName: null }),
    ).toBe(
      "If the 30-year par rate were 0.50 points lower (5.80%), 1,204,331 borrowers would clear this refresh's refi screen: 212,400 more than today; 46,100 of them are contactable.",
    );
    expect(scenarioValueText({ step: -50, ratePct: 5.8, inTheMoney: 1_204_331 })).toBe(
      'Par rate 5.80%, down 50 basis points: 1,204,331 borrowers in the money.',
    );
  });

  it('states a rise as fewer than today, for a drilled state', () => {
    expect(
      scenarioHeadline({ step: 25, ratePct: 6.55, inTheMoney: 800, today: 1_000, contactable: 30, scopeName: 'Texas' }),
    ).toBe(
      "If the 30-year par rate were 0.25 points higher (6.55%), 800 borrowers in Texas would clear this refresh's refi screen: 200 fewer than today; 30 of them are contactable.",
    );
    expect(scenarioValueText({ step: 25, ratePct: 6.55, inTheMoney: 800 })).toBe(
      'Par rate 6.55%, up 25 basis points: 800 borrowers in the money.',
    );
  });

  it('drops the contactable clause when it is not reported, and says "the same" for no change', () => {
    expect(
      scenarioHeadline({ step: 75, ratePct: 7.05, inTheMoney: 1, today: 1, contactable: null, scopeName: null }),
    ).toBe(
      "If the 30-year par rate were 0.75 points higher (7.05%), 1 borrower would clear this refresh's refi screen: the same as today.",
    );
    expect(
      scenarioHeadline({ step: 0, ratePct: 6.3, inTheMoney: 12, today: 12, contactable: null, scopeName: null }),
    ).toBe("At today's 30-year par rate (6.30%), 12 borrowers clear this refresh's refi screen.");
  });
});

const NAMES: Record<string, string> = { tx: 'Texas', fl: 'Florida', ca: 'California', il: 'Illinois' };
const nameOf = (id: string) => NAMES[id] ?? id.toUpperCase();

describe('largestChanges (wow-stage-1)', () => {
  it('names the three largest gains for a par fall, signed, in order', () => {
    expect(largestChanges({ tx: 1_234, fl: 980, ca: 870, il: 12 }, nameOf, -50)).toBe(
      'Largest in-the-money gains: Texas +1,234 · Florida +980 · California +870',
    );
  });

  it('names the three largest drops for a par rise', () => {
    expect(largestChanges({ tx: -1_234, fl: -980, ca: -870, il: -12 }, nameOf, 50)).toBe(
      'Largest in-the-money drops: Texas -1,234 · Florida -980 · California -870',
    );
  });

  it('breaks ties by name, and falls back to the USPS code off the map', () => {
    expect(largestChanges({ tx: 50, pr: 50, ca: 50, il: 50 }, nameOf, -25)).toBe(
      'Largest in-the-money gains: California +50 · Illinois +50 · PR +50',
    );
  });

  it('is null at step 0, without a change map, or when nothing moved', () => {
    expect(largestChanges({ tx: 5 }, nameOf, 0)).toBeNull();
    expect(largestChanges(null, nameOf, -25)).toBeNull();
    expect(largestChanges({ tx: 0, fl: 0 }, nameOf, -25)).toBeNull();
  });

  it('lists fewer than three when fewer moved, never a zero', () => {
    expect(largestChanges({ tx: 7, fl: 0 }, nameOf, -25)).toBe('Largest in-the-money gains: Texas +7');
  });
});

describe('scenarioCohortLink (wow-stage-1 map-side cohort handoff)', () => {
  const bounds = (href: string) => {
    const url = new URL(href, 'https://mip.test');
    expect(url.pathname).toBe('/lead-queue');
    const sp = url.searchParams;
    return {
      min: parseLeadBound('min_rate_spread_bps', sp.get('min_rate_spread_bps')),
      max: parseLeadBound('max_rate_spread_bps', sp.get('max_rate_spread_bps')),
      segment: sp.get('segment'),
      state: sp.get('state'),
    };
  };

  it('opens the band a par fall brings onto the screen, below today\'s threshold', () => {
    const link = scenarioCohortLink(-50, 75, null);
    expect(link?.href).toBe('/lead-queue?min_rate_spread_bps=25&max_rate_spread_bps=74');
    expect(link?.label).toBe('Open borrowers within 50 bps of the refi screen in the Lead Queue');
    expect(bounds(link?.href ?? '')).toEqual({ min: 25, max: 74, segment: null, state: null });
  });

  it('opens the in-the-money band a par rise takes off the screen, scoped to a drilled state', () => {
    const link = scenarioCohortLink(25, 75, { id: 'tx' });
    expect(link?.href).toBe('/lead-queue?segment=itm&min_rate_spread_bps=75&max_rate_spread_bps=99&state=TX');
    expect(link?.label).toBe('Open in-the-money borrowers within 25 bps of dropping off the refi screen');
    expect(bounds(link?.href ?? '')).toEqual({ min: 75, max: 99, segment: 'itm', state: 'TX' });
  });

  it('is null at step 0 or without the server threshold, and never states a count', () => {
    expect(scenarioCohortLink(0, 75, null)).toBeNull();
    expect(scenarioCohortLink(-25, null, null)).toBeNull();
    expect(scenarioCohortLink(-25, undefined, null)).toBeNull();
    // The label names the move, never a borrower count.
    expect(scenarioCohortLink(-100, 75, null)?.label).toBe(
      'Open borrowers within 100 bps of the refi screen in the Lead Queue',
    );
  });

  it('clamps to the public bound range, the same range the Lead Queue accepts', () => {
    expect(LEAD_BOUND_LIMITS.rate_spread_bps).toEqual({ min: -1000, max: 5000 });
    const low = scenarioCohortLink(-100, -950, null);
    expect(bounds(low?.href ?? '')).toEqual({ min: -1000, max: -951, segment: null, state: null });
    const high = scenarioCohortLink(100, 4_950, null);
    expect(bounds(high?.href ?? '')).toEqual({ min: 4_950, max: 5_000, segment: 'itm', state: null });
    // Wholly outside the range: no link rather than an inverted pair the queue would drop.
    expect(scenarioCohortLink(100, 6_000, null)).toBeNull();
  });
});
