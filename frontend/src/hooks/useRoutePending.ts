import { useContext, useEffect, useState } from 'react';
import { UNSAFE_DataRouterContext, useLocation } from 'react-router';

/**
 * useRoutePending — true while a navigation is held (the shell-05 remainder
 * from w2-error-telemetry; 2026-09-21 audit shell-05).
 *
 * The data router navigates inside startTransition, so a navigation to a
 * route whose chunk is still loading keeps the painted page until the chunk
 * arrives (app.tsx RouteTransition). Nothing said a navigation was in
 * flight. The router's own state moves at once; the location React has
 * COMMITTED (useLocation) moves only when the new route paints. Pending is
 * "the router's location.key differs from the committed one": through a
 * chunk hold, and also for the frame or two React spends rendering any new
 * route in its transition (the indicator's --dur-base delay hides that).
 *
 * Subscribed in an effect with plain useState, NOT useSyncExternalStore: its
 * tearing check re-renders synchronously, which would drop the Suspense hold
 * the whole mechanism relies on. Without a data router (declarative
 * MemoryRouter tests) it is always false. It reads router state only: no
 * loader, no fetch, no prefetch.
 */
export function useRoutePending(): boolean {
  const router = useContext(UNSAFE_DataRouterContext)?.router ?? null;
  const committedKey = useLocation().key;
  const [routerKey, setRouterKey] = useState<string | null>(() => router?.state.location.key ?? null);

  useEffect(() => {
    if (!router) return undefined;
    setRouterKey(router.state.location.key);
    return router.subscribe((state) => setRouterKey(state.location.key));
  }, [router]);

  return router !== null && routerKey !== null && routerKey !== committedKey;
}
