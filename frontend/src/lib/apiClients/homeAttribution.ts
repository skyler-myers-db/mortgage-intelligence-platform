/**
 * Delta Explainer client (audit 2026-09-21 `wow-ai-3`): where one "since your
 * last login" measure moved, from the funnel snapshots nearest the baseline.
 *
 * Lazy-only, NOT spread into `api`: imported by the DeltaExplainer chunk the
 * evidence drawer body loads for a supported measure. The GET is audit-free
 * and issued only while that drawer is open on its Overview tab, never on
 * hover, prefetch or poll. A retained (stale) serve carries its last good
 * read (`X-Data-Last-Good-At`, apiClients/headers); a non-2xx rejects first.
 */
import type { HomeAttributionMeasure, HomeSummaryAttributionResponse } from '../../types/homeAttribution';
import { getJsonWithHeaders } from '../apiTransport';
import { lastGoodAtHeader, type Fresh } from './headers';

export const homeAttributionApi = {
  homeSummaryAttribution: (
    measure: HomeAttributionMeasure,
    baseline: string,
    signal?: AbortSignal,
  ): Promise<Fresh<HomeSummaryAttributionResponse>> =>
    getJsonWithHeaders<HomeSummaryAttributionResponse>(
      `/api/home/summary/attribution?measure=${encodeURIComponent(measure)}&baseline=${encodeURIComponent(baseline)}`,
      signal,
    ).then(({ data, headers }) => ({ data, lastGoodAt: lastGoodAtHeader(headers) })),
};
