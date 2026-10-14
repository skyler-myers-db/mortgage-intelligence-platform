import { Fragment, lazy, Suspense, useEffect, useState, ViewTransition, type ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Navigate, Route, Routes, useLocation } from 'react-router';
import { RouteErrorBoundary } from './components/ErrorBoundaryRoute';
import { AppShell } from './components/layout/AppShell';
import { RouteNav } from './components/layout/RouteNav';
import { RouteFallback } from './components/layout/RouteFallback';
import { useRoutePending } from './hooks/useRoutePending';
import {
  AdminConfigRoute,
  AnalyticsRoute,
  AskGenieRoute,
  AssetRoute,
  AuditLedgerRoute,
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
import { lazyWithPreload } from './lib/lazyPreload';
import { legacyLedgerRedirect } from './lib/legacyLedgerRedirect';
import { ROUTE_IDS, ROUTES, routeSurfacePath, type RouteId } from './lib/routeMeta';
import { prefersReducedMotionAtMount, ROUTE_ENTER_CLASS, ROUTE_EXIT_CLASS } from './lib/routeMotion';
import { canReadAuditLedger, sessionQueryOptions } from './lib/sessionQuery';
import type * as LeadQueueModule from './routes/lead-queue';
import './app.transitions.css';
// The evidence hover card's sheet ships with the initial CSS (it was in
// partial 02 before): every route renders evidence chips, and as a lazy sheet
// with its hook it counted against every route closure (+0.56 KiB br each).
import './components/EvidenceHoverCard.css';

// Lazy: the denied pages must not cost the initial bundle anything.
const AdminAccessDeniedRoute = lazy(() => import('./routes/admin-config.access-denied'));
const AuditLedgerAccessDeniedRoute = lazy(() => import('./routes/audit-ledger.access-denied'));

/**
 * The ONE session gate both gated routes share (the shell's
 * `['session', 'access']` read, never retried): Administration, or with
 * `ledger` the audit ledger (an administrator or a read-only auditor, the
 * decision `require_audit_reader` enforces). Pending renders the route
 * fallback; a denied or failed check renders the route's own 403 surface,
 * never the route chunk and never one of its reads.
 */
function SessionRouteGate({ ledger = false }: { ledger?: boolean }) {
  const session = useQuery(sessionQueryOptions());

  if (session.isPending) return <RouteFallback />;
  const unverified = session.isError;
  if (ledger) {
    return canReadAuditLedger(session.data)
      ? <AuditLedgerRoute />
      : <AuditLedgerAccessDeniedRoute unverified={unverified} />;
  }
  return session.data?.can_access_admin
    ? <AdminConfigRoute />
    : <AdminAccessDeniedRoute unverified={unverified} />;
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
 *
 * An old explorer link (`/admin-config?audit_…`, D-audit-reads-c3) is sent on
 * to /audit-ledger BEFORE the admin decision, so an auditor following it
 * lands on the ledger instead of the admin 403.
 */
export function AdminRouteGate() {
  const { search } = useLocation();
  const ledger = legacyLedgerRedirect(search);
  return ledger ? <Navigate replace to={ledger} /> : <SessionRouteGate />;
}

/**
 * The audit ledger (D-audit-reads-c3): administrators and the read-only
 * Auditor role, by the same decision `require_audit_reader` enforces. A
 * denied actor loads no ledger chunk and issues no ledger read.
 */
export function AuditLedgerGate() {
  return <SessionRouteGate ledger />;
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
  askGenieConversation: <AskGenieRoute />,
  auditLedger: <AuditLedgerGate />,
  admin: <AdminRouteGate />,
  legacyOutreach: <Navigate to={ROUTES.legacyOutreach.redirectTo} replace />,
  legacyOutreachDetail: <Navigate to={ROUTES.legacyOutreachDetail.redirectTo} replace />,
} satisfies Record<RouteId, ReactElement>;

// The Lead Queue keep-alive slot (components/layout/LeadQueueKeepAlive) rides
// the route's own chunk: no chunk of its own, no second request.
let queueSlotLoaded = false;
const LeadQueueSlot = lazyWithPreload(() => (LeadQueueRoute.preload() as Promise<typeof LeadQueueModule>)
  .then((mod) => {
    queueSlotLoaded = true;
    return { default: mod.LeadQueueKeepAlive };
  }));

/**
 * RouteTransition — the painted route inside a `<ViewTransition>` keyed by
 * the page's surface path (2026-09-21 audit stack-04 / motion-03 / runtime-10 /
 * css-10 / shell-10, phase 1): the pathname, except that a conversation deep
 * link keys as its index route (lib/routeMeta `routeSurfacePath`, audit
 * shell-03), so /ask-genie <-> /ask-genie/<id> keeps ONE boundary and never
 * remounts the route; `data-route-path` stays the raw pathname. Scope is
 * only the inner `<main>` content; AppShell, Topbar, Rail, Console, and the
 * floating Genie panel are not in the boundary.
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
 * which offers Reload. No loaders, no useNavigation, no route objects: the
 * 2026-09-30 ruling (docs/prototype-deviations.md, data-router-loaders) keeps
 * route data on TanStack Query because a loader would write a VIEW_* audit
 * row on every revalidation instead of once per deliberate open, a cold
 * warehouse would hold the navigation instead of painting the route's own
 * warm-up state, and data-before-mount already ships as prefetchRouteData
 * (lib/routeDataPrefetch).
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
 *     measured one call per navigation and one on a cold load. The
 *     preference is read ONCE, at mount: switching the element type later
 *     would remount the painted route and drop its state (a decision receipt
 *     vanished when the OS setting flipped). A mid-session switch to reduce
 *     is honoured by app.transitions.css's reduced-motion belt instead.
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
 * Pending navigation (the shell-05 remainder; a DECLARED DEVIATION, the
 * prototype has none; deviation:route-pending-line): while a navigation is held, the painted wrapper
 * carries aria-busy="true" (hooks/useRoutePending) and app.transitions.css
 * draws a 2px --accent-ink line under the route nav once the hold outlasts
 * --dur-base, so a preloaded route never flashes it.
 *
 * The Lead Queue keep-alive slot (W5c, audit runtime-08;
 * components/layout/LeadQueueKeepAlive): the queue renders in a lazy slot
 * BESIDE the keyed boundary, inside this always-mounted Suspense, mounted on
 * /lead-queue and on Borrower 360 (where it keeps, hidden under <Activity>,
 * the queue the reader came from) and unmounted for any other destination. On /lead-queue the keyed boundary
 * renders nothing and the slot paints the one marker; hidden, it names no
 * route. Sharing this Suspense keeps the hold for a navigation INTO the queue
 * and the page-shaped cold-load fallback. A slot chunk that failed never
 * follows the reader to a dossier.
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
  const surfacePath = routeSurfacePath(pathname);
  const [reducedMotion] = useState(prefersReducedMotionAtMount);
  const pending = useRoutePending();
  const onQueue = pathname === ROUTES.leads.pattern;
  // Mounted on the queue, and on a dossier once the slot has loaded: the slot
  // keeps a queue only when the reader came from it (it renders nothing else).
  const keepQueue = onQueue || (queueSlotLoaded && pathname.startsWith(ROUTES.borrowerIndex.pattern));
  const painted = (
    <div className="route-transition" data-route-path={pathname} aria-busy={pending ? 'true' : undefined}>
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
        {/* The Lead Queue paints in its keep-alive slot instead (below). */}
        {onQueue ? null : reducedMotion ? (
          <Fragment key={surfacePath}>{painted}</Fragment>
        ) : (
          <ViewTransition
            key={surfacePath}
            enter={ROUTE_ENTER_CLASS}
            exit={ROUTE_EXIT_CLASS}
            update="none"
            default="none"
          >
            {painted}
          </ViewTransition>
        )}
        {keepQueue && <LeadQueueSlot />}
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
