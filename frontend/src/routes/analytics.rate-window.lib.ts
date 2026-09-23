/**
 * Pure model for the "Why now" rate window (dataviz-08 / dataviz-06): the
 * weekly market rate against the book's note-rate band, the in-the-money
 * count per week, the spread sentence, and the labelled spread-screen line.
 *
 * Everything here is deterministic layout math over the API payload so the
 * SVG component stays a thin renderer and the arithmetic is unit-pinnable
 * (the fixture spec checks the same sentence against the same numbers).
 */
import type { RateWindowResponse } from '../types';
import { categoricalTickIndexes } from './analytics.lib';

export const RATE_WINDOW_TITLE = 'Why now: the market rate against the book';

/**
 * 1-2-5 "nice" ticks covering [min, max] with the bounds rounded outward.
 * Local to this surface on purpose: analytics.lib's makeTicks divides the
 * range evenly (the 6.03K-style ticks of dataviz-07) and a shared chart
 * foundation is a later wave.
 */
export function niceTicks(min: number, max: number, count = 5): number[] {
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
  const step = factor * magnitude;
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  // The epsilon keeps an exact multiple (6.3 / 0.05) from rounding a whole
  // extra step outward when the division lands a few ulps off the integer.
  const start = Math.floor(lo / step + 1e-9) * step;
  const end = Math.ceil(hi / step - 1e-9) * step;
  const steps = Math.round((end - start) / step);
  return Array.from({ length: steps + 1 }, (_, idx) => Number((start + idx * step).toFixed(decimals)));
}

/**
 * Basis points the book's median note rate sits ABOVE the market rate
 * (positive = refi tailwind). Rounds half to even, like the governed
 * fn_rate_spread (BROUND), so a 12.5 bps gap reads 12 here and there. The
 * percent inputs carry binary noise (7.10 - 6.22 = 0.8799999...), so the raw
 * value is snapped to 1e-6 bps before the tie test.
 */
export function spreadBps(medianPct: number, marketPct: number): number {
  const raw = Number(((medianPct - marketPct) * 100).toFixed(6));
  const floor = Math.floor(raw);
  if (raw - floor === 0.5) return floor % 2 === 0 ? floor : floor + 1;
  return Math.round(raw);
}

export function spreadSentence(bps: number): string {
  if (bps === 0) return "The 30-year matches the book's median note rate.";
  const magnitude = Math.abs(bps).toLocaleString('en-US');
  return bps > 0
    ? `The 30-year is ${magnitude} bps below the book's median note rate.`
    : `The 30-year is ${magnitude} bps above the book's median note rate.`;
}

export function formatRatePct(value: number): string {
  return `${value.toFixed(2)}%`;
}

/** "Jan 2021" for a YYYY-MM-DD week label on the shared x-axis. */
export function formatMonthYear(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map((part) => Number(part));
  if (!year || !month || !day) return isoDate;
  return new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(Date.UTC(year, month - 1, day)));
}

export interface RateWindowPoint {
  week: string;
  /** 0..100 percent of the shared x-axis. */
  x: number;
  marketPct: number;
  medianPct: number | null;
  p25Pct: number | null;
  p75Pct: number | null;
  itmCount: number;
  isLatest: boolean;
}

export interface RateWindowThresholdLine {
  minSpreadBps: number;
  /** Market rate at which the median borrower clears the spread screen. */
  ratePct: number;
  label: string;
}

/**
 * Where an x-tick label sits against its position. The chart chrome centres
 * every label on its tick; the shared axis anchors the first and last inside
 * the plot so the edge months neither clip at the surface edge nor spill into
 * the y-tick gutter (dataviz-08 review, 1440x900).
 */
export type RateWindowTickAnchor = 'start' | 'middle' | 'end';

export interface RateWindowXTick {
  x: number;
  label: string;
  anchor: RateWindowTickAnchor;
  /**
   * A narrow plot keeps only the first, the most central and the last label;
   * minor ticks are dropped there so the month labels never overlap.
   */
  minor: boolean;
}

export interface RateWindowModel {
  points: RateWindowPoint[];
  current: RateWindowPoint;
  bookMedianPct: number | null;
  bookP25Pct: number | null;
  bookP75Pct: number | null;
  bookLienCount: number;
  bookAsOf: string | null;
  /** null when the book has no median (empty book). */
  spreadBps: number | null;
  spreadSentence: string;
  itmSentence: string;
  threshold: RateWindowThresholdLine | null;
  rate: { min: number; max: number; ticks: number[] };
  itm: { max: number; ticks: number[] };
  xTicks: RateWindowXTick[];
  /** Accessible description of the chart image: every number the marks draw. */
  ariaLabel: string;
}

/** Vertical position (0..100, top-down) inside the rate panel, with a small inset so edge strokes stay visible. */
export function rateY(model: Pick<RateWindowModel, 'rate'>, value: number): number {
  const span = model.rate.max - model.rate.min;
  const ratio = span <= 0 ? 0.5 : (value - model.rate.min) / span;
  return 96 - Math.max(0, Math.min(1, ratio)) * 92;
}

