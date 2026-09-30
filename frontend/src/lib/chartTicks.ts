/**
 * The chart foundation's axis maths (2026-09-21 audit stack-06, visual-08,
 * dataviz-07 step 1): 1-2-5 "nice" ticks, the value-axis domain they imply,
 * and ticks inside a server-pinned domain. Zero dependencies, no React.
 *
 * Every tick is a whole multiple of a 1, 2 or 5 x 10^k step, so a formatted
 * label always equals its tick value (lib/formatters prints at most one
 * compact fraction digit, and a 1-2-5 multiple never needs a second).
 */
import { roundTo } from './fixedPrecision';

export interface NiceTickOptions {
  /**
   * Count axes: the step is at least 1, so a small range never prints the
   * same rounded label twice ("0, 0.5, 1" read "0, 1, 1").
   */
  integer?: boolean;
}

/** 1-2-5 "nice" ticks covering [min, max] with the bounds rounded outward. */
export function niceTicks(min: number, max: number, count = 5, options: NiceTickOptions = {}): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  let lo = Math.min(min, max);
  let hi = Math.max(min, max);
  if (hi === lo) {
    if (lo === 0) return [0, 1];
    lo = lo > 0 ? 0 : lo * 2;
    hi = hi > 0 ? hi * 2 : 0;
  }
  const rawStep = (hi - lo) / Math.max(1, count - 1);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const residual = rawStep / magnitude;
  const factor = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
  const step = options.integer ? Math.max(1, factor * magnitude) : factor * magnitude;
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  // The epsilon keeps an exact multiple (6.3 / 0.05) from rounding a whole
  // extra step outward when the division lands a few ulps off the integer.
  const start = Math.floor(lo / step + 1e-9) * step;
  const end = Math.ceil(hi / step - 1e-9) * step;
  const steps = Math.round((end - start) / step);
  return Array.from({ length: steps + 1 }, (_, idx) => roundTo(start + idx * step, decimals));
}

/**
 * The [first, last] nice tick: a value axis plots over exactly this domain,
 * so its top gridline is the domain maximum and no mark sits above the axis.
 */
export function niceDomain(min: number, max: number, count = 5, options: NiceTickOptions = {}): [number, number] {
  const ticks = niceTicks(min, max, count, options);
  if (ticks.length === 0) return [0, 1];
  return [ticks[0], ticks[ticks.length - 1]];
}

/**
 * Nice ticks inside a domain the server pins (the scatter's plot range, a
 * histogram's bins): the domain is NEVER widened, so bins and points keep
 * their server positions; ticks outside it are dropped. A coarse step can
 * leave too few ticks in the window, so a finer count is tried until at
 * least max(3, count - 1) land inside it. A degenerate window keeps its bounds.
 */
export function niceTicksWithin(min: number, max: number, count = 5, options: NiceTickOptions = {}): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  const lo = Math.min(min, max);
  const hi = Math.max(min, max);
  const target = Math.max(3, count - 1);
  let best: number[] = [];
  for (let attempt = count; attempt <= count + 4; attempt += 1) {
    const inside = niceTicks(lo, hi, attempt, options).filter((tick) => tick >= lo - 1e-9 && tick <= hi + 1e-9);
    if (inside.length >= target) return inside;
    if (inside.length > best.length) best = inside;
  }
  if (best.length >= 2) return best;
  return lo === hi ? [lo] : [lo, hi];
}
