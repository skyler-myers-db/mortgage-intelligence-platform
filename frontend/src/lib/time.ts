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
 * Every surface that shows a clock time must go through this module —
 * never `new Date(raw)` + `toLocale*` directly on backend strings.
 */

const NAIVE_SQL_TIMESTAMP_RE =
  /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;

export function parseBackendTimestamp(
  value: string | number | Date | null | undefined,
): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === 'number') {
    const fromEpoch = new Date(value);
    return Number.isNaN(fromEpoch.getTime()) ? null : fromEpoch;
  }
  const raw = value.trim();
  if (!raw) return null;
  const iso = NAIVE_SQL_TIMESTAMP_RE.test(raw)
    ? `${raw.replace(' ', 'T')}Z`
    : raw;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export interface FormatTimestampOptions {
  /** Explicit zone (tests pass 'UTC' for determinism); default = viewer's. */
  timeZone?: string;
  /** Locale override (tests pass 'en-US'); default = viewer's. */
  locale?: string;
  withSeconds?: boolean;
  withYear?: boolean;
}

export const TIMESTAMP_UNAVAILABLE = 'timestamp unavailable';

/** "Jun 11, 2026, 3:32 AM UTC" — date + time + explicit short zone name. */
export function formatTimestamp(
  value: string | number | Date | null | undefined,
  opts: FormatTimestampOptions = {},
): string {
  const parsed = parseBackendTimestamp(value);
  if (!parsed) return TIMESTAMP_UNAVAILABLE;
  const { timeZone, locale, withSeconds = false, withYear = true } = opts;
  return new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    ...(withYear ? { year: 'numeric' } : {}),
    hour: 'numeric',
    minute: '2-digit',
    ...(withSeconds ? { second: '2-digit' } : {}),
    timeZoneName: 'short',
    ...(timeZone ? { timeZone } : {}),
  }).format(parsed);
}

/** "14:05:31 UTC" — dense 24h clock for audit rows; zone always attached. */
export function formatTimeOfDay(
  value: string | number | Date | null | undefined,
  opts: Pick<FormatTimestampOptions, 'timeZone' | 'locale'> = {},
): string {
  const parsed = parseBackendTimestamp(value);
  if (!parsed) return TIMESTAMP_UNAVAILABLE;
  const { timeZone, locale } = opts;
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'short',
    ...(timeZone ? { timeZone } : {}),
  }).format(parsed);
}
