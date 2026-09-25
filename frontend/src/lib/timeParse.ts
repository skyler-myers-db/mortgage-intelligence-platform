/**
 * Backend timestamp PARSING (2026-06-11 audit fix; split out of lib/time by
 * the 2026-09-21 audit, responsive-07), so a module that only needs an
 * instant (the freshness buckets of every evidence chip) does not carry the
 * formatters. lib/time re-exports both functions; importers are unchanged.
 *
 * The backend emits three timestamp shapes on the wire, ALL meaning UTC:
 *   - naive SQL casts:   "2026-06-11 03:32:18.767056"   (no zone marker)
 *   - ISO-8601 with Z:   "2026-06-11T03:34:30.000Z"
 *   - the drawer-source form with a UTC suffix: "2026-04-20 06:12 UTC"
 *
 * `new Date("2026-06-11 03:32:18")` parses the naive shape as LOCAL time,
 * silently shifting the instant by the viewer's UTC offset, and
 * `new Date("2026-04-20 06:12 UTC")` is not a format any engine has to
 * accept. Both are pinned to UTC here.
 */

const NAIVE_SQL_TIMESTAMP_RE =
  /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;
/** The same shape followed by an explicit " UTC" (the drawer-source registry form). */
const UTC_SUFFIX_RE = /\s*UTC$/i;
/** A calendar date with no clock ("2026-07-14", e.g. a gold snapshot_date). */
const DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export type TimestampInput = string | number | Date | null | undefined;

export function parseBackendTimestamp(
  value: TimestampInput,
): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === 'number') {
    const fromEpoch = new Date(value);
    return Number.isNaN(fromEpoch.getTime()) ? null : fromEpoch;
  }
  const trimmed = value.trim();
  const raw = trimmed.replace(UTC_SUFFIX_RE, '');
  if (!raw) return null;
  const iso = NAIVE_SQL_TIMESTAMP_RE.test(raw)
    ? `${raw.replace(' ', 'T')}Z`
    : raw === trimmed ? raw : null;
  if (iso === null) return null;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** A calendar date string, or null when `value` carries a clock or no date. */
export function calendarDate(value: TimestampInput): Date | null {
  if (typeof value !== 'string') return null;
  const match = DATE_ONLY_RE.exec(value.trim());
  if (!match) return null;
  const parsed = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
