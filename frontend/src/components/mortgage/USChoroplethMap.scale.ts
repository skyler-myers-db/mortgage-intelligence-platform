/**
 * Class breaks for the geography map (audit dataviz-02 / responsive-05).
 *
 * One function decides which of the four fill classes a count paints, and the
 * legend prints the same breaks, so the map and its legend cannot disagree.
 *
 *  - Fewer than `SQRT_SCALE_MAX_UNITS` populated units (the usual state view:
 *    a footprint of six to ten states) use a continuous square-root scale over
 *    [0, max], cut into four equal steps of sqrt(count / max). A rank quantile
 *    over six values always paints the top two alike whatever their
 *    magnitudes; the sqrt scale only paints two units alike when their counts
 *    are close.
 *  - `SQRT_SCALE_MAX_UNITS` or more populated units (a state's ZIP rollup)
 *    use quartiles of the positive counts, which spread a long-tailed
 *    distribution evenly across the four classes.
 *
 * A count that is missing, zero or negative has no class (`null`): it paints
 * the ramp's base step, the legend's first swatch.
 */

import { formatCompact } from '../../lib/formatters';

export type MapClass = 1 | 2 | 3 | 4;
export type MapScaleKind = 'sqrt' | 'quantile';

/** Below this many populated units the sqrt scale is used; at or above it, quartiles. */
export const SQRT_SCALE_MAX_UNITS = 12;

export interface ChoroplethScale {
  kind: MapScaleKind;
  /** Populated units (count > 0) the scale was built from. */
  units: number;
  /** Largest populated count. */
  max: number;
  /**
   * Integer lower bounds of classes 2, 3 and 4: a count paints class k + 1
   * when it is at or above `breaks[k - 1]`. Non-decreasing; equal breaks
   * (heavy ties) collapse a class rather than reorder them.
   */
  breaks: readonly [number, number, number];
}

function positiveCounts(counts: ReadonlyArray<number | null | undefined>): number[] {
  return counts
    .filter((count): count is number => typeof count === 'number' && Number.isFinite(count) && count > 0)
    .sort((a, b) => a - b);
}

/** R-7 (linear interpolation) quantile of an ascending array, the d3 `quantileSorted` rule. */
export function quantileSorted(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  const h = (sorted.length - 1) * p;
  const lo = Math.floor(h);
  const hi = Math.min(sorted.length - 1, lo + 1);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (h - lo);
}

/** The scale for one set of unit counts, or null when no unit has a positive count. */
export function buildChoroplethScale(counts: ReadonlyArray<number | null | undefined>): ChoroplethScale | null {
  const sorted = positiveCounts(counts);
  if (sorted.length === 0) return null;
  const max = sorted[sorted.length - 1];
  if (sorted.length < SQRT_SCALE_MAX_UNITS) {
    // Class k + 1 starts where sqrt(count / max) reaches k / 4. Counts are
    // integers, so `count >= ceil(b)` is the same test as `count >= b`.
    const at = (k: number) => Math.ceil(max * (k / 4) ** 2);
    return { kind: 'sqrt', units: sorted.length, max, breaks: [at(1), at(2), at(3)] };
  }
  const at = (p: number) => Math.ceil(quantileSorted(sorted, p));
  return { kind: 'quantile', units: sorted.length, max, breaks: [at(0.25), at(0.5), at(0.75)] };
}

/** The class a count paints under `scale`; null (the base step) when it has none. */
export function classify(scale: ChoroplethScale | null, count: number | null | undefined): MapClass | null {
  if (!scale || typeof count !== 'number' || !Number.isFinite(count) || count <= 0) return null;
  const [b1, b2, b3] = scale.breaks;
  if (count >= b3) return 4;
  if (count >= b2) return 3;
  if (count >= b1) return 2;
  return 1;
}

/** Legend tick text for a break: `1.3K`, `620K`, `1.9M`, `53` (lib/formatters compact count). */
export function formatBreak(value: number): string {
  return formatCompact(value);
}

/** Inclusive integer range of each class, for the screen-reader legend and the table. */
export function classRanges(scale: ChoroplethScale): Array<{ cls: MapClass; from: number; to: number | null }> {
  const [b1, b2, b3] = scale.breaks;
  const ranges: Array<{ cls: MapClass; from: number; to: number | null }> = [
    { cls: 1, from: 1, to: b1 - 1 },
    { cls: 2, from: b1, to: b2 - 1 },
    { cls: 3, from: b2, to: b3 - 1 },
    { cls: 4, from: b3, to: null },
  ];
  // A tie collapses a class to nothing (from > to); it is not listed.
  return ranges.filter((range) => range.to === null || range.from <= range.to);
}
