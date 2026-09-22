import { useCallback, useLayoutEffect, useRef, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary } from './ErrorBoundary';
import { routeLabelForPath } from './ErrorBoundaryFallback';

/**
 * RouteErrorBoundary: the route-level ErrorBoundary (app.tsx), plus what a
 * plain boundary cannot do for a data-driven route: make "Try again" re-read
 * the failed route's data.
 *
 * Why: a route that throws while rendering a malformed API payload leaves
 * that payload in the TanStack Query cache (fresh for 30 s). Clearing the
 * boundary alone re-mounts the route onto the same cached result, which
 * throws again, so Try again could never recover; only Reload did. Marking
 * the queries stale is not enough either: a stale query still hands its
 * cached data to the re-mounted route's first render (stale-while-revalidate),
 * and that render throws before the refetch starts.
 *
 * How: for the lifetime of one route visit (app.tsx re-keys this component on
 * every pathname) it records each query the cache reports activity for. On
 * Try again it removes the recorded queries that no mounted component
 * observes any more, which are the failed route's own (the boundary has just
 * unmounted them), so the re-mounted route starts them fresh. Queries the
 * shell still observes (health, session, Console, the docked Genie panel)
 * and caches this visit never touched are left alone.
 *
 * Audit posture: nothing is refetched until the route re-mounts on the
 * user's click. A Try again may re-issue the route's audited read (a VIEW_*
 * audit row), which is the user's explicit retry; no read happens passively.
 */

function useFailedRouteQueryReset(): () => void {
  const queryClient = useQueryClient();
  const touched = useRef<Set<string>>(new Set());

  // A layout effect subscribes before any child's passive effect runs, so the
  // route's own observers (subscribed in passive effects) are recorded from
  // the very first commit of the visit.
  useLayoutEffect(() => {
    const seen = touched.current;
    return queryClient.getQueryCache().subscribe((event) => {
      if (event.type === 'removed') seen.delete(event.query.queryHash);
      else seen.add(event.query.queryHash);
    });
  }, [queryClient]);

  return useCallback(() => {
    const seen = touched.current;
    queryClient.removeQueries({
      predicate: (query) => seen.has(query.queryHash) && query.getObserversCount() === 0,
    });
  }, [queryClient]);
}

interface RouteErrorBoundaryProps {
  /** The current pathname: resets a caught error and names the route in the copy. */
  pathname: string;
  children: ReactNode;
}

export function RouteErrorBoundary({ pathname, children }: RouteErrorBoundaryProps) {
  const resetFailedRouteQueries = useFailedRouteQueryReset();
  return (
    <ErrorBoundary
      boundary="route"
      resetKey={pathname}
      routeLabel={routeLabelForPath(pathname)}
      onRetry={resetFailedRouteQueries}
    >
      {children}
    </ErrorBoundary>
  );
}
