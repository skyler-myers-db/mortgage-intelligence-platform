/**
 * Pure models behind the Analytics charts (2026-09-21 audit dataviz-06,
 * dataviz-10): the governed bucket widths, the histogram model and the
 * one-sentence summaries. No React.
 *
 * Every summary is built from governed values only: the bins the chart
 * plots, the executive totals, and the thresholds block the economics
 * payload carries. A count the bins cannot prove exactly is never printed.
 */
import { binIsPast, type HistogramBin } from '../components/charts/Histogram';
import { formatCount, formatPercent } from '../lib/formatters';
import { HIGH_OPPORTUNITY_SCORE_LABEL } from '../lib/opportunityScore';
import { formatDate } from '../lib/time';
import type { AnalyticsThresholds } from '../types/economicsScatter';

/**
 * The governed bucket widths: CAST(FLOOR(opportunity_score / 5) * 5 AS INT)
 * and CAST(FLOOR(rate_spread_bps / 25) * 25 AS INT) in
 * backend/services/repositories/databricks_analytics.py, and the spread
 * histogram's `rate_spread_bps BETWEEN -100 AND 400` window.
 * tests/unit/test_analytics_thresholds.py pins the parity.
 */
export const SCORE_BUCKET_WIDTH = 5;
export const SPREAD_BUCKET_BPS = 25;
export const SPREAD_HISTOGRAM_MIN_BPS = -100;
export const SPREAD_HISTOGRAM_MAX_BPS = 400;

export interface HistogramModelBin extends HistogramBin {
  /** Exclusive upper edge, start + width. */
  end: number;
  /** Lower edge at or above the threshold: a bin straddling the threshold is not past. */
  past: boolean;
  /** The threshold falls strictly inside the bin, so part of it is past. */
  straddles: boolean;
}

export interface HistogramModel {
  bins: HistogramModelBin[];
  /** Borrowers in the past bins; null without a threshold. Exact only when the threshold sits on a bin edge. */
  past: number | null;
  /** The most common bin, the first on a tie; null when there are no bins. */
  modal: HistogramModelBin | null;
  total: number;
}

export function histogramModel(rows: ReadonlyArray<HistogramBin>, width: number, threshold: number | null): HistogramModel {
  const bins = [...rows]
    .filter((row) => Number.isFinite(row.start) && Number.isFinite(row.count))
    .sort((a, b) => a.start - b.start)
    .map((row) => ({
      start: row.start,
      end: row.start + width,
      count: Math.max(0, row.count),
      past: binIsPast(row.start, threshold),
      straddles: threshold !== null && row.start < threshold && threshold < row.start + width,
    }));
  let modal: HistogramModelBin | null = null;
  for (const bin of bins) if (modal === null || bin.count > modal.count) modal = bin;
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  const past = threshold === null ? null : bins.filter((bin) => bin.past).reduce((sum, bin) => sum + bin.count, 0);
  return { bins, past, modal, total };
}

/** "75–79", "100–124 bps", "-100 to -76 bps": a bin's integer range. */
export function formatBinRange(start: number, width: number, unit = ''): string {
  const lo = formatCount(start);
  const hi = formatCount(start + width - 1);
  const range = start < 0 ? `${lo} to ${hi}` : `${lo}–${hi}`;
  return unit ? `${range} ${unit}` : range;
}

/** The share a part is of a whole, "8.0%"; "0%" for an empty whole. */
function share(part: number, whole: number): string {
  return whole > 0 ? formatPercent(part / whole, 1) : '0%';
}

/**
 * Score distribution: the addressable count and the high-opportunity count
 * are the executive totals over the same filtered mip.gold.borrower_360
 * population the bins count, so that figure is governed, never re-summed bins.
 */
export function scoreSummary(model: HistogramModel, totals: { addressable_borrowers: number; high_opportunity_borrowers: number }): string {
  const scored = `${formatCount(totals.addressable_borrowers)} borrowers scored`;
  const modal = model.modal
    ? `; the most common band is ${formatBinRange(model.modal.start, SCORE_BUCKET_WIDTH)} (${formatCount(model.modal.count)})`
    : '';
  const high = totals.high_opportunity_borrowers;
  return `${scored}${modal}. ${formatCount(high)} (${share(high, totals.addressable_borrowers)}) score ${HIGH_OPPORTUNITY_SCORE_LABEL}.`;
}

/** The notice for a spread screen the refresh does not carry, or null when it does. */
export function thresholdNotice(thresholds: Pick<AnalyticsThresholds, 'min_spread_bps' | 'reason'>): string | null {
  if (thresholds.min_spread_bps !== null) return null;
  return thresholds.reason === 'not_uniform'
    ? 'No single refi-screen threshold applies to this refresh.'
    : 'The refi-screen threshold is not built in this refresh.';
}

/**
 * Spread distribution. The at-or-past count is printed only when the screen
 * sits on a 25 bps bin edge, where summing the bins is exact; otherwise a
 * bin straddles it and no count is claimed.
 */
export function spreadSummary(model: HistogramModel, thresholds: Pick<AnalyticsThresholds, 'min_spread_bps' | 'reason'>): string {
  const range = `${formatCount(SPREAD_HISTOGRAM_MIN_BPS)} and ${formatCount(SPREAD_HISTOGRAM_MAX_BPS)} bps`;
  const modal = model.modal ? `; most common ${formatBinRange(model.modal.start, SPREAD_BUCKET_BPS, 'bps')}` : '';
  const base = `${formatCount(model.total)} borrowers plotted between ${range}${modal}.`;
  const t = thresholds.min_spread_bps;
  if (t === null) return `${base} ${thresholdNotice(thresholds)}`;
  if (t % SPREAD_BUCKET_BPS !== 0 || model.past === null) return base;
  return `${base} ${formatCount(model.past)} (${share(model.past, model.total)}) sit at or past the ${formatCount(t)} bps refi screen.`;
}

/** A full calendar date, "May 2, 2026": the summary and the table name the year the short axis labels drop. */
export const evidenceDate = (date: string): string => formatDate(date, { withYear: true });

/**
 * Evidence per day: the total, the span and the busiest day (the first on a
 * tie), all from the rows the chart plots. Null for no rows.
 */
export function evidenceSummary(rows: ReadonlyArray<{ event_date: string; event_count: number }>): string | null {
  if (rows.length === 0) return null;
  let total = 0;
  let peak = rows[0];
  for (const row of rows) {
    total += row.event_count;
    if (row.event_count > peak.event_count) peak = row;
  }
  const first = evidenceDate(rows[0].event_date);
  if (rows.length === 1) return `${formatCount(total)} evidence events on ${first}.`;
  const last = evidenceDate(rows[rows.length - 1].event_date);
  return `${formatCount(total)} evidence events from ${first} to ${last}; the busiest day was ${evidenceDate(peak.event_date)} with ${formatCount(peak.event_count)}.`;
}
