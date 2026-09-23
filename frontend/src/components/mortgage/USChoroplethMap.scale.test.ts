import { describe, expect, it } from 'vitest';
import {
  SQRT_SCALE_MAX_UNITS,
  buildChoroplethScale,
  classRanges,
  classify,
  formatBreak,
  quantileSorted,
  type ChoroplethScale,
  type MapClass,
} from './USChoroplethMap.scale';

/** The class a count falls in according to the legend's printed ranges (the screen-reader legend). */
function legendClass(scale: ChoroplethScale, count: number): MapClass | null {
  if (count <= 0) return null;
  const range = classRanges(scale).find((r) => count >= r.from && (r.to === null || count <= r.to));
  return range ? range.cls : null;
}

// The synthetic fixture footprint (tests/e2e/fixture/data/reference.ts).
const FIXTURE_STATES = [21480, 17920, 14650, 11230, 9040, 6710, 4980, 3543];

describe('choropleth class scale (dataviz-02)', () => {
  it('uses a square-root scale over [0, max] below the unit threshold', () => {
    const scale = buildChoroplethScale(FIXTURE_STATES);
    expect(scale?.kind).toBe('sqrt');
    expect(scale?.units).toBe(8);
    // Class k + 1 starts where sqrt(count / max) reaches k / 4.
    expect(scale?.breaks).toEqual([
      Math.ceil(21480 / 16),
      Math.ceil(21480 / 4),
      Math.ceil((21480 * 9) / 16),
    ]);
  });

  it('paints two states alike only when their magnitudes are close, not because of their rank', () => {
    // Six populated states where the runner-up is a tenth of the leader. A
    // rank quartile over six values always put the top two in the top class.
    const counts = [100_000, 10_000, 9_000, 8_000, 7_000, 6_000];
    const scale = buildChoroplethScale(counts);
    expect(scale?.kind).toBe('sqrt');
    expect(classify(scale, 100_000)).toBe(4);
    expect(classify(scale, 10_000)).toBe(2);
    // ...and two close magnitudes share a class honestly.
    const close = buildChoroplethScale([21480, 17920]);
    expect(classify(close, 21480)).toBe(classify(close, 17920));
  });

  it('switches to quartiles of the positive counts at the threshold', () => {
    const below = Array.from({ length: SQRT_SCALE_MAX_UNITS - 1 }, (_, i) => (i + 1) * 10);
    const at = Array.from({ length: SQRT_SCALE_MAX_UNITS }, (_, i) => (i + 1) * 10);
    expect(buildChoroplethScale(below)?.kind).toBe('sqrt');
    const scale = buildChoroplethScale([0, null, ...at]);
    expect(scale?.kind).toBe('quantile');
    expect(scale?.units).toBe(SQRT_SCALE_MAX_UNITS);
    const sorted = [...at].sort((a, b) => a - b);
    expect(scale?.breaks).toEqual([0.25, 0.5, 0.75].map((p) => Math.ceil(quantileSorted(sorted, p))));
    // Quartiles spread the units evenly: three per class for twelve units.
    const perClass = [1, 2, 3, 4].map((cls) => at.filter((count) => classify(scale, count) === cls).length);
    expect(perClass).toEqual([3, 3, 3, 3]);
  });

  it('gives a missing, zero or negative count no class', () => {
    const scale = buildChoroplethScale(FIXTURE_STATES);
    for (const count of [null, undefined, 0, -5, Number.NaN]) {
      expect(classify(scale, count)).toBeNull();
    }
    expect(buildChoroplethScale([0, null, undefined])).toBeNull();
    expect(classify(null, 10)).toBeNull();
  });

  it('paints the class the legend prints for every value', () => {
    const distributions = [
      FIXTURE_STATES,
      [5],
      [1, 1, 1],
      [94, 0, 12, 3],
      Array.from({ length: 40 }, (_, i) => Math.round(1.7 ** (i % 17)) + i),
      Array.from({ length: 212 }, (_, i) => 9000 - i * 41),
    ];
    for (const counts of distributions) {
      const scale = buildChoroplethScale(counts);
      expect(scale).not.toBeNull();
      if (!scale) continue;
      // Every unit's own count, and every integer across the domain edges.
      const probes = new Set<number>([...counts, 1, scale.max, scale.max + 1]);
      for (const b of scale.breaks) [b - 1, b, b + 1].forEach((v) => probes.add(v));
      for (const value of probes) {
        if (value <= 0) continue;
        expect(classify(scale, value), `${scale.kind} ${value}`).toBe(legendClass(scale, value));
      }
      // The legend's ranges are contiguous from 1 and never overlap.
      const ranges = classRanges(scale);
      expect(ranges[0].from).toBe(1);
      for (let i = 1; i < ranges.length; i += 1) expect(ranges[i].from).toBe((ranges[i - 1].to ?? 0) + 1);
      expect(ranges[ranges.length - 1].to).toBeNull();
    }
  });

  it('prints compact break values', () => {
    expect(formatBreak(53)).toBe('53');
    expect(formatBreak(1343)).toBe('1.3K');
    expect(formatBreak(12083)).toBe('12.1K');
    expect(formatBreak(1_851_040)).toBe('1.9M');
  });
});
