/**
 * Pure geometry for "Crossed the line" (audit wow-stage-4).
 *
 * Inputs: the weekly MORTGAGE30US series (GET /analytics/rate-window, percent
 * form), the borrower's fixed note rate (Borrower360.current_rate, percent),
 * the refresh's spread screen (why_panel.min_spread_bps) and the SERVER's
 * crossing week (Borrower360.first_itm_week). The client never decides
 * in-the-money for any week: the crossing marker sits at first_itm_week,
 * which gold computed with fn_in_the_money(fn_rate_spread(...)) and its
 * BROUND. The dashed screen line (note - min_spread / 100) is display only.
 *
 * Coordinates are 0..100 on both axes (y grows downward, SVG style); the
 * component scales them into its viewBox.
 */
import { fixedAttr } from '../../lib/fixedPrecision';
import type { RateWindowWeek } from '../../types';

export interface SpreadHistoryInput {
  weeks: readonly RateWindowWeek[];
  /** Borrower360.current_rate, percent (7.25 == 7.25%). */
  noteRatePct: number;
  /** why_panel.min_spread_bps: the refresh's spread screen. */
  minSpreadBps: number;
  /** Borrower360.first_itm_week (a Monday, YYYY-MM-DD) or null. */
  firstItmWeek: string | null | undefined;
  /** Borrower360.first_pos_date (YYYY-MM-DD) or null: the domain's lower bound. */
  firstPosDate: string | null | undefined;
}

export interface SpreadHistoryPoint {
  week: string;
  marketPct: number;
  x: number;
  marketY: number;
  /** The spread area's lower edge: the market rate clamped at the note rate. */
  spreadY: number;
}

export interface SpreadHistoryCrossing {
  week: string;
  x: number;
  /** The run reaches the series' first week: "since at least". */
  leftCensored: boolean;
}

export interface SpreadHistoryRow {
  week: string;
  marketPct: number;
  crossing: boolean;
}

export interface SpreadHistoryModel {
  points: SpreadHistoryPoint[];
  noteRatePct: number;
  noteY: number;
  screenPct: number;
  screenY: number;
  crossing: SpreadHistoryCrossing | null;
  seriesStart: string;
  domainStart: string;
  domainEnd: string;
  latestMarketPct: number;
  rows: SpreadHistoryRow[];
}

const DAY_MS = 86_400_000;

function dayNumber(isoDate: string): number {
  const [year, month, day] = isoDate.slice(0, 10).split('-').map(Number);
  return Date.UTC(year, month - 1, day) / DAY_MS;
}

/** The week-starting Monday of an ISO date (Spark DATE_TRUNC('WEEK', d)). */
export function mondayOf(isoDate: string): string {
  const day = dayNumber(isoDate);
  if (!Number.isFinite(day)) return isoDate;
  // 1970-01-01 was a Thursday: weekday index with Monday = 0.
  const weekday = (((day + 3) % 7) + 7) % 7;
  return new Date((day - weekday) * DAY_MS).toISOString().slice(0, 10);
}

function validWeeks(weeks: readonly RateWindowWeek[]): RateWindowWeek[] {
  return weeks
    .filter((week) => /^\d{4}-\d{2}-\d{2}$/.test(week.week) && Number.isFinite(week.market_rate_pct))
    .slice()
    .sort((a, b) => (a.week < b.week ? -1 : a.week > b.week ? 1 : 0));
}

export function buildSpreadHistoryModel(input: SpreadHistoryInput): SpreadHistoryModel | null {
  const series = validWeeks(input.weeks);
  if (series.length === 0 || !Number.isFinite(input.noteRatePct) || input.noteRatePct <= 0) return null;
  const seriesStart = series[0].week;
  const originWeek = input.firstPosDate ? mondayOf(input.firstPosDate) : null;
  const domainStartWanted = originWeek && originWeek > seriesStart ? originWeek : seriesStart;
  const clipped = series.filter((week) => week.week >= domainStartWanted);
  // A lien newer than the latest print still draws the latest week.
  const shown = clipped.length > 0 ? clipped : series.slice(-1);
  const domainStart = shown[0].week;
  const domainEnd = shown[shown.length - 1].week;

  const note = input.noteRatePct;
  const screen = note - (Number.isFinite(input.minSpreadBps) ? input.minSpreadBps : 0) / 100;
  const markets = shown.map((week) => week.market_rate_pct);
  const lowRaw = Math.min(screen, ...markets);
  const highRaw = Math.max(note, ...markets);
  const pad = Math.max(0.1, (highRaw - lowRaw) * 0.08);
  const low = lowRaw - pad;
  const high = highRaw + pad;
  const y = (pct: number) => ((high - pct) / (high - low)) * 100;

  const d0 = dayNumber(domainStart);
  const d1 = dayNumber(domainEnd);
  const span = d1 - d0;
  const x = (isoDate: string) => (span > 0 ? Math.min(100, Math.max(0, ((dayNumber(isoDate) - d0) / span) * 100)) : 100);

  const points = shown.map((week) => ({
    week: week.week,
    marketPct: week.market_rate_pct,
    x: x(week.week),
    marketY: y(week.market_rate_pct),
    spreadY: y(Math.min(week.market_rate_pct, note)),
  }));

  const first = input.firstItmWeek ?? null;
  const crossing: SpreadHistoryCrossing | null = first
    ? { week: first, x: x(first), leftCensored: first === seriesStart }
    : null;
  // The table marks the first shown week on or after the server's week.
  const crossingRowWeek = first ? shown.find((week) => week.week >= first)?.week ?? null : null;

  return {
    points,
    noteRatePct: note,
    noteY: y(note),
    screenPct: screen,
    screenY: y(screen),
    crossing,
    seriesStart,
    domainStart,
    domainEnd,
    latestMarketPct: shown[shown.length - 1].market_rate_pct,
    rows: shown.map((week) => ({
      week: week.week,
      marketPct: week.market_rate_pct,
      crossing: week.week === crossingRowWeek,
    })),
  };
}

/** The shaded spread: the note line across the top, the clamped market line back along the bottom. */
export function spreadPolygon(model: SpreadHistoryModel, scaleX = 1, scaleY = 1): string {
  if (model.points.length === 0) return '';
  const first = model.points[0];
  const last = model.points[model.points.length - 1];
  const at = (px: number, py: number) => `${fixedAttr(px * scaleX)},${fixedAttr(py * scaleY)}`;
  const top = [at(first.x, model.noteY), at(last.x, model.noteY)];
  const bottom = [...model.points].reverse().map((point) => at(point.x, point.spreadY));
  return [...top, ...bottom].join(' ');
}

/** The market line as a path (a <path> so pathLength drives the draw-in). */
export function marketPath(model: SpreadHistoryModel, scaleX = 1, scaleY = 1): string {
  return model.points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${fixedAttr(point.x * scaleX)} ${fixedAttr(point.marketY * scaleY)}`)
    .join(' ');
}
