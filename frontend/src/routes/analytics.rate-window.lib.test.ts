/**
 * Pure-model pins for the "Why now" rate window (dataviz-08 / dataviz-06):
 * the 1-2-5 nice ticks, the spread arithmetic and sentence, the labelled
 * spread-screen line, and the shared-axis layout.
 */
import { describe, expect, it } from 'vitest';
import type { RateWindowResponse } from '../types';
import {
  buildRateWindowModel,
  buildXTicks,
  formatMonthYear,
  itmY,
  niceTicks,
  rateY,
  spreadBps,
  spreadSentence,
} from './analytics.rate-window.lib';

function response(overrides: Partial<RateWindowResponse> = {}): RateWindowResponse {
  const weeks = [
    { week: '2026-03-30', market_rate_pct: 6.45, book_median_pct: 7.1, book_p25_pct: 6.55, book_p75_pct: 7.62, itm_count: 1680 },
    { week: '2026-04-06', market_rate_pct: 6.37, book_median_pct: 7.1, book_p25_pct: 6.55, book_p75_pct: 7.62, itm_count: 1776 },
    { week: '2026-04-13', market_rate_pct: 6.22, book_median_pct: 7.1, book_p25_pct: 6.55, book_p75_pct: 7.62, itm_count: 1956, is_latest: true },
  ];
  return {
    series_id: 'MORTGAGE30US',
    weeks,
    book_lien_count: 48_210,
    book_as_of: '2026-04-16 06:00:00',
    thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
    provenance: {
      market_rate_source: 'mip.silver.market_rates_weekly',
      book_source: 'mip.gold.borrower_360 + mip.silver.lien_current',
      gold_source: 'mip.gold.rate_window_weekly',
      rule_source: 'mip.gold.fn_rate_spread + mip.gold.fn_in_the_money',
      book_as_of: '2026-04-16 06:00:00',
      refreshed_at: '2026-04-16 06:00:00',
      note: 'test',
    },
    ...overrides,
  };
}

describe('niceTicks', () => {
  it('rounds the bounds outward onto a 1-2-5 step', () => {
    expect(niceTicks(6.0, 7.7, 5)).toEqual([6, 6.5, 7, 7.5, 8]);
    expect(niceTicks(0, 1956, 5)).toEqual([0, 500, 1000, 1500, 2000]);
    expect(niceTicks(0, 1956, 4)).toEqual([0, 1000, 2000]);
    expect(niceTicks(6.2, 6.3, 5)).toEqual([6.2, 6.25, 6.3]);
  });

  it('never returns a single-point or NaN domain', () => {
    expect(niceTicks(0, 0)).toEqual([0, 1]);
    expect(niceTicks(6.3, 6.3).length).toBeGreaterThan(1);
    expect(niceTicks(Number.NaN, 1)).toEqual([]);
  });
});

describe('spread arithmetic', () => {
  it('states the basis points the market print sits below the book median', () => {
    expect(spreadBps(7.1, 6.22)).toBe(88);
    expect(spreadSentence(88)).toBe("The 30-year is 88 bps below the book's median note rate.");
  });

  it('rounds half to even like the governed fn_rate_spread (BROUND)', () => {
    expect(spreadBps(6.125, 6.0)).toBe(12);
    expect(spreadBps(6.135, 6.0)).toBe(14);
    expect(spreadBps(6.0, 6.125)).toBe(-12);
    expect(spreadBps(6.0, 6.135)).toBe(-14);
    expect(spreadBps(6.126, 6.0)).toBe(13);
  });

  it('flips the direction when the market is above the book and handles parity', () => {
    expect(spreadBps(6.0, 6.4)).toBe(-40);
    expect(spreadSentence(-40)).toBe("The 30-year is 40 bps above the book's median note rate.");
    expect(spreadSentence(0)).toBe("The 30-year matches the book's median note rate.");
    expect(spreadSentence(1250)).toContain('1,250 bps');
  });
});

