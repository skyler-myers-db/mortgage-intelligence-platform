/**
 * The viewer's local day for the Follow-ups rail item and tray
 * (D-shell-deviations-a). Pure and tiny: the lazy rail item imports only this
 * module, never the list hook, to decide its dot.
 */
import { parseBackendTimestamp, type TimestampInput } from './timeParse';

/** Local midnight that starts the day holding `now` (epoch ms). */
export function startOfLocalDay(now: number): number {
  const day = new Date(now);
  day.setHours(0, 0, 0, 0);
  return day.getTime();
}

/** The next local midnight after `now`: the exclusive end of the viewer's day. */
export function endOfLocalDay(now: number): number {
  const day = new Date(now);
  day.setHours(24, 0, 0, 0);
  return day.getTime();
}

/** Epoch ms of a wire timestamp, or null. */
export function instantOf(value: TimestampInput): number | null {
  const parsed = parseBackendTimestamp(value);
  return parsed === null ? null : parsed.getTime();
}

/**
 * The rail item's dot: something is due today or overdue in the viewer's own
 * time zone. The server's summary already ignores items overdue by more than
 * 14 days and anything snoozed.
 */
export function hasDueToday(nextDueAt: TimestampInput, now: number): boolean {
  const due = instantOf(nextDueAt);
  return due !== null && due < endOfLocalDay(now);
}
