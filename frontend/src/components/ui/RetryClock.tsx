import { useEffect, useState } from 'react';
import { formatTimeOfDay } from '../../lib/time';

/**
 * RetryClock — the wait clocks of the error and warm-up surfaces (audit
 * 2026-09-21 `states-08` part 4): how long a wait has run, and when the next
 * attempt is due.
 *
 * One 1 s interval primitive in ElapsedTicker's pattern (that ticker and
 * Timestamp stay untouched in the initial chunk; this module is lazy, loaded
 * only with the route surfaces that wait):
 *   <WaitClock since={ms} />   "0:42"  (m:ss, so no rendered string reads as a
 *                                       "30 s" / "60 seconds" duration claim)
 *   <Countdown until={ms} />   "12 s"
 * The digits are `aria-hidden` (a once-a-second change inside a live region
 * would be re-spoken every second); each clock carries one screen-reader
 * sentence, fixed when it mounts. The interval does not re-render while the
 * document is hidden and catches up on the next visibility change.
 */

/**
 * Epoch ms, refreshed every second while the document is visible. With
 * `stopAt`, the clock stops ticking once it reaches that instant (a finished
 * countdown re-renders nothing more).
 */
export function useSecondClock(stopAt?: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    let timer: number | undefined;
    const tick = () => {
      if (document.visibilityState === 'hidden') return;
      const at = Date.now();
      setNow(at);
      if (stopAt !== undefined && at >= stopAt) window.clearInterval(timer);
    };
    timer = window.setInterval(tick, 1_000);
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [stopAt]);
  return now;
}

/** Whole seconds left until `until`, never negative; 0 once it has passed. */
export function secondsUntil(until: number, now: number): number {
  return Math.max(0, Math.ceil((until - now) / 1_000));
}

/** "m:ss" for a non-negative number of whole seconds. */
export function formatWait(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}

/** Seconds left until `until`, ticking once a second; the clock stops at zero. */
export function useSecondsUntil(until: number): number {
  return secondsUntil(until, useSecondClock(until));
}

export function WaitClock({ since }: { since: number }) {
  const now = useSecondClock();
  const [spoken] = useState(() => `Waiting since ${formatTimeOfDay(since)}.`);
  return (
    <>
      <span className="mono" aria-hidden="true">{formatWait((now - since) / 1_000)}</span>
      <span className="sr-only">{spoken}</span>
    </>
  );
}

export function Countdown({ until }: { until: number }) {
  const left = useSecondsUntil(until);
  const [spoken] = useState(() => `About ${secondsUntil(until, Date.now())} seconds.`);
  return (
    <>
      <span className="mono" aria-hidden="true">{left} s</span>
      <span className="sr-only">{spoken}</span>
    </>
  );
}