describe('buildRateWindowModel', () => {
  it('anchors the sentence, the threshold line and the current print on the latest week', () => {
    const model = buildRateWindowModel(response());
    expect(model).not.toBeNull();
    if (!model) return;
    expect(model.current.week).toBe('2026-04-13');
    expect(model.spreadBps).toBe(88);
    expect(model.spreadSentence).toBe("The 30-year is 88 bps below the book's median note rate.");
    expect(model.itmSentence).toBe(
      "1,956 of 48,210 fixed-rate liens in the whole book are in the money at this week's rate (spread and equity screens).",
    );
    expect(model.threshold).toEqual({
      minSpreadBps: 75,
      ratePct: 6.35,
      label: 'Spread screen: 75 bps below the book median (6.35%)',
    });
    // The rate domain covers the band, the market line and the threshold; the
    // in-the-money domain starts at zero so the area reads as a count.
    expect(model.rate.min).toBeLessThanOrEqual(6.22);
    expect(model.rate.max).toBeGreaterThanOrEqual(7.62);
    expect(model.itm.ticks[0]).toBe(0);
    expect(model.itm.max).toBeGreaterThanOrEqual(1956);
    // Shared x-axis: first and last week pinned to the edges, oldest first.
    expect(model.points.map((p) => p.x)).toEqual([0, 50, 100]);
    expect(model.xTicks[0]).toEqual({ x: 0, label: 'Mar 2026', anchor: 'start', minor: false });
    expect(model.xTicks[model.xTicks.length - 1]).toMatchObject({ x: 100, label: 'Apr 2026', anchor: 'end', minor: false });
    // The image's accessible description carries every number the marks draw.
    expect(model.ariaLabel).toContain('30-year fixed 6.22% in the week of 2026-04-13');
    expect(model.ariaLabel).toContain('book median note rate of 7.10%');
    expect(model.ariaLabel).toContain('88 bps below');
    expect(model.ariaLabel).toContain('Spread screen: 75 bps below the book median (6.35%).');
    expect(model.ariaLabel).toContain('1,956 of 48,210');
  });

  it('maps values top-down inside each panel with the baseline at the bottom', () => {
    const model = buildRateWindowModel(response());
    if (!model) throw new Error('model');
    expect(rateY(model, model.rate.max)).toBeCloseTo(4);
    expect(rateY(model, model.rate.min)).toBeCloseTo(96);
    expect(itmY(model, 0)).toBe(100);
    expect(itmY(model, model.itm.max)).toBeCloseTo(8);
    expect(rateY(model, 6.35)).toBeGreaterThan(rateY(model, 7.1));
  });

  it('sorts unordered weeks and falls back to the last week when nothing is flagged latest', () => {
    const payload = response();
    payload.weeks = [payload.weeks[2], payload.weeks[0], payload.weeks[1]].map((w) => ({ ...w, is_latest: false }));
    const model = buildRateWindowModel(payload);
    if (!model) throw new Error('model');
    expect(model.points.map((p) => p.week)).toEqual(['2026-03-30', '2026-04-06', '2026-04-13']);
    expect(model.current.week).toBe('2026-04-13');
  });

  it('degrades honestly for an empty book and an empty series', () => {
    const empty = response({ thresholds: { min_spread_bps: null, min_equity_pct: null }, book_lien_count: 0 });
    empty.weeks = empty.weeks.map((w) => ({ ...w, book_median_pct: null, book_p25_pct: null, book_p75_pct: null, itm_count: 0 }));
    const model = buildRateWindowModel(empty);
    if (!model) throw new Error('model');
    expect(model.spreadBps).toBeNull();
    expect(model.threshold).toBeNull();
    expect(model.spreadSentence).toContain('empty');
    expect(model.itm.max).toBeGreaterThan(0);
    expect(buildRateWindowModel(response({ weeks: [] }))).toBeNull();
  });
});

describe('buildXTicks', () => {
  const points = (n: number) =>
    Array.from({ length: n }, (_, idx) => ({
      week: new Date(Date.UTC(2025, 1, 24 + idx * 7)).toISOString().slice(0, 10),
      x: n === 1 ? 50 : (idx / (n - 1)) * 100,
      marketPct: 6.5,
      medianPct: 7.1,
      p25Pct: 6.55,
      p75Pct: 7.62,
      itmCount: 0,
      isLatest: idx === n - 1,
    }));

  it('anchors the edge labels inside the plot and keeps first, central and last on a narrow plot', () => {
    const ticks = buildXTicks(points(60));
    expect(ticks).toHaveLength(6);
    expect(ticks.map((t) => t.anchor)).toEqual(['start', 'middle', 'middle', 'middle', 'middle', 'end']);
    // Six ticks at 0, 20.3, 40.7, 61.0, 81.4 and 100: 40.7 is the closest to the centre.
    expect(ticks.map((t) => t.minor)).toEqual([false, true, false, true, true, false]);
  });

  it('centres a lone week and never marks it minor', () => {
    expect(buildXTicks(points(1))).toEqual([{ x: 50, label: 'Feb 2025', anchor: 'middle', minor: false }]);
  });
});

describe('formatMonthYear', () => {
  it('labels the shared axis by month and year in UTC', () => {
    expect(formatMonthYear('2021-01-04')).toBe('Jan 2021');
    expect(formatMonthYear('2026-12-28')).toBe('Dec 2026');
    expect(formatMonthYear('garbage')).toBe('garbage');
  });
});
