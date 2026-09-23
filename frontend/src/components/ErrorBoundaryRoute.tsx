import { useCallback, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { ErrorBoundary } from './ErrorBoundary';
import { routeLabelForPath } from './ErrorBoundaryFallback';

/**
 * RouteErrorBoundary: the route-level ErrorBoundary (app.tsx), plus what a
 * plain boundary cannot do for a data-driven route: make "Try again" re-read
 * the failed route's data.
 *
 * Why: a route that throws while rendering a malformed API payload leaves
 * that payload in the TanStack Query cache (fresh for 30 s, kept for 5 min).
 * Clearing the boundary alone re-mounts the route onto the same cached
 * result, which throws again, so Try again could never recover; only Reload
 * did. Marking the queries stale is not enough either: a stale query still
 * hands its cached data to the re-mounted route's first render
 * (stale-while-revalidate), and that render throws before the refetch starts.
 *
 * Which queries to drop cannot be learned by watching the cache: a render
 * that throws never commits, so it subscribes no observer and the cache
 * reports nothing for a query it read from an existing entry. That is the
 * case when the user comes back to a route that already threw (Back, or a
 * Leads-row link, within the cache lifetime) and when a route throws on a
 * payload another route cached (routes share query keys: the sales team on
 * Lead Queue and Analytics, asset metadata in the evidence drawer and on the
 * asset page, the activation summary on Offer Orchestrator and Admin).
 *
 * How: on Try again it removes every cached query that no mounted component
 * observes. The failed route's own queries are among them (the boundary has
 * unmounted the route), so the re-mounted route starts them fresh, whichever
 * visit or route cached them. Covered: the first visit, a revisit that
 * re-throws from an earlier visit's cache, and a payload another route
 * cached. Not covered: a malformed payload in a query a mounted shell
 * component still observes (session access, workspace, footprint, the
 * Console's recent activity); it is kept, and the shell reading it is its
 * own failure.
 *
 * Cost: other routes' unobserved warm caches are dropped too, so the next
 * visit to such a route renders its loading state and reads again. The Genie
 * transcript is not a query (lib/genieConversationStore) and is unaffected.
 *
 * Audit posture: removing a query never fetches it. Nothing is read until the
 * route re-mounts on the user's click; that Try again may re-issue the
 * route's audited read (a VIEW_* audit row), which is the user's explicit
 * retry, and a dropped cache of another route is read again only when the
 * user navigates there. No read happens passively.
 */
function useUnobservedQueryReset(): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    // Zero observers, not `type: 'inactive'`: a query held only by a disabled
    // observer is inactive yet still mounted, and removing it would detach
    // that observer from the cache.
    queryClient.removeQueries({ predicate: (query) => query.getObserversCount() === 0 });
  }, [queryClient]);
}

interface RouteErrorBoundaryProps {
  /** The current pathname: resets a caught error and names the route in the copy. */
  pathname: string;
  children: ReactNode;
}

export function RouteErrorBoundary({ pathname, children }: RouteErrorBoundaryProps) {
  const resetUnobservedQueries = useUnobservedQueryReset();
  return (
    <ErrorBoundary
      boundary="route"
      resetKey={pathname}
      routeLabel={routeLabelForPath(pathname)}
      onRetry={resetUnobservedQueries}
    >
      {children}
    </ErrorBoundary>
  );
}
