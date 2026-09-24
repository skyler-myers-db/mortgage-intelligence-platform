import { useEffect, useState } from 'react';

/**
 * ElapsedTicker — whole seconds since `startedAt`, as "Ns".
 *
 * Shared by the topbar's "Waking warehouse" pill (audit delivery-01) and, once
 * w2-genie-turn swaps onto it, the Genie progress card. Same contract as
 * GenieProgress's private ticker: `{startedAt, paused, className, now}`.
 *
 * `aria-hidden` on purpose (audit `genie-v1` / `a11y-06`): a once-a-second
 * text change inside a live region or an accessible name would be re-spoken
 * every second, so the ticker must sit outside any aria text. While `paused`
 * no interval runs; resuming reads the clock first, because it kept running.
 * The first value is read in the state initialiser, so a remount mid-wait
 * paints the real elapsed time instead of flashing "0s" for a frame; the
 * shell pays for one state value and one interval, nothing per render.
 */
export interface ElapsedTickerProps {
  /** Epoch ms the elapsed time counts from. */
  startedAt: number;
  /** Stop the one-second interval (a hidden surface has nobody to show it to). */
  paused: boolean;
  className?: string;
  /** Injectable clock for tests; defaults to `Date.now`. */
  now?: () => number;
}

const elapsedSeconds = (now: () => number, startedAt: number) => Math.max(0, Math.round((now() - startedAt) / 1000));

export function ElapsedTicker({ startedAt, paused, className = 'mono', now = Date.now }: ElapsedTickerProps) {
  const [seconds, setSeconds] = useState(() => elapsedSeconds(now, startedAt));
  useEffect(() => {
    if (paused) return undefined;
    const tick = () => setSeconds(elapsedSeconds(now, startedAt));
    tick();
    const timer = window.setInterval(tick, 1_000);
    return () => window.clearInterval(timer);
  }, [paused, now, startedAt]);
  return (
    <span className={className} aria-hidden="true">
      {seconds}s
    </span>
  );
}
