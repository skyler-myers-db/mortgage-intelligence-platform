import { describe, expect, it } from 'vitest';
import { linearScale, tickEdgeClass, utcDay, utcDayScale } from './scales';
import { nearestIndex } from './useChartCursor';

describe('linearScale', () => {
  it('maps a domain onto a range, inverted ranges included', () => {
    const x = linearScale([30, 100], [0, 100]);
    expect(x(30)).toBe(0);
    expect(x(100)).toBe(100);
    expect(x(65)).toBe(50);
    const y = linearScale([0, 30_000], [96, 6]);
    expect(y(0)).toBe(96);
    expect(y(30_000)).toBe(6);
    expect(y(15_000)).toBe(51);
  });

  it('maps a zero-width domain to the range midpoint', () => {
    expect(linearScale([5, 5], [0, 100])(5)).toBe(50);
    expect(linearScale([5, 5], [0, 100])(9)).toBe(50);
  });
});

describe('utcDayScale', () => {
  it('positions dates by their UTC-day offset, so a gap keeps its width', () => {
    const x = utcDayScale('2026-05-01', '2026-05-05');
    expect(['2026-05-01', '2026-05-02', '2026-05-05'].map(x)).toEqual([0, 25, 100]);
  });

  it('counts calendar days across a month and a DST change without drift', () => {
    const x = utcDayScale('2026-03-01', '2026-03-31');
    expect(x('2026-03-08')).toBeCloseTo((7 / 30) * 100, 9);
    expect(x('2026-03-31')).toBe(100);
    expect((utcDay('2026-04-01') ?? 0) - (utcDay('2026-03-31') ?? 0)).toBe(1);
  });

  it('centres a single day and returns NaN for a date it cannot read', () => {
    expect(utcDayScale('2026-07-14', '2026-07-14')('2026-07-14')).toBe(50);
    expect(utcDayScale('2026-07-14', '2026-07-20')('not a date')).toBeNaN();
    expect(utcDayScale('garbage', '2026-07-20')('2026-07-14')).toBeNaN();
    expect(utcDay('2026-07-14 06:00:00')).toBe(utcDay('2026-07-14'));
  });
});

describe('tickEdgeClass', () => {
  it('anchors only the ticks at the plot edges', () => {
    expect(tickEdgeClass(0)).toBe(' analytics-chart__tick--edge-start');
    expect(tickEdgeClass(100)).toBe(' analytics-chart__tick--edge-end');
    expect(tickEdgeClass(50)).toBe('');
    expect(tickEdgeClass(95.2)).toBe('');
  });
});

describe('nearestIndex', () => {
  it('snaps to the nearest position, the first winning a tie', () => {
    expect(nearestIndex([0, 25, 100], 60)).toBe(1);
    expect(nearestIndex([0, 25, 100], 63)).toBe(2);
    expect(nearestIndex([0, 50, 100], 25)).toBe(0);
    expect(nearestIndex([10], 90)).toBe(0);
  });
});
