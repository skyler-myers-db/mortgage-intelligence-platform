import { lazy, Suspense, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, Route, Routes, useLocation } from 'react-router';
import { RouteErrorBoundary } from './components/ErrorBoundaryRoute';
import { AppShell } from './components/layout/AppShell';
import { RouteNav } from './components/layout/RouteNav';
import { Skeleton } from './components/ui/Skeleton';
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
import type { SessionResponse } from './types';

// Lazy: the denied page must not cost the initial bundle anything.
const AdminAccessDeniedRoute = lazy(() => import('./routes/admin-config.access-denied'));

function RouteFallback() {
  return (
    <div className="surface" aria-busy="true" role="status">
      <div className="surface__hdr">
        <Skeleton width={28} height={28} rounded="md" />
        <Skeleton width={180} height={18} rounded="sm" />
      </div>
      <div className="surface__body surface__body--stack-sm">
        <Skeleton width="55%" height={16} rounded="sm" />
        <Skeleton width="80%" height={12} rounded="sm" />
        <Skeleton width="70%" height={12} rounded="sm" />
      </div>
    </div>
  );
}

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
 * RouteTransition — re-keys its child on every `pathname` change so the
 * CSS `.route-transition` animation replays for each route. Scope is only
 * the inner `<main>` content; AppShell, Topbar, Rail, Console, and the
 * floating Genie panel don't animate.
 *
 * The route ErrorBoundary wraps the Suspense (a boundary inside PageShell
 * could not catch a failed lazy chunk or a route-level throw) and resets on
 * pathname, so a broken route leaves the shell usable and navigating away
 * clears it even if the `key` re-mount is ever dropped. Its Try again first
 * discards the failed route's cached queries (components/ErrorBoundaryRoute)
 * so the re-mounted route re-reads its data.
 */
function RouteTransition() {
  const { pathname } = useLocation();
  return (
    <div key={pathname} className="route-transition">
      <RouteErrorBoundary pathname={pathname}>
        <Suspense fallback={<RouteFallback />}>
          <Routes>
            <Route path="/" element={<HomeRoute />} />
            <Route path="/analytics" element={<AnalyticsRoute />} />
            <Route path="/data-estate/assets/:assetKey" element={<AssetRoute />} />
            <Route path="/portfolio-builder" element={<PortfolioBuilderRoute />} />
            <Route path="/segment-intelligence" element={<SegmentIntelligenceRoute />} />
            <Route path="/lead-queue" element={<LeadQueueRoute />} />
            <Route path="/borrower-360" element={<Borrower360Route />} />
            <Route path="/borrower-360/:id" element={<Borrower360Route />} />
            <Route path="/glossary" element={<GlossaryRoute />} />
            <Route path="/offer-orchestrator" element={<OfferOrchestratorRoute />} />
            <Route path="/offer-orchestrator/:id" element={<OfferOrchestratorRoute />} />
            <Route path="/ask-genie" element={<AskGenieRoute />} />
            <Route path="/admin-config" element={<AdminRouteGate />} />
            {/* Outreach drafting lives inside /offer-orchestrator; any
                legacy /outreach-composer link redirects to the lead queue
                so a visitor never lands on a blank shell. */}
            <Route path="/outreach-composer" element={<Navigate to="/lead-queue" replace />} />
            <Route path="/outreach-composer/:id" element={<Navigate to="/lead-queue" replace />} />
            <Route path="*" element={<NotFoundRoute />} />
          </Routes>
        </Suspense>
      </RouteErrorBoundary>
    </div>
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