/** Vertical position inside the in-the-money panel (baseline at the bottom edge). */
export function itmY(model: Pick<RateWindowModel, 'itm'>, value: number): number {
  const ratio = model.itm.max <= 0 ? 0 : value / model.itm.max;
  return 100 - Math.max(0, Math.min(1, ratio)) * 92;
}

/** Shared-axis ticks: edge labels anchored inside the plot, minor ticks marked for narrow plots. */
export function buildXTicks(points: readonly RateWindowPoint[]): RateWindowXTick[] {
  const picked = categoricalTickIndexes(points.length, 6).map((idx) => points[idx]);
  const last = picked.length - 1;
  let central = -1;
  let bestDistance = Number.POSITIVE_INFINITY;
  picked.forEach((point, idx) => {
    if (idx === 0 || idx === last) return;
    const distance = Math.abs(point.x - 50);
    if (distance < bestDistance) {
      bestDistance = distance;
      central = idx;
    }
  });
  const spansAxis = points.length > 1;
  return picked.map((point, idx) => ({
    x: point.x,
    label: formatMonthYear(point.week),
    anchor: spansAxis && idx === 0 ? 'start' : spansAxis && idx === last ? 'end' : 'middle',
    minor: idx !== 0 && idx !== last && idx !== central,
  }));
}

export function buildRateWindowModel(response: RateWindowResponse): RateWindowModel | null {
  const weeks = [...response.weeks].sort((a, b) => a.week.localeCompare(b.week));
  if (weeks.length === 0) return null;
  const n = weeks.length;
  const points: RateWindowPoint[] = weeks.map((row, idx) => ({
    week: row.week,
    x: n === 1 ? 50 : (idx / (n - 1)) * 100,
    marketPct: row.market_rate_pct,
    medianPct: row.book_median_pct ?? null,
    p25Pct: row.book_p25_pct ?? null,
    p75Pct: row.book_p75_pct ?? null,
    itmCount: Math.max(0, row.itm_count),
    isLatest: Boolean(row.is_latest),
  }));
  const current = points.find((p) => p.isLatest) ?? points[n - 1];
  const bookMedianPct = current.medianPct;
  const bookP25Pct = current.p25Pct;
  const bookP75Pct = current.p75Pct;
  const bps = bookMedianPct === null ? null : spreadBps(bookMedianPct, current.marketPct);

  const minSpreadBps = response.thresholds.min_spread_bps ?? null;
  const threshold: RateWindowThresholdLine | null =
    minSpreadBps !== null && bookMedianPct !== null
      ? (() => {
          const ratePct = bookMedianPct - minSpreadBps / 100;
          return {
            minSpreadBps,
            ratePct,
            label: `Spread screen: ${minSpreadBps} bps below the book median (${formatRatePct(ratePct)})`,
          };
        })()
      : null;

  const rateValues = points.flatMap((p) => [p.marketPct, p.p25Pct, p.p75Pct, p.medianPct])
    .filter((v): v is number => v !== null && Number.isFinite(v));
  if (threshold) rateValues.push(threshold.ratePct);
  const rateTicks = niceTicks(Math.min(...rateValues), Math.max(...rateValues), 5);
  const itmMax = Math.max(0, ...points.map((p) => p.itmCount));
  const itmTicks = niceTicks(0, itmMax, 5);

  const bookLienCount = Math.max(0, response.book_lien_count);
  // "In the money" is the full fn_in_the_money rule (spread AND equity); the
  // reference line is only its spread leg, so the two never share a name.
  const itmSentence = `${current.itmCount.toLocaleString('en-US')} of ${bookLienCount.toLocaleString('en-US')} fixed-rate liens in the whole book are in the money at this week's rate (spread and equity screens).`;
  const spreadText = bps === null
    ? 'The fixed-rate book is empty, so there is no median note rate to compare against.'
    : spreadSentence(bps);
  const ariaLabel = [
    `${RATE_WINDOW_TITLE}.`,
    `30-year fixed ${formatRatePct(current.marketPct)} in the week of ${current.week}` +
      (bookMedianPct === null ? '.' : ` against a book median note rate of ${formatRatePct(bookMedianPct)}.`),
    spreadText,
    threshold ? `${threshold.label}.` : null,
    itmSentence,
  ].filter((part): part is string => part !== null).join(' ');

  return {
    points,
    current,
    bookMedianPct,
    bookP25Pct,
    bookP75Pct,
    bookLienCount,
    bookAsOf: response.book_as_of ?? null,
    spreadBps: bps,
    spreadSentence: spreadText,
    itmSentence,
    threshold,
    rate: { min: rateTicks[0], max: rateTicks[rateTicks.length - 1], ticks: rateTicks },
    itm: { max: itmTicks[itmTicks.length - 1], ticks: itmTicks },
    xTicks: buildXTicks(points),
    ariaLabel,
  };
}
