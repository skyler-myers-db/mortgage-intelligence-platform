/**
 * Timezone-correct timestamp parsing + formatting (2026-06-11 audit fix).
 *
 * The backend emits two timestamp shapes on the wire, BOTH meaning UTC:
 *   - naive SQL casts:   "2026-06-11 03:32:18.767056"   (no zone marker)
 *   - ISO-8601 with Z:   "2026-06-11T03:34:30.000Z"
 *
 * `new Date("2026-06-11 03:32:18")` parses the naive shape as LOCAL time,
 * silently shifting the instant by the viewer's UTC offset — the bug that
 * rendered a 03:32 UTC gold refresh as "Jun 11 03:32 AM" on a PDT machine.
 * `parseBackendTimestamp` pins naive strings to UTC; the formatters always
 * attach a short timezone name so no rendered time is ever ambiguous.
 *
 * Every surface that shows a date or a clock time must go through this
 * module — never `new Date(raw)` + `toLocale*` directly on backend strings.
 * ESLint enforces it (`no-restricted-syntax` in frontend/eslint.config.js).
 *
 * The date vocabulary (2026-09-21 audit, responsive-07):
 *   formatDate           "Jul 14"                 date only; a calendar date
 *                                                 ("2026-07-14") is never shifted
 *   formatDateTimeShort  "Jul 14, 8:00 AM EDT"    dense cells; zone always attached
 *   formatTimestamp      "Jul 14, 2026, 8:00 AM EDT"
 *   formatRelative       "3 hours ago" / "yesterday" / "5 months ago"
 *   formatUtcTitle       "Jul 14, 2026, 12:00 PM UTC"  the absolute tooltip
 * The short forms include the year once the instant is more than 11 months
 * from now, so a stale snapshot never reads as this year's. `<Timestamp>`
 * (components/ui/Timestamp) renders them inside `<time dateTime title>`.
 *
 * Month names are pinned to en-US like every number (lib/formatters): the
 * product copy is English, so a browser locale must not re-spell one field
 * of a screen. The ZONE stays the viewer's own unless a caller pins one.
 *
 * The parsing half lives in lib/timeParse (responsive-07), re-exported here
 * so importers are unchanged: a module that only needs an instant (the
 * evidence chips' freshness buckets) imports it without the formatters.
 */
import { calendarDate, parseBackendTimestamp, type TimestampInput } from './timeParse';

export { calendarDate, parseBackendTimestamp };

const DEFAULT_LOCALE = 'en-US';
const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;
const AVG_MONTH_MS = (365.2425 / 12) * DAY_MS;
/** Past this distance from now, a short date carries its year. */
const YEAR_VISIBLE_AFTER_MS = 11 * AVG_MONTH_MS;

const FORMATS = new Map<string, Intl.DateTimeFormat>();

