/**
 * Geography endpoint clients: segment rollups and the state -> county -> ZIP
 * drill, plus the assigned-vs-unattended overlay for one drill level.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type {
  CountyRollupResponse,
  SegmentCode,
  SegmentSummary,
  StateRollupResponse,
  ZipRollupResponse,
} from '../../types';
import type {
  SegmentFilterMode,
  GeoQueryCriteria,
  ZipRollupKey,
  GeoOverlayLevel,
  GeoAssignmentOverlayResponse,
} from '../apiTypes';
import { appendPortfolioCriteria, getJson } from '../apiTransport';

export const geoApi = {
  segments: (
    signal?: AbortSignal,
    segmentCodes?: SegmentCode[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    const params = new URLSearchParams();
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    const qs = params.toString();
    return getJson<SegmentSummary[]>(qs ? `/api/segments?${qs}` : '/api/segments', signal);
  },

  stateRollups: (
    segmentCodes?: string[] | null,
    signal?: AbortSignal,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    // 2026-05-04 (FIX G): when a segment filter is active, fetch the
    // segment-aware per-state counts so the choropleth tooltip + the
    // shading reflect "marketable in <segment>" instead of the cross-
    // segment total. No filter ⇒ the original cross-segment query.
    const params = new URLSearchParams();
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    const qs = params.toString();
    return getJson<StateRollupResponse>(
      qs ? `/api/geo/state-rollups?${qs}` : '/api/geo/state-rollups',
      signal,
    );
  },

  countyRollups: (
    state: string,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    const params = new URLSearchParams();
    params.set('state', state.toUpperCase());
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    return getJson<CountyRollupResponse>(
      `/api/geo/county-rollups?${params.toString()}`,
      signal,
    );
  },

  /**
   * Per-ZIP rollups for exactly one geography key.
   *
   * `{ state }` is the live drill: the Cotality share carries a single
   * county FIPS per state, so `county_fips_5` is NULL across gold and a
   * `{ countyFips }` read returns [] for every county today. The FIPS key
   * stays on the contract for a future licensed county dataset. The union
   * type makes "both keys" a compile error, mirroring the API's 422.
   */
  zipRollups: (
    key: ZipRollupKey,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => {
    const params = new URLSearchParams();
    if (key.state) {
      params.set('state', key.state.toUpperCase());
    } else if (key.countyFips) {
      params.set('county_fips', key.countyFips);
    }
    if (segmentCodes && segmentCodes.length > 0) {
      params.set('segment_codes', segmentCodes.join(','));
      params.set('segment_mode', segmentMode);
    }
    appendPortfolioCriteria(params, portfolioCriteria);
    return getJson<ZipRollupResponse>(
      `/api/geo/zip-rollups?${params.toString()}`,
      signal,
    );
  },

  /**
   * S9 assigned-vs-unattended overlay for one drill level. Follows the
   * stateRollups/countyRollups/zipRollups pattern: 422 when level=county
   * without state or level=zip without countyFips; a transient 503 flows
   * through the same retry/degraded-state path as every other geo read.
   */
  assignmentOverlay: (
    level: GeoOverlayLevel,
    opts: { state?: string | null; countyFips?: string | null; signal?: AbortSignal } = {},
  ) => {
    const params = new URLSearchParams();
    params.set('level', level);
    if (opts.state) params.set('state', opts.state.toUpperCase());
    if (opts.countyFips) params.set('county_fips', opts.countyFips);
    return getJson<GeoAssignmentOverlayResponse>(
      `/api/geo/assignment-overlay?${params.toString()}`,
      opts.signal,
    );
  },
};
