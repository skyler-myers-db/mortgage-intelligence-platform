import { lazy, Suspense, useEffect, type ReactElement } from 'react';
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
import type { SessionResponse } from './types';

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
 * RouteTransition — re-keys the route content on every `pathname` change so
 * the CSS `.route-transition` animation replays for each route. Scope is only
 * the inner `<main>` content; AppShell, Topbar, Rail, Console, and the
 * floating Genie panel don't animate.
 *
 * Nesting (audit 2026-09-21 shell-05, the cheap part): RouteErrorBoundary >
 * Suspense > keyed div > Routes. The Suspense sits ABOVE the key, so it is
 * the same, already-revealed boundary on every navigation. The data router
 * navigates inside startTransition (react-router 8.3 RouterProvider
 * setState), and React keeps a revealed boundary's content during a
 * transition instead of falling back: a navigation to a route whose chunk is
 * still loading holds the painted page until the chunk arrives, rather than
 * flashing the RouteFallback. The first render of a route still shows the
 * fallback, wrapped in its own `.route-transition` so the page-shaped layout
 * (and the `.route-transition > [data-route-fallback]` selectors) are
 * unchanged. A chunk that rejects during a held navigation still reaches the
 * route boundary, which offers Reload. No loaders, no useNavigation, no route
 * objects (owner decision #9).
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
  return (
    <RouteErrorBoundary pathname={pathname}>
      <Suspense
        fallback={(
          <div className="route-transition">
            <RouteFallback />
          </div>
        )}
      >
        <div key={pathname} className="route-transition">
          <Routes>
            {ROUTE_IDS.map((id) => (
              <Route key={id} path={ROUTES[id].pattern} element={ROUTE_ELEMENTS[id]} />
            ))}
            <Route path="*" element={<NotFoundRoute />} />
          </Routes>
        </div>
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
