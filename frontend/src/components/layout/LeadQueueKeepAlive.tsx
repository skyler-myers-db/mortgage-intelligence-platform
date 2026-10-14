import { Activity, useEffect, useState, ViewTransition } from 'react';
import { Route, Routes, useLocation, useNavigate, type Location } from 'react-router';
import { RouteErrorBoundary } from '../ErrorBoundaryRoute';
import { useRoutePending } from '../../hooks/useRoutePending';
import { LeadQueueRoute } from '../../lib/routePreloaders';
import { ROUTES } from '../../lib/routeMeta';
import { prefersReducedMotionAtMount, ROUTE_ENTER_CLASS, ROUTE_EXIT_CLASS } from '../../lib/routeMotion';

const QUEUE_PATH = ROUTES.leads.pattern;

interface KeptQueue {
  location: Location;
  /** A new queue (other filters) remounts the slot's boundary and route. */
  instance: number;
}

const queueSearch = (location: Location): string => {
  const params = new URLSearchParams(location.search);
  params.delete('row');
  return params.toString();
};

/** The kept Lead Queue after a navigation from `prior` to `next` (a dossier, or the queue). */
function nextKeptQueue(kept: KeptQueue | null, prior: Location, next: Location): KeptQueue | null {
  // A dossier: the hidden queue (if the reader came from one) stays frozen.
  if (next.pathname !== QUEUE_PATH) return kept;
  if (kept) {
    // Live on the queue, or back to it with the same filters (`row` aside): the kept view.
    if (kept.location.key === prior.key || queueSearch(kept.location) === queueSearch(next)) {
      return { ...kept, location: next };
    }
    // The bare Leads link (nav, Cmd-K) while a view is kept reveals it; the URL is replaced below.
    if (next.search === '') return kept;
  }
  return { location: next, instance: (kept?.instance ?? 0) + 1 };
}

/**
 * The Lead Queue's keep-alive slot (W5c, audit runtime-08; w5b_rulings[3]).
 * app.tsx's RouteTransition mounts it on /lead-queue and on Borrower 360,
 * and unmounts it for any other destination (the query cache keeps the
 * loaded pages for their gcTime). Mounted on a dossier the reader did not
 * reach from the queue, it keeps nothing and renders nothing. It rides the route's own
 * lazy chunk (re-exported by routes/lead-queue.tsx), off the initial bundle. The queue renders HERE,
 * outside the keyed route boundary, so a dossier visit HIDES it under
 * <Activity> instead of unmounting it: its sort, expanded row, selection,
 * loaded pages and table scroll survive, and Back reveals it with no
 * /api/leads read (every served page writes a VIEW_LEADS row). Hidden, its
 * effects are unmounted (hotkeys, the queue-version poll, focus listeners)
 * and its hooks read the kept location.
 *
 * Reveal: Back, or a return whose search equals the kept search once `row`
 * is removed; the bare Leads link reveals the kept view and replaces the URL
 * with its filters. Any other search starts a new queue (a new instance).
 *
 * The hidden slot is neither a `.route-transition` nor a `data-route-path`
 * marker: exactly one painted `.route-transition[data-route-path]`, as the
 * harness settle reads it, and no second wrapper for a route locator.
 * There is no Suspense here: RouteTransition's always-mounted one holds a
 * navigation into the queue (shell-05) and paints the cold-load fallback.
 * React treats an Activity reveal as an enter and a hide as an exit, so the
 * route motion is RouteTransition's (app.view-transition.test).
 */
export default function LeadQueueKeepAlive() {
  const location = useLocation();
  const navigate = useNavigate();
  const pending = useRoutePending();
  const [reducedMotion] = useState(prefersReducedMotionAtMount);
  const [kept, setKept] = useState<KeptQueue | null>(
    location.pathname === QUEUE_PATH ? { location, instance: 0 } : null,
  );
  const [seen, setSeen] = useState(location);
  if (seen.key !== location.key) {
    setSeen(location);
    setKept(nextKeptQueue(kept, seen, location));
  }
  const visible = location.pathname === QUEUE_PATH;
  const revealing = visible && kept !== null && kept.location.key !== location.key;
  useEffect(() => {
    if (revealing && kept) void navigate(`${QUEUE_PATH}${kept.location.search}`, { replace: true });
  }, [revealing, kept, navigate]);
  if (!kept) return null;
  const painted = (
    <div
      className={visible ? 'route-transition route-transition--lead-queue' : undefined}
      data-route-path={visible ? QUEUE_PATH : undefined}
      aria-busy={visible && pending ? 'true' : undefined}
    >
      <Routes location={kept.location}>
        <Route path={QUEUE_PATH} element={<LeadQueueRoute />} />
      </Routes>
    </div>
  );
  return (
    <Activity mode={visible ? 'visible' : 'hidden'}>
      <RouteErrorBoundary key={kept.instance} pathname={QUEUE_PATH}>
        {reducedMotion ? painted : (
          <ViewTransition enter={ROUTE_ENTER_CLASS} exit={ROUTE_EXIT_CLASS} update="none" default="none">
            {painted}
          </ViewTransition>
        )}
      </RouteErrorBoundary>
    </Activity>
  );
}
