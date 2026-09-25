import { Fragment, lazy, Suspense, useEffect, ViewTransition, type ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, Route, Routes, useLocation } from 'react-router';
import { RouteErrorBoundary } from './components/ErrorBoundaryRoute';
import { AppShell } from './components/layout/AppShell';
import { RouteNav } from './components/layout/RouteNav';
import { RouteFallback } from './components/layout/RouteFallback';
import {
  AdminConfigRoute,
  AnalyticsRoute,
  AskGenieRoute,
  AssetRoute,
  Borrower360Route,
  GlossaryRoute,
  HomeRoute,
  LeadQueueRoute,
  NotFoundRoute,
  OfferOrchestratorRoute,
  PortfolioBuilderRoute,
  SegmentIntelligenceRoute,
  preloadLikelyNextRoutes,
} from './lib/routePreloaders';
import { api } from './lib/api';
import { ROUTE_IDS, ROUTES, type RouteId } from './lib/routeMeta';
import { usePrefersReducedMotion } from './lib/usePrefersReducedMotion';
import type { SessionResponse } from './types';
import './app.transitions.css';

// Lazy: the denied page must not cost the initial bundle anything.
const AdminAccessDeniedRoute = lazy(() => import('./routes/admin-config.access-denied'));

/**
 * AdminRouteGate keeps the server-authoritative session decision at the route
 * boundary. Backend AdminDep checks remain the security boundary; this guard
 * prevents a denied deep link from rendering an operator console full of 403
 * panels while the navigation correctly hides the same destination.
 *
 * A denied actor gets a 403 surface naming the required role and a way back,
 * not a silent redirect to Home (2026-09-21 audit shell-06). A session check
 * that failed stays closed too, but says so instead of claiming a missing role.
 */
export function AdminRouteGate() {
  const session = useQuery<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });

  if (session.isPending) return <RouteFallback />;
  if (!session.data?.can_access_admin) return <AdminAccessDeniedRoute unverified={session.isError} />;
  return <AdminConfigRoute />;
}

/**
 * What each registered route renders (audit shell-08). The PATHS live in
 * lib/routeMeta's `ROUTES`; `satisfies Record<RouteId, ...>` makes a route
 * registered there with no element here a type error, so the router, the nav,
 * the command palette, the breadcrumbs and the preloaders cannot drift.
 * Legacy outreach drafting lives inside /offer-orchestrator; its old links
 * redirect to the registered destination so a visitor never lands on a blank
 * shell.
 */
const ROUTE_ELEMENTS = {
  home: <HomeRoute />,
  analytics: <AnalyticsRoute />,
  asset: <AssetRoute />,
  portfolio: <PortfolioBuilderRoute />,
  segments: <SegmentIntelligenceRoute />,
  leads: <LeadQueueRoute />,
  borrowerIndex: <Borrower360Route />,
  borrower: <Borrower360Route />,
  glossary: <GlossaryRoute />,
  offerIndex: <OfferOrchestratorRoute />,
  offer: <OfferOrchestratorRoute />,
  askGenie: <AskGenieRoute />,
  admin: <AdminRouteGate />,
  legacyOutreach: <Navigate to={ROUTES.legacyOutreach.redirectTo} replace />,
  legacyOutreachDetail: <Navigate to={ROUTES.legacyOutreachDetail.redirectTo} replace />,
} satisfies Record<RouteId, ReactElement>;

/**
 * The View Transition classes of the painted route (app.transitions.css):
 * `::view-transition-old(.mip-route-exit)` / `::view-transition-new(.mip-route-enter)`.
 */
const ROUTE_EXIT_CLASS = 'mip-route-exit';
const ROUTE_ENTER_CLASS = 'mip-route-enter';