/** One cached Intl.DateTimeFormat per (locale, options): never one per render. */
function dateTimeFormat(locale: string, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${JSON.stringify(options)}`;
  let formatter = FORMATS.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(locale, options);
    FORMATS.set(key, formatter);
  }
  return formatter;
}

const RELATIVE_FORMATS = new Map<string, Intl.RelativeTimeFormat>();

function relativeTimeFormat(locale: string, style: RelativeStyle): Intl.RelativeTimeFormat {
  const key = `${locale}|${style}`;
  let formatter = RELATIVE_FORMATS.get(key);
  if (!formatter) {
    formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto', style });
    RELATIVE_FORMATS.set(key, formatter);
  }
  return formatter;
}

function nowMs(now: Date | number | undefined): number {
  if (now === undefined) return Date.now();
  return typeof now === 'number' ? now : now.getTime();
}

/** `'auto'`: the year appears only when the instant is more than 11 months from `now`. */
export type YearDisplay = boolean | 'auto';

function showYear(instant: Date, withYear: YearDisplay, now: Date | number | undefined): boolean {
  if (withYear !== 'auto') return withYear;
  return Math.abs(nowMs(now) - instant.getTime()) > YEAR_VISIBLE_AFTER_MS;
}

export interface FormatTimestampOptions {
  /** Explicit zone (tests pass 'UTC' for determinism); default = viewer's. */
  timeZone?: string;
  /** Locale override; default en-US (see the module note). */
  locale?: string;
  withSeconds?: boolean;
  /** Default true; `'auto'` shows it only past 11 months from `now`. */
  withYear?: YearDisplay;
  /** Reference instant for `withYear: 'auto'`; default the real clock. */
  now?: Date | number;
}

export const TIMESTAMP_UNAVAILABLE = 'timestamp unavailable';
/** What the short forms render for a missing or unparseable value. */
export const DATE_UNKNOWN = '—';

/** "Jun 11, 2026, 3:32 AM UTC" — date + time + explicit short zone name. */
export function formatTimestamp(
  value: TimestampInput,
  opts: FormatTimestampOptions = {},
): string {
  const parsed = parseBackendTimestamp(value);
  if (!parsed) return TIMESTAMP_UNAVAILABLE;
  const { timeZone, locale = DEFAULT_LOCALE, withSeconds = false, withYear = true, now } = opts;
  return dateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    ...(showYear(parsed, withYear, now) ? { year: 'numeric' } : {}),
    hour: 'numeric',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
    timeZoneName: 'short',
    ...(timeZone ? { timeZone } : {}),
  }).format(parsed);
}

/** "14:05:31 UTC" — dense 24h clock for audit rows; zone always attached. */
export function formatTimeOfDay(
  value: TimestampInput,
  opts: Pick<FormatTimestampOptions, 'timeZone' | 'locale'> = {},
): string {
  const parsed = parseBackendTimestamp(value);
  if (!parsed) return TIMESTAMP_UNAVAILABLE;
  const { timeZone, locale = DEFAULT_LOCALE } = opts;
  return dateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'short',
    ...(timeZone ? { timeZone } : {}),
  }).format(parsed);
}

export type ShortDateOptions = Omit<FormatTimestampOptions, 'withSeconds' | 'withYear'> & {
  /** Default `'auto'`. */
  withYear?: YearDisplay;
};

/**
 * "Jul 14" (or "Jul 14, 2025" past 11 months). A calendar date such as a
 * gold `snapshot_date` is formatted in UTC: rendering "2026-07-14" in a
 * viewer zone west of UTC would print the day before.
 */
export function formatDate(value: TimestampInput, opts: ShortDateOptions = {}): string {
  const calendar = calendarDate(value);
  const parsed = calendar ?? parseBackendTimestamp(value);
  if (!parsed) return DATE_UNKNOWN;
  const { locale = DEFAULT_LOCALE, withYear = 'auto', now } = opts;
  const timeZone = calendar ? 'UTC' : opts.timeZone;
  return dateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    ...(showYear(parsed, withYear, now) ? { year: 'numeric' } : {}),
    ...(timeZone ? { timeZone } : {}),
  }).format(parsed);
}

/** "Jan 2021" — a month-axis label; a calendar date is read in UTC like `formatDate`. */
export function formatMonthYear(value: TimestampInput, opts: Pick<FormatTimestampOptions, 'timeZone' | 'locale'> = {}): string {
  const calendar = calendarDate(value);
  const parsed = calendar ?? parseBackendTimestamp(value);
  if (!parsed) return DATE_UNKNOWN;
  const timeZone = calendar ? 'UTC' : opts.timeZone;
  return dateTimeFormat(opts.locale ?? DEFAULT_LOCALE, {
    month: 'short',
    year: 'numeric',
    ...(timeZone ? { timeZone } : {}),
  }).format(parsed);
}

/** "Jul 14, 8:00 AM EDT" — the dense-cell form; the zone is always attached. */
export function formatDateTimeShort(value: TimestampInput, opts: ShortDateOptions = {}): string {
  if (!parseBackendTimestamp(value)) return DATE_UNKNOWN;
  return formatTimestamp(value, { ...opts, withYear: opts.withYear ?? 'auto' });
}

/**
 * The absolute tooltip for a relative or short label: the full date and
 * clock in UTC ("Jul 14, 2026, 12:00 PM UTC"), or the full calendar date
 * for a date-only value. Empty string when the value does not parse.
 */
export function formatUtcTitle(value: TimestampInput, locale: string = DEFAULT_LOCALE): string {
  if (calendarDate(value)) return formatDate(value, { locale, withYear: true });
  if (!parseBackendTimestamp(value)) return '';
  return formatTimestamp(value, { locale, timeZone: 'UTC' });
}

/** The machine-readable `dateTime` attribute for a `<time>` element. */
export function isoDateTimeAttr(value: TimestampInput): string | null {
  if (calendarDate(value)) return (value as string).trim();
  const parsed = parseBackendTimestamp(value);
  return parsed ? parsed.toISOString() : null;
}

export type RelativeStyle = 'long' | 'short' | 'narrow';

export interface RelativeOptions {
  /** Reference instant; default the real clock. */
  now?: Date | number;
  locale?: string;
  /** `'narrow'` reads "2d ago" (the prototype's `.trig__when`); default `'long'`. */
  style?: RelativeStyle;
}

/**
 * Relative age via a cached `Intl.RelativeTimeFormat` (numeric: 'auto'):
 * "now", "12 minutes ago", "3 hours ago", "yesterday", "5 days ago",
 * "last month", "5 months ago". Past 11 months relative age stops being
 * useful ("last year" hides whether it is 12 or 23 months), so it renders
 * the absolute date WITH the year instead: "Jul 14, 2025".
 */
export function formatRelative(value: TimestampInput, opts: RelativeOptions = {}): string {
  const parsed = calendarDate(value) ?? parseBackendTimestamp(value);
  if (!parsed) return DATE_UNKNOWN;
  const { locale = DEFAULT_LOCALE, style = 'long' } = opts;
  const diff = parsed.getTime() - nowMs(opts.now);
  const abs = Math.abs(diff);
  const rtf = relativeTimeFormat(locale, style);
  if (abs < 45_000) return rtf.format(0, 'second');
  if (abs < 45 * MINUTE_MS) return rtf.format(Math.round(diff / MINUTE_MS), 'minute');
  if (abs < 22 * HOUR_MS) return rtf.format(Math.round(diff / HOUR_MS), 'hour');
  if (abs < 26 * DAY_MS) return rtf.format(Math.round(diff / DAY_MS), 'day');
  if (abs <= YEAR_VISIBLE_AFTER_MS) return rtf.format(Math.round(diff / AVG_MONTH_MS), 'month');
  return formatDate(value, { locale, withYear: true });
}
