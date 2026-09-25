/**
 * Hand-rolled scales for the chart kit (2026-09-21 audit dataviz-07 step 2).
 * No d3: the kit needs a linear map and a calendar-day map, nothing else.
 * Plot coordinates are percent (0-100) of a `preserveAspectRatio="none"`
 * viewBox, so HTML ticks, tooltips and SVG marks share one space.
 */

export type Scale = (value: number) => number;

/** [d0, d1] onto [r0, r1]. A zero-width domain maps every value to the range midpoint. */
export function linearScale(domain: readonly [number, number], range: readonly [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  if (span === 0 || !Number.isFinite(span)) {
    const mid = (r0 + r1) / 2;
    return () => mid;
  }
  return (value) => r0 + ((value - d0) / span) * (r1 - r0);
}

const DAY_MS = 86_400_000;

/** Whole UTC days since the epoch for a `YYYY-MM-DD` date (a timestamp's date part), or null. */
export function utcDay(iso: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!match) return null;
  const ms = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isFinite(ms) ? Math.round(ms / DAY_MS) : null;
}

/**
 * Percent (0-100) of a calendar date along [firstIso, lastIso] by its UTC-day
 * offset, never by its index in a list, so a missing day keeps its gap
 * (dataviz-v2). One day maps to 50; an unparseable date to NaN.
 */
export function utcDayScale(firstIso: string, lastIso: string): (iso: string) => number {
  const first = utcDay(firstIso);
  const last = utcDay(lastIso);
  if (first === null || last === null) return () => Number.NaN;
  const toPct = linearScale([first, last], [0, 100]);
  return (iso) => {
    const day = utcDay(iso);
    return day === null ? Number.NaN : toPct(day);
  };
}

/**
 * Where an x tick label sits against its tick: a label at the plot's left
 * edge starts there and one at the right edge ends there, so neither spills
 * past the plot (the centred last date overflowed its surface, stack-06).
 */
export function tickEdgeClass(pct: number): string {
  if (pct <= 0.5) return ' analytics-chart__tick--edge-start';
  if (pct >= 99.5) return ' analytics-chart__tick--edge-end';
  return '';
}
