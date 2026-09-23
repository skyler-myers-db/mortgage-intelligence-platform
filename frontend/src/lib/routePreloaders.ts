import { lazyWithPreload, preloadBestEffort } from './lazyPreload';
import { createIdlePreloader } from './prefetch';
import { ROUTE_IDS, ROUTES, indexPathOf, type IndexPath, type RouteChunk, type RouteId } from './routeMeta';

// Audit bundle-07: the Home hero map needs the state geometry chunk, which the
// map used to request only from a mount effect, a fourth sequential fetch
// after the route chunk. Start it beside the route chunk instead. The loader
// is shared by HomeRoute.preload (hover/idle) and the lazy render, so the
// geometry is warmed on whichever comes first; loadUsaStateMap is
// single-flight, so nothing is fetched twice. Best-effort: a geometry failure
// must never fail the route, and the map's own load retries later. The module
// is dynamically imported so the initial bundle carries no map code.
function warmHomeGeometry(): Promise<unknown> {
  return import('../components/mortgage/USStateMapData').then((mod) => mod.loadUsaStateMap());
}

export const HomeRoute = lazyWithPreload(() => {
  preloadBestEffort(warmHomeGeometry);
  return import('../routes/home');
});
export const AnalyticsRoute = lazyWithPreload(() => import('../routes/analytics'));
export const AssetRoute = lazyWithPreload(() => import('../routes/asset'));
export const PortfolioBuilderRoute = lazyWithPreload(() => import('../routes/portfolio-builder'));
export const SegmentIntelligenceRoute = lazyWithPreload(() => import('../routes/segment-intelligence'));
export const LeadQueueRoute = lazyWithPreload(() => import('../routes/lead-queue'));
export const Borrower360Route = lazyWithPreload(() => import('../routes/borrower-360'));
export const GlossaryRoute = lazyWithPreload(() => import('../routes/glossary'));
export const NotFoundRoute = lazyWithPreload(() => import('../routes/not-found'));
export const OfferOrchestratorRoute = lazyWithPreload(() => import('../routes/offer-orchestrator'));
export const AskGenieRoute = lazyWithPreload(() => import('../routes/ask-genie'));
export const AdminConfigRoute = lazyWithPreload(() => import('../routes/admin-config'));

/**
 * The lazy module behind each route chunk. `satisfies` makes a chunk the
 * registry names but nothing renders a compile error.
 */
export const ROUTE_CHUNKS = {
  home: HomeRoute,
  analytics: AnalyticsRoute,
  asset: AssetRoute,
  portfolio: PortfolioBuilderRoute,
  segments: SegmentIntelligenceRoute,
  leads: LeadQueueRoute,
  borrower: Borrower360Route,
  glossary: GlossaryRoute,
  offer: OfferOrchestratorRoute,
  askGenie: AskGenieRoute,
  admin: AdminConfigRoute,
} as const satisfies Record<RouteChunk, { preload: () => Promise<unknown> }>;

type ChunkedRouteId = {
  [Id in RouteId]: (typeof ROUTES)[Id] extends { chunk: RouteChunk } ? Id : never;
}[RouteId];

/** `/borrower-360` for `/borrower-360/:id`: the key a nav link preloads by. */
export type PreloadPath = IndexPath<(typeof ROUTES)[ChunkedRouteId]['pattern']>;

/**
 * Route chunk preloaders keyed by index path, derived from the route registry
 * (audit shell-08): a route added to `ROUTES` is preloadable by its nav link
 * without a second hand-kept table.
 */
export const routePreloaders = Object.fromEntries(
  ROUTE_IDS.flatMap((id) => {
    const route: { pattern: string; chunk?: RouteChunk } = ROUTES[id];
    return route.chunk ? [[indexPathOf(route.pattern), ROUTE_CHUNKS[route.chunk].preload]] : [];
  }),
) as Record<PreloadPath, () => Promise<unknown>>;

export const preloadLikelyNextRoutes = createIdlePreloader(async () => {
  await Promise.all([
    routePreloaders['/portfolio-builder'](),
    routePreloaders['/analytics'](),
    routePreloaders['/segment-intelligence'](),
    routePreloaders['/lead-queue'](),
  ]);
}, 5000);

export function preloadRouteForPath(path: string): void {
  const cleanPath = path.split(/[?#]/, 1)[0] || '/';
  const key = (Object.keys(routePreloaders) as PreloadPath[])
    .find((candidate) => cleanPath === candidate || (candidate !== '/' && cleanPath.startsWith(`${candidate}/`)));
  if (key) preloadBestEffort(routePreloaders[key]);
}
