/**
 * The Sales ops period (2026-09-21 audit critic-10, item 6a): which window
 * the conversion and outcome cards read, held in the URL so a manager can
 * share it. Pure and static: no React, no I/O.
 *
 *   ?sales_period=yesterday | wtd | 7d | 30d | mtd | custom
 *   &sales_from=YYYY-MM-DD&sales_to=YYYY-MM-DD   (custom only)
 *
 * No parameter is the default, week to date. An unknown preset, a malformed
 * or impossible date, a custom range that ends in the future, runs backwards
 * or spans more than 92 days (the server's own cap on the window reads) all
 * fall back to week to date, and `normalizedParams` returns the URL without
 * the bad keys so the caller can replace it. Dates are LOCAL calendar days.
 */
import { formatDate } from '../lib/time';

export type SalesPeriodPreset = 'yesterday' | 'wtd' | '7d' | '30d' | 'mtd' | 'custom';

export const SALES_PERIOD_PARAM = 'sales_period';
export const SALES_FROM_PARAM = 'sales_from';
export const SALES_TO_PARAM = 'sales_to';
export const MAX_PERIOD_DAYS = 92;

export const SALES_PERIOD_PRESETS: ReadonlyArray<{ value: SalesPeriodPreset; label: string }> = [
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'wtd', label: 'Week to date' },
  { value: '7d', label: '7 days' },
  { value: '30d', label: '30 days' },
  { value: 'mtd', label: 'Month to date' },
  { value: 'custom', label: 'Custom' },
];

export interface SalesPeriod {
  preset: SalesPeriodPreset;
  /** Inclusive local calendar days, YYYY-MM-DD. */
  from: string;
  to: string;
  days: number;
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;

export function isoDay(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date: Date, days: number): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

/** A real local calendar day, or null (2026-02-30 is not one). */
export function parseIsoDay(value: string | null): Date | null {
  if (!value || !ISO_DAY.test(value)) return null;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return isoDay(date) === value ? date : null;
}

function spanDays(from: Date, to: Date): number {
  return Math.round((startOfDay(to).getTime() - startOfDay(from).getTime()) / DAY_MS) + 1;
}

function periodWindow(preset: SalesPeriodPreset, from: Date, to: Date): SalesPeriod {
  return { preset, from: isoDay(from), to: isoDay(to), days: spanDays(from, to) };
}

function presetWindow(preset: Exclude<SalesPeriodPreset, 'custom'>, today: Date): SalesPeriod {
  const day = startOfDay(today);
  switch (preset) {
    case 'yesterday': {
      const yesterday = addDays(day, -1);
      return periodWindow(preset, yesterday, yesterday);
    }
    case '7d':
      return periodWindow(preset, addDays(day, -6), day);
    case '30d':
      return periodWindow(preset, addDays(day, -29), day);
    case 'mtd':
      return periodWindow(preset, new Date(day.getFullYear(), day.getMonth(), 1), day);
    case 'wtd':
    default: {
      const weekday = day.getDay();
      return periodWindow('wtd', addDays(day, weekday === 0 ? -6 : 1 - weekday), day);
    }
  }
}

function isPreset(value: string | null): value is SalesPeriodPreset {
  return SALES_PERIOD_PRESETS.some((preset) => preset.value === value);
}

/** Why a custom range cannot be read (the range fields' inline error), in check order. */
export type CustomRangeIssue = 'from_incomplete' | 'to_incomplete' | 'from_future' | 'to_future' | 'reversed' | 'too_long';

function rangeIssue(from: Date | null, to: Date | null, today: Date): CustomRangeIssue | null {
  const last = startOfDay(today);
  if (!from) return 'from_incomplete';
  if (!to) return 'to_incomplete';
  if (from > last) return 'from_future';
  if (to > last) return 'to_future';
  if (to < from) return 'reversed';
  return spanDays(from, to) > MAX_PERIOD_DAYS ? 'too_long' : null;
}

/**
 * Null when `from`..`to` is a real, past-or-today range of at most 92 days.
 * One rule for the URL and for a draft in the range fields: a partly typed
 * year (0002-07-01) is not a real day here, so it reads as incomplete.
 */
export function customRangeIssue(from: string, to: string, today: Date = new Date()): CustomRangeIssue | null {
  return rangeIssue(parseIsoDay(from), parseIsoDay(to), today);
}

/** The custom window, when it is a real, past-or-today range of at most 92 days. */
function customWindow(params: URLSearchParams, today: Date): SalesPeriod | null {
  const from = parseIsoDay(params.get(SALES_FROM_PARAM));
  const to = parseIsoDay(params.get(SALES_TO_PARAM));
  if (!from || !to || rangeIssue(from, to, today)) return null;
  return periodWindow('custom', from, to);
}

/** The period the URL names; anything invalid reads as week to date. */
export function resolveSalesPeriod(params: URLSearchParams, today: Date = new Date()): SalesPeriod {
  const raw = params.get(SALES_PERIOD_PARAM);
  if (raw === 'custom') return customWindow(params, today) ?? presetWindow('wtd', today);
  if (isPreset(raw) && raw !== 'custom') return presetWindow(raw, today);
  return presetWindow('wtd', today);
}

/**
 * The URL with every invalid or stray period key removed, or null when it is
 * already clean (callers replace, never push, with it).
 */
export function normalizedParams(params: URLSearchParams, today: Date = new Date()): URLSearchParams | null {
  const raw = params.get(SALES_PERIOD_PARAM);
  const next = new URLSearchParams(params);
  const valid = raw === null || (isPreset(raw) && (raw !== 'custom' || customWindow(params, today) !== null));
  if (!valid) next.delete(SALES_PERIOD_PARAM);
  if (!valid || raw !== 'custom') {
    next.delete(SALES_FROM_PARAM);
    next.delete(SALES_TO_PARAM);
  }
  return next.toString() === params.toString() ? null : next;
}

/** The URL for a chosen period (custom keeps the range it is given). */
export function paramsForPeriod(
  params: URLSearchParams,
  preset: SalesPeriodPreset,
  range?: { from: string; to: string },
): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete(SALES_FROM_PARAM);
  next.delete(SALES_TO_PARAM);
  if (preset === 'wtd') next.delete(SALES_PERIOD_PARAM);
  else next.set(SALES_PERIOD_PARAM, preset);
  if (preset === 'custom' && range) {
    next.set(SALES_FROM_PARAM, range.from);
    next.set(SALES_TO_PARAM, range.to);
  }
  return next;
}

/** The same number of days immediately before `period.from` (W5e's comparison). */
export function previousWindow(period: SalesPeriod): { from: string; to: string } {
  const from = parseIsoDay(period.from);
  if (!from) return { from: period.from, to: period.to };
  const to = addDays(from, -1);
  return { from: isoDay(addDays(to, -(period.days - 1))), to: isoDay(to) };
}

/** The conversion card's eyebrow; a custom range reads in the app's short date form ("Jul 1"). */
export function periodEyebrow(period: SalesPeriod, today: Date = new Date()): string {
  switch (period.preset) {
    case 'yesterday':
      return "Yesterday's conversion";
    case 'wtd':
      return 'Week-to-date conversion';
    case '7d':
      return 'Last 7 days conversion';
    case '30d':
      return 'Last 30 days conversion';
    case 'mtd':
      return 'Month-to-date conversion';
    default:
      return `Conversion ${formatDate(period.from, { now: today })} to ${formatDate(period.to, { now: today })}`;
  }
}
