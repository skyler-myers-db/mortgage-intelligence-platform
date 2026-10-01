/**
 * Geography endpoint clients: segment rollups and the state -> county -> ZIP
 * drill, plus the assigned-vs-unattended overlay for one drill level.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 *
 * Audit delivery-06 (client half): each rollup read has one path builder,
 * shared by its plain member and its `*WithFreshness` twin, which also reads
 * `X-Data-Last-Good-At` (apiClients/headers) and returns `Fresh<T>`. A
 * non-2xx response rejects before any header is read.
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
import { appendPortfolioCriteria, getJson, getJsonWithHeaders } from '../apiTransport';
import { lastGoodAtHeader, type Fresh } from './headers';

/** The cohort half of a rollup query (segment filter, then portfolio criteria), appended to `params`. */
function cohortQuery(
  params: URLSearchParams,
  segmentCodes: readonly string[] | null | undefined,
  segmentMode: SegmentFilterMode,
  portfolioCriteria: GeoQueryCriteria | null | undefined,
): string {
  if (segmentCodes && segmentCodes.length > 0) {
    params.set('segment_codes', segmentCodes.join(','));
    params.set('segment_mode', segmentMode);
  }
  appendPortfolioCriteria(params, portfolioCriteria);
  return params.toString();
}

function segmentsPath(
  segmentCodes: readonly string[] | null | undefined,
  segmentMode: SegmentFilterMode,
  portfolioCriteria: GeoQueryCriteria | null | undefined,
): string {
  const qs = cohortQuery(new URLSearchParams(), segmentCodes, segmentMode, portfolioCriteria);
  return qs ? `/api/segments?${qs}` : '/api/segments';
}

function stateRollupsPath(
  segmentCodes: readonly string[] | null | undefined,
  segmentMode: SegmentFilterMode,
  portfolioCriteria: GeoQueryCriteria | null | undefined,
): string {
  // 2026-05-04 (FIX G): when a segment filter is active, fetch the
  // segment-aware per-state counts so the choropleth tooltip + the
  // shading reflect "marketable in <segment>" instead of the cross-
  // segment total. No filter ⇒ the original cross-segment query.
  const qs = cohortQuery(new URLSearchParams(), segmentCodes, segmentMode, portfolioCriteria);
  return qs ? `/api/geo/state-rollups?${qs}` : '/api/geo/state-rollups';
}

function countyRollupsPath(
  state: string,
  segmentCodes: readonly string[] | null | undefined,
  segmentMode: SegmentFilterMode,
  portfolioCriteria: GeoQueryCriteria | null | undefined,
): string {
  const params = new URLSearchParams();
  params.set('state', state.toUpperCase());
  return `/api/geo/county-rollups?${cohortQuery(params, segmentCodes, segmentMode, portfolioCriteria)}`;
}

/**
 * Per-ZIP rollups for exactly one geography key.
 *
 * `{ state }` is the live drill: the Cotality share carries a single
 * county FIPS per state, so `county_fips_5` is NULL across gold and a
 * `{ countyFips }` read returns [] for every county today. The FIPS key
 * stays on the contract for a future licensed county dataset. The union
 * type makes "both keys" a compile error, mirroring the API's 422.
 */
function zipRollupsPath(
  key: ZipRollupKey,
  segmentCodes: readonly string[] | null | undefined,
  segmentMode: SegmentFilterMode,
  portfolioCriteria: GeoQueryCriteria | null | undefined,
): string {
  const params = new URLSearchParams();
  if (key.state) {
    params.set('state', key.state.toUpperCase());
  } else if (key.countyFips) {
    params.set('county_fips', key.countyFips);
  }
  return `/api/geo/zip-rollups?${cohortQuery(params, segmentCodes, segmentMode, portfolioCriteria)}`;
}

/** A 2xx read with its retained-value marker; a non-2xx never gets here. */
function withFreshness<T>(read: Promise<{ data: T; headers: Headers }>): Promise<Fresh<T>> {
  return read.then(({ data, headers }) => ({ data, lastGoodAt: lastGoodAtHeader(headers) }));
}

export const geoApi = {
  segments: (
    signal?: AbortSignal,
    segmentCodes?: SegmentCode[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => getJson<SegmentSummary[]>(segmentsPath(segmentCodes, segmentMode, portfolioCriteria), signal),

  stateRollups: (
    segmentCodes?: string[] | null,
    signal?: AbortSignal,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => getJson<StateRollupResponse>(stateRollupsPath(segmentCodes, segmentMode, portfolioCriteria), signal),

  countyRollups: (
    state: string,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => getJson<CountyRollupResponse>(countyRollupsPath(state, segmentCodes, segmentMode, portfolioCriteria), signal),

  zipRollups: (
    key: ZipRollupKey,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => getJson<ZipRollupResponse>(zipRollupsPath(key, segmentCodes, segmentMode, portfolioCriteria), signal),

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

  // delivery-06: the same reads with the age of a retained value (same signatures, same URLs).
  segmentsWithFreshness: (
    signal?: AbortSignal,
    segmentCodes?: SegmentCode[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => withFreshness(getJsonWithHeaders<SegmentSummary[]>(segmentsPath(segmentCodes, segmentMode, portfolioCriteria), signal)),

  stateRollupsWithFreshness: (
    segmentCodes?: string[] | null,
    signal?: AbortSignal,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => withFreshness(getJsonWithHeaders<StateRollupResponse>(stateRollupsPath(segmentCodes, segmentMode, portfolioCriteria), signal)),

  countyRollupsWithFreshness: (
    state: string,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => withFreshness(
    getJsonWithHeaders<CountyRollupResponse>(countyRollupsPath(state, segmentCodes, segmentMode, portfolioCriteria), signal),
  ),

  zipRollupsWithFreshness: (
    key: ZipRollupKey,
    signal?: AbortSignal,
    segmentCodes?: string[] | null,
    segmentMode: SegmentFilterMode = 'any',
    portfolioCriteria?: GeoQueryCriteria | null,
  ) => withFreshness(getJsonWithHeaders<ZipRollupResponse>(zipRollupsPath(key, segmentCodes, segmentMode, portfolioCriteria), signal)),
};
