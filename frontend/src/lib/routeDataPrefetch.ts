import type { QueryClient } from '@tanstack/react-query';
import { api } from './api';
import { geoQueryKeys, requestStateRollupsByCode, rollupCohort } from './geoQueryKeys';
import { homeQueries } from './homeQueries';
import { queryKeys } from './queryKeys';
import { preloadRouteForPath } from './routePreloaders';

/**
 * Route data prefetch (audit delivery-03): a route's hero reads start beside
 * its lazy chunk instead of after the chunk has loaded and mounted. Called by
 * main.tsx before render for the landing URL, and by RouteNav on Analytics
 * hover / focus.
 *
 * Only NON-AUDITED aggregate reads are ever prefetched. GET /api/leads
 * (VIEW_LEADS), borrower and proof reads (VIEW_*), outreach drafts
 * (DRAFT_OUTREACH) and offer recommendations (RECOMMEND_OFFER) start at mount
 * so each writes exactly one audit row for a page the person opened; the
 * queue, borrower and offer paths preload their chunks only.
 *
 * RETRY RULE: never pass `retry`. TanStack query-core 5.100 joins a mounted
 * observer to the in-flight retryer, whose retry config was captured when the
 * fetch started (query.js:183-193, 264-287): a `retry: false` prefetch that
 * meets a 503 warming_up would surface a terminal error with no warming loop.
 * Without it the QueryClient default applies (queryClient.ts:
 * planForReason 5 s x 6, never a client failure), the same plan
 * useWarmingUpRetry gives the mounted route.
 */

/** The Analytics route's default executive criteria, as analytics.tsx builds them with no filter in the URL. */
const ANALYTICS_DEFAULT_CRITERIA = ['all', 'all', 'All', 'all'] as const;
const ANALYTICS_STALE_MS = 60_000;

/** Home: the three hero reads and the national state rollups of the hero map. */
function prefetchHome(queryClient: QueryClient): void {
  const { homePreview, homeContactablePreview, homeSummary } = homeQueries;
  void queryClient.prefetchQuery({ queryKey: homePreview.queryKey(), queryFn: ({ signal }) => homePreview.fetch(signal) });
  void queryClient.prefetchQuery({
    queryKey: homeContactablePreview.queryKey(),
    queryFn: ({ signal }) => homeContactablePreview.fetch(signal),
  });
  void queryClient.prefetchQuery({ queryKey: homeSummary.queryKey(), queryFn: ({ signal }) => homeSummary.fetch(signal) });
  void queryClient.prefetchQuery({
    queryKey: geoQueryKeys.stateRollups(rollupCohort(null, 'any')),
    queryFn: ({ signal }) => requestStateRollupsByCode(null, 'any', undefined, signal),
  });
}

/** Analytics with no filter: the executive tab's read and the rate window beside it. */
function prefetchAnalytics(queryClient: QueryClient): void {
  void queryClient.prefetchQuery({
    queryKey: queryKeys.analytics('executive', ANALYTICS_DEFAULT_CRITERIA),
    queryFn: ({ signal }) => api.analyticsExecutive(signal, {
      states: [],
      segmentCodes: [],
      segmentMode: 'any',
      lenderRelationship: null,
      targetLenderRef: null,
    }),
    staleTime: ANALYTICS_STALE_MS,
  });
  void queryClient.prefetchQuery({
    queryKey: queryKeys.analytics('rate-window'),
    queryFn: ({ signal }) => api.analyticsRateWindow(signal),
    staleTime: ANALYTICS_STALE_MS,
  });
}

/**
 * Preload the route chunk for `pathname`, then prefetch its hero data: `/`
 * gets Home's reads, `/analytics` with an EMPTY search gets the executive
 * and rate-window reads, and every other path prefetches no data.
 */
export function prefetchRouteData(queryClient: QueryClient, pathname: string, search: string): void {
  preloadRouteForPath(pathname);
  if (pathname === '/') prefetchHome(queryClient);
  else if (pathname === '/analytics' && (search === '' || search === '?')) prefetchAnalytics(queryClient);
}
