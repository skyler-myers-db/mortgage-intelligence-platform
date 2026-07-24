import { Suspense, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, Route, Routes, useLocation } from 'react-router';
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
 */
export function AdminRouteGate() {
  const session = useQuery<SessionResponse>({
    queryKey: ['session', 'access'],
    queryFn: ({ signal }) => api.session(signal),
    retry: false,
  });

  if (session.isPending) return <RouteFallback />;
  if (!session.data?.can_access_admin) return <Navigate to="/" replace />;
  return <AdminConfigRoute />;
}

/**
 * RouteTransition — re-keys its child on every `pathname` change so the
 * CSS `.route-transition` animation replays for each route. Scope is only
 * the inner `<main>` content; AppShell, Topbar, Rail, Console, and the
 * floating Genie panel don't animate.
 */
function RouteTransition() {
  const { pathname } = useLocation();
  return (
    <div key={pathname} className="route-transition">
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
