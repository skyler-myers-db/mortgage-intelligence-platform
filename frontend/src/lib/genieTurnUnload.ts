/**
 * Is the page leaving? The beforeunload half of the Genie turn's
 * page-lifecycle rule (runtime-01 wave-3 remainder, W5b; split out of
 * lib/genieInFlightTurn, which keeps the pagehide / pageshow half).
 *
 * WebKit 26.6 (captured with a page-lifecycle probe on genie-pagehide
 * cross-engine (b)) and Firefox (its first CI cross-engine run) cancel the
 * old document's requests when a reload's navigation starts: beforeunload,
 * then the cancelled complete fetch rejects a few ms later, at least one task
 * passes, and only then pagehide (16-60 ms after the rejection on WebKit
 * locally, longer under load). The store's one-task deferral, enough for
 * Chromium, landed that rejection as the turn failing and removed the record.
 *
 * So while a turn completes, a beforeunload is heard and stamped; a failure
 * within GENIE_LEAVE_DECISION_MS of it waits for pagehide (the record then
 * survives for the next page) and lands only if the page is still there
 * after the window (a download, a 204, a navigation that did not happen).
 *
 * The listener exists only while a turn completes: in Firefox a beforeunload
 * listener makes the page ineligible for the back/forward cache. It never
 * calls preventDefault (useUnsavedGuard owns the "Leave site?" prompt).
 */

/** How long after a beforeunload a completing turn's failure waits for pagehide. */
export const GENIE_LEAVE_DECISION_MS = 10_000;

/** performance.now() of the beforeunload heard while completing; null: none. */
let leaveStartedAt: number | null = null;
let watching = false;

function onBeforeUnload(): void {
  leaveStartedAt = performance.now();
}

/** Start hearing beforeunload (a turn entered its complete call or job). */
export function watchLeave(): void {
  if (watching || typeof window === 'undefined' || typeof window.addEventListener !== 'function') return;
  window.addEventListener('beforeunload', onBeforeUnload);
  watching = true;
}

/** Stop, and forget any beforeunload heard (the turn settled, stopped or reset). */
export function unwatchLeave(): void {
  leaveStartedAt = null;
  if (!watching) return;
  window.removeEventListener('beforeunload', onBeforeUnload);
  watching = false;
}

/** Back from the back/forward cache: the page did leave, so decide now. */
export function forgetLeave(): void {
  leaveStartedAt = null;
}

/** How much longer a failure waits for pagehide (0: decide now). */
export function leaveWaitMs(): number {
  if (leaveStartedAt === null) return 0;
  return Math.max(0, GENIE_LEAVE_DECISION_MS - (performance.now() - leaveStartedAt));
}
