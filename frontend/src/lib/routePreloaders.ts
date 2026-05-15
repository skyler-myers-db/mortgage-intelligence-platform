import { lazyWithPreload, preloadBestEffort } from './lazyPreload';
import { createIdlePreloader } from './prefetch';

export const HomeRoute = lazyWithPreload(() => import('../routes/home'));
export const PortfolioBuilderRoute = lazyWithPreload(() => import('../routes/portfolio-builder'));
export const SegmentIntelligenceRoute = lazyWithPreload(() => import('../routes/segment-intelligence'));
export const LeadQueueRoute = lazyWithPreload(() => import('../routes/lead-queue'));
export const Borrower360Route = lazyWithPreload(() => import('../routes/borrower-360'));
export const OfferOrchestratorRoute = lazyWithPreload(() => import('../routes/offer-orchestrator'));
export const AskGenieRoute = lazyWithPreload(() => import('../routes/ask-genie'));
export const AdminConfigRoute = lazyWithPreload(() => import('../routes/admin-config'));

export const routePreloaders = {
  '/': HomeRoute.preload,
  '/portfolio-builder': PortfolioBuilderRoute.preload,
  '/segment-intelligence': SegmentIntelligenceRoute.preload,
  '/lead-queue': LeadQueueRoute.preload,
  '/borrower-360': Borrower360Route.preload,
  '/offer-orchestrator': OfferOrchestratorRoute.preload,
  '/ask-genie': AskGenieRoute.preload,
  '/admin-config': AdminConfigRoute.preload,
} as const;

export const preloadLikelyNextRoutes = createIdlePreloader(async () => {
  await Promise.all([
    routePreloaders['/portfolio-builder'](),
    routePreloaders['/segment-intelligence'](),
    routePreloaders['/lead-queue'](),
  ]);
}, 5000);

export function preloadRouteForPath(path: string): void {
  const cleanPath = path.split(/[?#]/, 1)[0] || '/';
  const key = (Object.keys(routePreloaders) as Array<keyof typeof routePreloaders>)
    .find((candidate) => cleanPath === candidate || (candidate !== '/' && cleanPath.startsWith(`${candidate}/`)));
  if (key) preloadBestEffort(routePreloaders[key]);
}
