import { api } from './api';
import { queryKeys } from './queryKeys';

/**
 * The Home hero's three non-audited reads as key + fetcher pairs, so the
 * route-data prefetch (lib/routeDataPrefetch, audit delivery-03) can start
 * them before the lazy Home chunk has even loaded. They mirror routes/home.tsx
 * (`requestHomePortfolioPreview`, `requestHomeSummary`) and
 * routes/home.approval-banner.tsx (`requestHomeContactablePreview`) byte for
 * byte; routeDataPrefetch.test.tsx pins the parity against the mounted route
 * until those files import this module.
 *
 * None of these reads writes an audit row: the preview is a pure aggregate
 * (the banner deliberately avoids GET /api/leads, which writes VIEW_LEADS).
 */

export const HOME_PORTFOLIO_PREVIEW_CRITERIA = { marketing_eligibility: 'Any' } as const;
export const HOME_CONTACTABLE_PREVIEW_CRITERIA = { marketing_eligibility: 'Eligible only' } as const;

export const homeQueries = {
  homePreview: {
    queryKey: () => queryKeys.homePreview(),
    fetch: (signal?: AbortSignal) => api.portfolioPreview(HOME_PORTFOLIO_PREVIEW_CRITERIA, signal),
  },
  homeContactablePreview: {
    queryKey: () => queryKeys.portfolioPreview(['home', 'contactable']),
    fetch: (signal?: AbortSignal) => api.portfolioPreview(HOME_CONTACTABLE_PREVIEW_CRITERIA, signal),
  },
  homeSummary: {
    queryKey: () => queryKeys.homeSummary(),
    fetch: (signal?: AbortSignal) => api.homeSummary(signal),
  },
} as const;
