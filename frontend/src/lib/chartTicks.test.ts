/**
 * Goldens for the chart foundation's axis maths (stack-06 / dataviz-07 step
 * 1). The first block is the rate window's, moved here with niceTicks.
 */
import { describe, expect, it } from 'vitest';
import { formatCompact, formatCount } from './formatters';
import { niceDomain, niceTicks, niceTicksWithin } from './chartTicks';

/** A step is a 1, 2 or 5 x 10^k multiple and every tick a whole multiple of it. */
function expectOneTwoFive(ticks: number[]): void {
  expect(ticks.length).toBeGreaterThan(1);
  const step = ticks[1] - ticks[0];
  const mantissa = step / 10 ** Math.floor(Math.log10(step));
  expect([1, 2, 5]).toContain(Math.round(mantissa * 1e9) / 1e9);
  for (const tick of ticks) expect(Math.abs(tick / step - Math.round(tick / step))).toBeLessThan(1e-9);
}

describe('niceTicks (moved from the rate window)', () => {
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

describe('niceTicks on count axes', () => {
  it('replaces the old even split (24.1K / 18.08K / 12.05K) with 1-2-5 steps', () => {
    const ticks = niceTicks(0, 27_620, 5, { integer: true });
    expect(ticks).toEqual([0, 10_000, 20_000, 30_000]);
    expectOneTwoFive(ticks);
    expect(ticks.map((tick) => formatCompact(tick))).toEqual(['0', '10K', '20K', '30K']);
  });

  it('keeps the step at least 1, so a small count never prints a label twice', () => {
    expect(niceTicks(0, 2, 5)).toEqual([0, 0.5, 1, 1.5, 2]);
    const ticks = niceTicks(0, 2, 5, { integer: true });
    expect(ticks).toEqual([0, 1, 2]);
    const labels = ticks.map((tick) => formatCount(tick));
    expect(new Set(labels).size).toBe(labels.length);
    expect(niceTicks(0, 3, 5, { integer: true })).toEqual([0, 1, 2, 3]);
  });

  it('prints every label equal to its tick value', () => {
    for (const max of [7, 43, 180, 999, 2_480, 27_620, 413_000, 5_160_000]) {
      const ticks = niceTicks(0, max, 5, { integer: true });
      expectOneTwoFive(ticks);
      for (const tick of ticks) {
        const label = formatCompact(tick);
        const unit = label.endsWith('K') ? 1e3 : label.endsWith('M') ? 1e6 : label.endsWith('B') ? 1e9 : 1;
        expect(Number(label.replace(/[KMB]$/, '')) * unit, `${label} for ${tick}`).toBeCloseTo(tick, 6);
      }
    }
  });
});

describe('niceDomain', () => {
  it('is the first and last nice tick, so the top gridline is the domain maximum', () => {
    expect(niceDomain(0, 27_620, 5, { integer: true })).toEqual([0, 30_000]);
    expect(niceDomain(0, 1956)).toEqual([0, 2000]);
    expect(niceDomain(0, 0, 5, { integer: true })).toEqual([0, 1]);
    expect(niceDomain(Number.NaN, 1)).toEqual([0, 1]);
  });
});

describe('niceTicksWithin', () => {
  it('ticks the scatter spread domain without widening it', () => {
    expect(niceTicksWithin(-100, 400)).toEqual([-100, 0, 100, 200, 300, 400]);
  });

  it('ticks the equity domain at 1-2-5 steps inside [0, 100]', () => {
    expect(niceTicksWithin(0, 100)).toEqual([0, 20, 40, 60, 80, 100]);
  });

  it('finds at least three ticks in a narrow zoom window and never leaves it', () => {
    const ticks = niceTicksWithin(75, 99, 5, { integer: true });
    expect(ticks).toEqual([75, 80, 85, 90, 95]);
    expect(Math.min(...ticks)).toBeGreaterThanOrEqual(75);
    expect(Math.max(...ticks)).toBeLessThanOrEqual(99);
    expect(niceTicksWithin(40, 44, 5, { integer: true })).toEqual([40, 41, 42, 43, 44]);
  });

  it('keeps a degenerate window to its bounds', () => {
    expect(niceTicksWithin(40, 40, 5, { integer: true })).toEqual([40]);
    expect(niceTicksWithin(Number.NaN, 1)).toEqual([]);
  });
});
