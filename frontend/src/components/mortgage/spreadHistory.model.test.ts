import { describe, expect, it } from 'vitest';
import type { RateWindowWeek } from '../../types';
import { buildSpreadHistoryModel, marketPath, mondayOf, spreadPolygon } from './spreadHistory.model';

function weeks(start: string, rates: readonly number[]): RateWindowWeek[] {
  const day0 = Date.parse(`${start}T00:00:00Z`);
  return rates.map((rate, index) => ({
    week: new Date(day0 + index * 7 * 86_400_000).toISOString().slice(0, 10),
    market_rate_pct: rate,
    itm_count: 0,
    is_latest: index === rates.length - 1,
  }));
}

// A half-basis-point boundary (audit wow-stage-4): note 6.875%, screen 75 bps.
// The 6.13% week is (6.875 - 6.13) * 100 = 74.5 bps: the governed
// fn_rate_spread BROUNDs it to 74 (not in the money), while a client-side
// Math.round would read 75 (in the money) because of the binary 74.50000000000001.
// So gold's first_itm_week is the 6.10% week, one week LATER than a naive
// client derivation would put it.
const BOUNDARY = weeks('2025-01-06', [7.2, 6.9, 6.5, 6.13, 6.1, 6.05]);
const SERVER_FIRST_ITM_WEEK = BOUNDARY[4].week; // the 6.10% week

describe('mondayOf', () => {
  it('truncates to the week-starting Monday, like Spark DATE_TRUNC(WEEK)', () => {
    expect(mondayOf('2025-01-06')).toBe('2025-01-06'); // a Monday
    expect(mondayOf('2025-01-12')).toBe('2025-01-06'); // the Sunday after
    expect(mondayOf('2019-03-15')).toBe('2019-03-11');
    expect(mondayOf('2024-02-29')).toBe('2024-02-26');
  });
});

describe('buildSpreadHistoryModel', () => {
  it('takes the crossing from first_itm_week and never derives it from the rates', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: SERVER_FIRST_ITM_WEEK,
      firstPosDate: '2018-05-01',
    });
    expect(model).not.toBeNull();
    const naive = BOUNDARY.find((week) => Math.round((6.875 - week.market_rate_pct) * 100) >= 75);
    expect(naive?.week, 'the fixture discriminates: a client derivation lands a week early').toBe(BOUNDARY[3].week);
    expect(model!.crossing?.week).toBe(SERVER_FIRST_ITM_WEEK);
    const serverPoint = model!.points.find((point) => point.week === SERVER_FIRST_ITM_WEEK);
    expect(model!.crossing?.x).toBeCloseTo(serverPoint!.x, 6);
    expect(model!.rows.filter((row) => row.crossing).map((row) => row.week)).toEqual([SERVER_FIRST_ITM_WEEK]);
  });

  it('has no crossing when the server says not in the money', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: null,
      firstPosDate: null,
    });
    expect(model!.crossing).toBeNull();
    expect(model!.rows.some((row) => row.crossing)).toBe(false);
  });

  it('clips the domain to the origination week and keeps the series start for left-censoring', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: SERVER_FIRST_ITM_WEEK,
      // A Thursday inside the third week: the domain starts on its Monday.
      firstPosDate: '2025-01-23',
    });
    expect(model!.seriesStart).toBe('2025-01-06');
    expect(model!.domainStart).toBe('2025-01-20');
    expect(model!.points.map((point) => point.week)).toEqual(BOUNDARY.slice(2).map((week) => week.week));
    expect(model!.points[0].x).toBe(0);
    expect(model!.points[model!.points.length - 1].x).toBe(100);
    expect(model!.crossing?.leftCensored).toBe(false);
  });

  it('flags a run that reaches the series start as left-censored', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 9,
      minSpreadBps: 75,
      firstItmWeek: BOUNDARY[0].week,
      firstPosDate: '2016-07-01',
    });
    expect(model!.domainStart).toBe(BOUNDARY[0].week);
    expect(model!.crossing).toEqual({ week: BOUNDARY[0].week, x: 0, leftCensored: true });
  });

  it('a lien newer than the latest print still draws the latest week', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: null,
      firstPosDate: '2026-01-01',
    });
    expect(model!.points.map((point) => point.week)).toEqual([BOUNDARY[5].week]);
  });

  it('clamps the spread at the note rate where the market sits above it', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: null,
      firstPosDate: null,
    })!;
    const above = model.points[0]; // 7.20% > 6.875%
    const below = model.points[5]; // 6.05% < 6.875%
    expect(above.marketY).toBeLessThan(model.noteY);
    expect(above.spreadY).toBe(model.noteY);
    expect(below.spreadY).toBe(below.marketY);
    expect(below.spreadY).toBeGreaterThan(model.noteY);
    expect(spreadPolygon(model).split(' ')).toHaveLength(2 + model.points.length);
  });

  it('draws the screen line at note - min_spread / 100 on the same scale', () => {
    const model = buildSpreadHistoryModel({
      weeks: BOUNDARY,
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: null,
      firstPosDate: null,
    })!;
    expect(model.screenPct).toBeCloseTo(6.125, 9);
    // Linear: the screen sits between the 6.13 and 6.10 weeks' y, nearer 6.13.
    const y613 = model.points[3].marketY;
    const y610 = model.points[4].marketY;
    expect(model.screenY).toBeGreaterThan(y613);
    expect(model.screenY).toBeLessThan(y610);
    const slope = (y610 - y613) / (6.1 - 6.13);
    expect(model.screenY).toBeCloseTo(y613 + slope * (6.125 - 6.13), 6);
    expect(model.noteY).toBeGreaterThan(0);
    expect(model.screenY).toBeLessThan(100);
  });

  it('returns null without weeks or without a note rate', () => {
    const base = { minSpreadBps: 75, firstItmWeek: null, firstPosDate: null };
    expect(buildSpreadHistoryModel({ ...base, weeks: [], noteRatePct: 6 })).toBeNull();
    expect(buildSpreadHistoryModel({ ...base, weeks: BOUNDARY, noteRatePct: 0 })).toBeNull();
  });

  it('orders an unsorted series and draws one path segment per week', () => {
    const model = buildSpreadHistoryModel({
      weeks: [...BOUNDARY].reverse(),
      noteRatePct: 6.875,
      minSpreadBps: 75,
      firstItmWeek: null,
      firstPosDate: null,
    })!;
    expect(model.points.map((point) => point.week)).toEqual(BOUNDARY.map((week) => week.week));
    const path = marketPath(model, 3.2, 1.4);
    expect(path.startsWith('M0.00 ')).toBe(true);
    expect(path.match(/L/g)).toHaveLength(BOUNDARY.length - 1);
  });
});