/**
 * RouteTransition — the painted route inside a `<ViewTransition>` keyed by
 * `pathname` (2026-09-21 audit stack-04 / motion-03 / runtime-10 / css-10 /
 * shell-10, phase 1). Scope is only the inner `<main>` content; AppShell,
 * Topbar, Rail, Console, and the floating Genie panel are not in the
 * boundary.
 *
 * Nesting: RouteErrorBoundary > Suspense > keyed ViewTransition > div >
 * Routes. The Suspense sits ABOVE the key, so it is the same, already-
 * revealed boundary on every navigation (audit shell-05, the cheap part).
 * The data router navigates inside startTransition (react-router 8.4
 * RouterProvider with `useTransitions` left undefined; never pass it), and
 * React keeps a revealed boundary's content during a transition instead of
 * falling back: a navigation to a route whose chunk is still loading holds
 * the painted page until the chunk arrives, rather than flashing the
 * RouteFallback. The first render of a route still shows the fallback,
 * wrapped in its own `.route-transition--fallback` (no route-in, so a cold
 * load makes one entrance, not two) with the page-shaped layout and the
 * `.route-transition > [data-route-fallback]` selectors unchanged. A chunk
 * that rejects during a held navigation still reaches the route boundary,
 * which offers Reload. No loaders, no useNavigation, no route objects
 * (owner decision #9).
 *
 * View Transitions: that same startTransition activates the boundary. A new
 * pathname unmounts the old keyed boundary (exit) and mounts a new one
 * (enter), so React calls document.startViewTransition once per route
 * change and the classes above animate the two unpaired snapshots.
 *   - Keyed, not one stable boundary: a stable boundary morphs its group
 *     from the old rect to the new one, and useMainScroll resets scrollTop
 *     in the same commit, so a page scrolled 2,000 px would slide 2,000 px.
 *   - update="none" / default="none": every ?query navigation (Lead Queue
 *     filters, Analytics ?view=) also runs in the router's startTransition;
 *     without them each filter change would cross-fade the route.
 *   - Reduced motion renders the same keyed wrapper under a keyed Fragment
 *     instead, so React never calls document.startViewTransition. Passing
 *     enter / exit "none" is not enough: React 19.3 starts a transition for
 *     ANY ViewTransition placement in a transition or retry commit
 *     (react-dom trackEnterViewTransitions), whatever its class, which
 *     measured one call per navigation and one on a cold load. Flipping the
 *     OS setting mid-session therefore remounts the painted route once.
 *   - No `viewTransition` option on a Link or navigate(): React Router would
 *     call startViewTransition itself and double the transition.
 *   - Back / Forward swap at once: React renders a transition started inside
 *     `popstate` synchronously (so the browser can restore scroll), and a
 *     sync render starts no View Transition.
 * Browsers without View Transitions keep the CSS `route-in` fallback; it is
 * switched off by feature support in app.transitions.css.
 *
 * HARNESS CONTRACT (w2-error-telemetry review). Because of the hold, the URL
 * can name a route that is not yet on screen (history has already moved; the
 * old route is still painted). `data-route-path` on the painted
 * `.route-transition` is the committed-route marker: exactly one painted
 * `.route-transition[data-route-path]` inside #main-content, with the route
 * content as its direct child, naming the route actually painted. A reader
 * that must know what is on screen (the fixture harness's settle,
 * tests/e2e/fixture/app.ts; app.error-boundary.test.tsx) compares it with
 * the URL instead of trusting the URL. The fallback wrapper carries none.
 *
 * The route ErrorBoundary wraps the Suspense (a boundary inside PageShell
 * could not catch a failed lazy chunk or a route-level throw) and resets on
 * pathname, so a broken route leaves the shell usable and navigating away
 * clears it. Its Try again first discards every cached query no mounted
 * component observes, the failed route's among them
 * (components/ErrorBoundaryRoute), so the re-mounted route re-reads its data.
 */
function RouteTransition() {
  const { pathname } = useLocation();
  const reducedMotion = usePrefersReducedMotion();
  const painted = (
    <div className="route-transition" data-route-path={pathname}>
      <Routes>
        {ROUTE_IDS.map((id) => (
          <Route key={id} path={ROUTES[id].pattern} element={ROUTE_ELEMENTS[id]} />
        ))}
        <Route path="*" element={<NotFoundRoute />} />
      </Routes>
    </div>
  );
  return (
    <RouteErrorBoundary pathname={pathname}>
      <Suspense
        fallback={(
          <div className="route-transition route-transition--fallback">
            <RouteFallback />
          </div>
        )}
      >
        {reducedMotion ? (
          <Fragment key={pathname}>{painted}</Fragment>
        ) : (
          <ViewTransition
            key={pathname}
            enter={ROUTE_ENTER_CLASS}
            exit={ROUTE_EXIT_CLASS}
            update="none"
            default="none"
          >
            {painted}
          </ViewTransition>
        )}
      </Suspense>
    </RouteErrorBoundary>
  );
}

export default function App() {
  useEffect(() => preloadLikelyNextRoutes(), []);
  return (
    <AppShell>
      <RouteNav />
      <RouteTransition />
    </AppShell>
  );
}
