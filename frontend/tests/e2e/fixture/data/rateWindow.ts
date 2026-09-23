/**
 * "Why now" rate-window fixture (lane rate-window, dataviz-08): a synthetic
 * 60-week MORTGAGE30US series ending on 2026-04-13 against one fixed-rate
 * book band. The numbers are chosen so the spec can state the expected
 * sentence exactly: book median 7.10% against a current print of 6.22% is
 * 88 bps, the spread screen sits 75 bps below the median at 6.35%, and the
 * last week counts 1,956 of 48,210 liens in the money.
 */
import type { RateWindowResponse, RateWindowWeek } from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { SNAPSHOT_AT } from './reference';

export const RATE_WINDOW_WEEK_COUNT = 60;
const FIRST_WEEK_UTC = Date.UTC(2025, 1, 24); // Monday 2025-02-24
const BOOK = { median: 7.1, p25: 6.55, p75: 7.62, lienCount: 48_210 } as const;
const THRESHOLDS = { min_spread_bps: 75, min_equity_pct: 15 } as const;
const CURRENT_MARKET_RATE = 6.22;

function isoWeek(index: number): string {
  return new Date(FIRST_WEEK_UTC + index * 7 * 86_400_000).toISOString().slice(0, 10);
}

function marketRate(index: number): number {
  const last = RATE_WINDOW_WEEK_COUNT - 1;
  if (index === last) return CURRENT_MARKET_RATE;
  const drift = (BOOK.median - CURRENT_MARKET_RATE) * (index / last);
  const wobble = (((index * 7) % 5) - 2) * 0.01;
  return Number((BOOK.median - drift + wobble).toFixed(2));
}

export const RATE_WINDOW_WEEKS: readonly RateWindowWeek[] = Array.from({ length: RATE_WINDOW_WEEK_COUNT }, (_, index) => {
  const market_rate_pct = marketRate(index);
  return {
    week: isoWeek(index),
    market_rate_pct,
    book_median_pct: BOOK.median,
    book_p25_pct: BOOK.p25,
    book_p75_pct: BOOK.p75,
    itm_count: Math.round(900 + (BOOK.median - market_rate_pct) * 1200),
    is_latest: index === RATE_WINDOW_WEEK_COUNT - 1,
  };
});

export const RATE_WINDOW: RateWindowResponse = {
  series_id: 'MORTGAGE30US',
  weeks: [...RATE_WINDOW_WEEKS],
  book_lien_count: BOOK.lienCount,
  book_as_of: SNAPSHOT_AT,
  thresholds: { ...THRESHOLDS },
  provenance: {
    market_rate_source: 'mip.silver.market_rates_weekly',
    book_source: 'mip.gold.borrower_360 + mip.silver.lien_current',
    gold_source: 'mip.gold.rate_window_weekly',
    rule_source: 'mip.gold.fn_rate_spread + mip.gold.fn_in_the_money',
    book_as_of: SNAPSHOT_AT,
    refreshed_at: SNAPSHOT_AT,
    note: "Today's book against the historical rate (fixture).",
  },
};

/** What the rendered panel must say for the numbers above. */
export const RATE_WINDOW_EXPECTED = {
  firstWeek: isoWeek(0),
  lastWeek: isoWeek(RATE_WINDOW_WEEK_COUNT - 1),
  currentPrint: '6.22%',
  spreadSentence: "The 30-year is 88 bps below the book's median note rate.",
  itmSentence: "1,956 of 48,210 fixed-rate liens in the whole book are in the money at this week's rate (spread and equity screens).",
  thresholdLabel: 'Spread screen: 75 bps below the book median (6.35%)',
} as const;

/**
 * Edge case for the spread-screen label: a book whose median is also its p75
 * and the top of the rate domain (nice ticks 5.0..7.0 add no headroom), with
 * a 5 bps screen, so the screen line sits about 6% from the top of the plot.
 */
export const RATE_WINDOW_SCREEN_NEAR_TOP: RateWindowResponse = {
  ...RATE_WINDOW,
  weeks: RATE_WINDOW_WEEKS.map((week, index) => ({
    ...week,
    market_rate_pct: Number((5 + index / (RATE_WINDOW_WEEK_COUNT - 1)).toFixed(2)),
    book_median_pct: 7,
    book_p25_pct: 6.8,
    book_p75_pct: 7,
  })),
  thresholds: { ...THRESHOLDS, min_spread_bps: 5 },
};

export const rateWindowFixtures: FixtureEntry[] = [
  fixture('GET', '/api/analytics/rate-window', () => json<RateWindowResponse>(RATE_WINDOW)),
];
