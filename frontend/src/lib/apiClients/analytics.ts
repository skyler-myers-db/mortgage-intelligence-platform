/**
 * Analytics + health endpoint clients: the read-only dashboard scopes
 * (executive, geography, economics, segments, signals), the equity/spread
 * scatter points, the home summary, and the honest health probe.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  EconomicsAnalyticsResponse,
  EquitySpreadPointsResponse,
  EquitySpreadViewport,
  ExecutiveAnalyticsResponse,
  GeographyAnalyticsResponse,
  HomeSummary,
  RateWindowResponse,
  SegmentAnalyticsResponse,
  SignalAnalyticsResponse,
} from '../../types';
import type { HealthHint, HealthPayload, AnalyticsQueryOptions } from '../apiTypes';
import { analyticsPath, isAbortError, getJson } from '../apiTransport';

/**
 * `/api/health`, plus `?idle_s=<int>` when the shell passes an activity hint:
 * a whole number of seconds clamped to 0..86400 (one day, at most five
 * digits); a non-finite value reads as 0.
 */
export function healthPath(hint?: HealthHint): string {
  const idleS = hint?.idleS;
  return idleS === undefined
    ? '/api/health'
    : `/api/health?idle_s=${Math.min(86_400, Math.max(0, Math.floor(idleS))) || 0}`;
}

export const analyticsApi = {
  /**
   * Honest health probe. A dead backend returns an "unreachable" status
   * object instead of throwing — callers render dependency state as
   * `unknown`, never as synthesized "up". A caller-triggered abort
   * re-throws so `HealthProvider` can cancel in-flight polls on
   * unmount. The optional hint adds only an integer `idle_s` (keep-warm).
   */
  health: async (signal?: AbortSignal, hint?: HealthHint): Promise<HealthPayload> => {
    try {
      return await getJson<HealthPayload>(healthPath(hint), signal);
    } catch (err) {
      if (isAbortError(err)) throw err;
      return { status: 'unreachable', mode: 'unknown', dependencies: {} };
    }
  },

  analyticsExecutive: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<ExecutiveAnalyticsResponse>(analyticsPath('executive', opts), signal),

  analyticsGeography: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<GeographyAnalyticsResponse>(analyticsPath('geography', opts), signal),

  analyticsEconomics: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<EconomicsAnalyticsResponse>(analyticsPath('economics', opts), signal),

  // S7 zoom: real borrower points for a scatter viewport. The server caps
  // the page and reports the honest pre-cap total (showing N of M).
  analyticsEconomicsPoints: (
    signal?: AbortSignal,
    opts: AnalyticsQueryOptions = {},
    viewport?: EquitySpreadViewport,
  ) =>
    getJson<EquitySpreadPointsResponse>(
      analyticsPath(
        'economics/points',
        opts,
        viewport
          ? {
              equity_min: viewport.equity_min,
              equity_max: viewport.equity_max,
              spread_min: viewport.spread_min,
              spread_max: viewport.spread_max,
            }
          : {},
      ),
      signal,
    ),

  analyticsSegments: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<SegmentAnalyticsResponse>(analyticsPath('segments', opts), signal),

  analyticsSignals: (signal?: AbortSignal, opts: AnalyticsQueryOptions = {}) =>
    getJson<SignalAnalyticsResponse>(analyticsPath('signals', opts), signal),

  // "Why now" rate window (dataviz-08): the precomputed weekly series from
  // mip.gold.rate_window_weekly. Unfiltered by design -- the book band is
  // measured once per refresh over the whole fixed-rate book.
  analyticsRateWindow: (signal?: AbortSignal) =>
    getJson<RateWindowResponse>('/api/analytics/rate-window', signal),

  homeSummary: (signal?: AbortSignal) =>
    getJson<HomeSummary>('/api/home/summary', signal),
};
