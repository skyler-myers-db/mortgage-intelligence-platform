import { useContext } from 'react';
import { UNSAFE_DataRouterContext, useLocation } from 'react-router';

/**
 * useLiveSearchParams — the search params the ROUTER holds now, for a URL
 * write that must build on a write still in flight.
 *
 * The data router moves its own location, and history, at once; React
 * commits it inside startTransition (app.tsx; hooks/useRoutePending reads the
 * same gap as "pending"). Until that render commits, useSearchParams() and
 * every handler the committed render created are one navigation behind the
 * address bar. A write derived from them drops the navigation in flight:
 * two HIDE picks inside the gap kept only the second column
 * (routes/lead-queue.columns.ts). The gap is one route render, so it grows
 * with CPU load.
 *
 * Returns a reader for EVENT time. It is never called during render: the
 * router's state is not React state, and the committed render must keep
 * painting the committed URL. The reader answers a copy the caller may
 * change. It is a copy of `committed` (the caller's own useSearchParams
 * value) when there is no data router (the declarative MemoryRouter of the
 * route unit tests) and when the router is not on the caller's pathname: a
 * navigation away is in flight, or the Lead Queue is kept alive behind a
 * dossier and renders its kept location (LeadQueueKeepAlive).
 */
export function useLiveSearchParams(committed: URLSearchParams): () => URLSearchParams {
  const router = useContext(UNSAFE_DataRouterContext)?.router ?? null;
  const { pathname } = useLocation();
  return () => {
    const live = router?.state.location;
    return new URLSearchParams(live && live.pathname === pathname ? live.search : committed);
  };
}
