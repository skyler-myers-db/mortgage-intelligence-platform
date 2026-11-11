import { useSyncExternalStore } from 'react';
import { Tooltip } from './Tooltip';
import {
  DATE_UNKNOWN,
  calendarDate,
  formatDate,
  formatDateTimeShort,
  formatRelative,
  formatUtcTitle,
  isoDateTimeAttr,
  parseBackendTimestamp,
  type RelativeStyle,
} from '../../lib/time';

/**
 * Timestamp — the one way a screen shows a backend date or time
 * (2026-09-21 audit, responsive-07).
 *
 * Renders `<time dateTime={ISO}>` around the lib/time label, wrapped in a
 * ui/Tooltip whose content is the absolute UTC form (a native `title` is
 * banned, audit critic-08), so every freshness chip, trigger row and dense
 * cell carries a machine-readable instant AND an unambiguous absolute
 * tooltip (on hover, and as the hidden description), whatever the visible
 * form:
 *   format="relative" (default)  "3 hours ago" · narrow "2d ago"
 *   format="date"                "Jul 14"  (year past 11 months)
 *   format="datetime"            "Jul 14, 8:00 AM EDT"  (a calendar date: "Jul 14")
 * A missing or unparseable value renders the fallback as plain text, never a
 * `<time>` claiming an instant it does not have.
 *
 * Relative labels stay true while a page sits open: every mounted Timestamp
 * shares one minute clock (a single timer, started by the first one to
 * mount and stopped by the last to unmount). The clock is the minute it is
 * in, so it ticks on the wall clock's minute boundaries, and a value inside
 * that minute reads "now" (PR #338 review: a 60 s interval from the first
 * mount trailed the real time by up to 2 minutes, and a fresh read showed
 * "Fetched in 1m" or "in 2m").
 */

export type TimestampFormat = 'relative' | 'date' | 'datetime';

interface TimestampProps {
  value: string | number | Date | null | undefined;
  format?: TimestampFormat;
  /** Relative form only; `'narrow'` is the prototype's `.trig__when` shape. */
  relativeStyle?: RelativeStyle;
  /** Relative form only: the label under 45 seconds in place of "now" (lib/time formatRelative). */
  nowLabel?: string;
  className?: string;
  /** Plain text rendered when the value is missing or unparseable. */
  fallback?: string;
  /**
   * Reference instant (tests), used as given; default the shared minute
   * clock, where a value inside the clock's minute reads "now".
   */
  now?: Date | number;
}

const MINUTE_MS = 60_000;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

/** The real clock at minute grain: a relative label never needs more. */
function minuteNow(): number {
  return Math.floor(Date.now() / MINUTE_MS) * MINUTE_MS;
}

let clockMs = minuteNow();

/**
 * Tick at the next wall-clock minute, then at every one after it. A timer
 * that fires early leaves the clock where it is and re-arms for the boundary.
 * It re-arms before notifying, so the last listener to unsubscribe clears the
 * live timer.
 */
function scheduleTick(): void {
  timer = setTimeout(() => {
    clockMs = minuteNow();
    scheduleTick();
    listeners.forEach((notify) => notify());
  }, MINUTE_MS - (Date.now() % MINUTE_MS));
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    clockMs = minuteNow();
    scheduleTick();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };
}

function getSnapshot(): number {
  // Before anything subscribes, read the real clock, so a first render
  // never uses an hours-old instant; minute grain keeps repeated reads
  // within one render equal.
  if (timer === null) clockMs = minuteNow();
  return clockMs;
}

/** The shared minute clock (epoch ms). */
export function useMinuteClock(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/**
 * The reference for a relative label read against the minute clock. The
 * clock is the floor of the minute the real time is in, so a value inside
 * that minute is as recent as minute grain can tell, and reads "now", never
 * "in 1m". A value in a later minute keeps the clock and stays ahead.
 */
function minuteGrainReference(value: TimestampProps['value'], clock: number): number {
  if (calendarDate(value)) return clock;
  const at = parseBackendTimestamp(value)?.getTime();
  return at !== undefined && at > clock && at < clock + MINUTE_MS ? at : clock;
}

export function Timestamp({
  value,
  format = 'relative',
  relativeStyle = 'long',
  nowLabel,
  className,
  fallback = DATE_UNKNOWN,
  now,
}: TimestampProps) {
  const clock = useMinuteClock();
  const dateTime = isoDateTimeAttr(value);
  if (dateTime === null) return <span className={className}>{fallback}</span>;
  const reference = now ?? clock;
  const label =
    format === 'date'
      ? formatDate(value, { now: reference })
      : format === 'datetime'
        ? formatDateTimeShort(value, { now: reference })
        : formatRelative(value, { now: now ?? minuteGrainReference(value, clock), style: relativeStyle, nowLabel });
  return (
    <Tooltip content={formatUtcTitle(value)}>
      <time className={className} dateTime={dateTime}>
        {label}
      </time>
    </Tooltip>
  );
}
