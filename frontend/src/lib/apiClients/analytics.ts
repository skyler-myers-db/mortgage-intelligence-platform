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
  SegmentAnalyticsResponse,
  SignalAnalyticsResponse,
} from '../../types';
import type { HealthPayload, AnalyticsQueryOptions } from '../apiTypes';
import { analyticsPath, isAbortError, getJson } from '../apiTransport';

export const analyticsApi = {
  /**
   * Honest health probe. A dead backend returns an "unreachable" status
   * object instead of throwing — callers render dependency state as
   * `unknown`, never as synthesized "up". A caller-triggered abort
   * re-throws so `HealthProvider` can cancel in-flight polls on
   * unmount.
   */
  health: async (signal?: AbortSignal): Promise<HealthPayload> => {
    try {
      return await getJson<HealthPayload>('/api/health', signal);
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

  homeSummary: (signal?: AbortSignal) =>
    getJson<HomeSummary>('/api/home/summary', signal),
};
