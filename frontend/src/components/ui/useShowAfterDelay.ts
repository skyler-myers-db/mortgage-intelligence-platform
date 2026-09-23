import { useEffect, useState } from 'react';

/**
 * Show-delay for a placeholder (the spin-delay pattern, audit 2026-09-21
 * `states-10`): false until `delayMs` has passed since mount, then true.
 *
 * Use it only where the awaited thing is routinely faster than ~200ms and
 * NOT already cached (TanStack cache hits render synchronously and never show
 * a skeleton). Today that is the route Suspense fallback: a preloaded route
 * chunk or the session check resolves in a few frames, and without the delay
 * the page flashed a skeleton for one of them. Callers keep the placeholder's
 * box laid out while hidden (visibility, not unmount) so its space stays
 * reserved and nothing shifts when it appears.
 */
export function useShowAfterDelay(delayMs: number): boolean {
  const [shown, setShown] = useState(delayMs <= 0);
  useEffect(() => {
    if (delayMs <= 0) return undefined;
    const timer = window.setTimeout(() => setShown(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs]);
  return shown;
}
