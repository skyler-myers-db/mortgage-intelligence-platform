import { useSyncExternalStore } from 'react';
import {
  DATE_UNKNOWN,
  formatDate,
  formatDateTimeShort,
  formatRelative,
  formatUtcTitle,
  isoDateTimeAttr,
  type RelativeStyle,
} from '../../lib/time';

/**
 * Timestamp — the one way a screen shows a backend date or time
 * (2026-09-21 audit, responsive-07).
 *
 * Renders `<time dateTime={ISO} title={absolute UTC}>` around the lib/time
 * label, so every freshness chip, trigger row and dense cell carries a
 * machine-readable instant AND an unambiguous absolute tooltip, whatever the
 * visible form:
 *   format="relative" (default)  "3 hours ago" · narrow "2d ago"
 *   format="date"                "Jul 14"  (year past 11 months)
 *   format="datetime"            "Jul 14, 8:00 AM EDT"
 * A missing or unparseable value renders the fallback as plain text, never a
 * `<time>` claiming an instant it does not have.
 *
 * Relative labels stay true while a page sits open: every mounted Timestamp
 * shares one minute clock (a single interval, started by the first one to
 * mount and stopped by the last to unmount).
 */

export type TimestampFormat = 'relative' | 'date' | 'datetime';

interface TimestampProps {
  value: string | number | Date | null | undefined;
  format?: TimestampFormat;
  /** Relative form only; `'narrow'` is the prototype's `.trig__when` shape. */
  relativeStyle?: RelativeStyle;
  className?: string;
  /** Plain text rendered when the value is missing or unparseable. */
  fallback?: string;
  /** Reference instant (tests); default the shared minute clock. */
  now?: Date | number;
}

const MINUTE_MS = 60_000;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

/** The real clock at minute grain: a relative label never needs more. */
function minuteNow(): number {
  return Math.floor(Date.now() / MINUTE_MS) * MINUTE_MS;
}

let clockMs = minuteNow();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === null) {
    clockMs = minuteNow();
    timer = setInterval(() => {
      clockMs = minuteNow();
      listeners.forEach((notify) => notify());
    }, MINUTE_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
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

export function Timestamp({
  value,
  format = 'relative',
  relativeStyle = 'long',
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
        : formatRelative(value, { now: reference, style: relativeStyle });
  return (
    <time className={className} dateTime={dateTime} title={formatUtcTitle(value)}>
      {label}
    </time>
  );
}
