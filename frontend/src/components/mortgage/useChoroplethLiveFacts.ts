/**
 * useChoroplethLiveFacts — every network-backed fact the geography map
 * renders: the lazily-imported state topology, the per-state rollups (keyed
 * on segment filter / mode / portfolio criteria), the drilled state's ZIP
 * rollups, and the S9 assigned-vs-unattended overlay.
 *
 * Audit dataviz-04 (2026-09-21): the rollups used raw effects whose `.catch`
 * turned a cold warehouse into an empty map, a confident "0" and no retry.
 * They now run through `useWarmingUpRetry`, the app's cold-start loop, on
 * `mip/geo/...` query keys, so Home and Segment Intelligence share one cache
 * entry per cohort and a 503 `warming_up` keeps retrying with a visible
 * WarmingUpBlock. Unknown stays unknown (`null`), never `{}` or zero.
 *
 * Every read here is an aggregate over gold tables or Lakebase assignment
 * counts (`backend/api/geo.py`); none writes an audit row, so the default
 * refetch behaviour cannot inflate governance evidence.
 *
 * Audit runtime-06 (map slice): a changed cohort keeps the previous fill up
 * as a placeholder instead of blanking the hero to is-loading. The state
 * rollups keep the previous payload; a drilled state's ZIP rollups keep it
 * only when it was read for the SAME state, so one state's tiles never paint
 * under another. `updating` says a placeholder is on screen (the map labels
 * it), and a final error ends the placeholder: the previous cohort is never
 * presented as current after a failure.
 *
 * The Rate Lever read (audit wow-stage-1) is enabled only while the rate
 * colouring is the effective mode: never on load, never prefetched.
 */

import { useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../../lib/api';
import type { GeoAssignmentOverlayResponse, GeoOverlayLevel } from '../../lib/api';
import { queryKeys } from '../../lib/queryKeys';
import {
  useWarmingUpRetry,
  type UseWarmingUpRetryResult,
  type WarmingUpState,
} from '../../lib/useWarmingUpRetry';
import type { StateRollup, ZipRollup } from '../../types';
import type { RateSensitivityResponse } from '../../types/rateScenario';
import { rateScenarioApi } from '../../lib/apiClients/rateScenario';
import type { UsaSvgMap } from './USChoroplethMap.utils';
import { loadUsaStateMap } from './USStateMapData';

type Criteria = Record<string, string | number | null | undefined>;

/** Geography rollup keys under the app's `mip` root (never collide with other reads). */
export const geoQueryKeys = {
  stateRollups: (cohort: readonly unknown[]) => [...queryKeys.all, 'geo', 'state-rollups', ...cohort] as const,
  zipRollups: (state: string, cohort: readonly unknown[]) =>
    [...queryKeys.all, 'geo', 'zip-rollups', state, ...cohort] as const,
  assignmentOverlay: (level: GeoOverlayLevel, state: string | null) =>
    [...queryKeys.all, 'geo', 'assignment-overlay', level, state ?? ''] as const,
  rateSensitivity: () => [...queryKeys.all, 'geo', 'rate-sensitivity'] as const,
};

/** Where the drilled state sits in a `zipRollups` key. */
const ZIP_KEY_STATE_AT = geoQueryKeys.zipRollups('', []).length - 1;

/** One rollup read as the map renders it. `data === null` means unknown. */
export interface GeoRead<T> {
  data: T | null;
  /** Non-null while a 503 retry loop is running (warehouse warming, breaker cooling). */
  warmingUp: WarmingUpState | null;
  /** Non-null once the read failed for good (retries exhausted or non-retryable). */
  error: Error | null;
  /** True for the first load of this key (no data, no warming, no error yet). */
  loading: boolean;
  /** True while `data` is the previous key's payload shown as a placeholder (runtime-06). */
  updating: boolean;
  retry: () => void;
}

export interface UseChoroplethLiveFactsInput {
  /** Uppercase USPS code of the drilled state, or null at the national view. */
  drillState: string | null;
  segmentFilter?: string[];
  segmentFilterMode: 'any' | 'all';
  portfolioCriteria?: Criteria;
  /** Whether the "Unattended leads" overlay is on (the overlay is only read then). */
  overlayOn: boolean;
  /** Whether the rate colouring is the effective mode (the rate grid is only read then). */
  rateOn: boolean;
}

function geoRead<T>(result: UseWarmingUpRetryResult<T>, enabled: boolean): GeoRead<T> {
  const data = enabled ? result.data : null;
  const error = enabled && data === null ? result.error : null;
  // Once the retry budget is spent, react-query keeps the last 503 as its
  // `failureReason`, so the hook still reports a warming state ("attempt 6
  // of 6") that nothing will ever retry. A final error ends the loop: the
  // stage shows Retry instead. A manual or health-recovery refetch clears
  // the error while no data exists, so a new loop reads as warming again.
  const warmingUp = enabled && error === null ? result.warmingUp : null;
  return {
    data,
    warmingUp,
    error,
    loading: enabled && data === null && warmingUp === null && error === null,
    // react-query shows a placeholder only while the new key is pending: a
    // final error drops it (data null above), so the error is what renders.
    updating: data !== null && result.isPlaceholderData,
    retry: result.manualRetry,
  };
}

export function useChoroplethLiveFacts({
  drillState,
  segmentFilter,
  segmentFilterMode,
  portfolioCriteria,
  overlayOn,
  rateOn,
}: UseChoroplethLiveFactsInput) {
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);

  // Lazy-load the state geography so the TopoJSON conversion lands in its
  // own code-split chunk instead of the main bundle.
  useEffect(() => {
    let cancelled = false;
    loadUsaStateMap().then((map) => {
      if (!cancelled) setUsaMap(map);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // The cohort half of every rollup key, built from the values the fetchers
  // read. Without a segment filter the API ignores segment_mode, so it is
  // normalised to `any` and Home's national read shares one entry.
  const segments = useMemo(
    () => (segmentFilter && segmentFilter.length > 0 ? segmentFilter : null),
    [segmentFilter],
  );
  const mode = segments ? segmentFilterMode : 'any';
  const cohort = useMemo(
    () => [segments?.join(',') ?? '', mode, JSON.stringify(portfolioCriteria ?? {})] as const,
    [segments, mode, portfolioCriteria],
  );

  const stateResult = useWarmingUpRetry<Record<string, StateRollup>>(
    (signal) =>
      api.stateRollups(segments, signal, mode, portfolioCriteria).then((payload) => {
        // Keyed by lowercase USPS code to match the map's location ids.
        const byCode: Record<string, StateRollup> = {};
        for (const rollup of payload.rollups) byCode[rollup.state.toLowerCase()] = rollup;
        return byCode;
      }),
    { queryKey: geoQueryKeys.stateRollups(cohort), keepPreviousData: true },
  );

  const zipEnabled = drillState !== null;
  const zipResult = useWarmingUpRetry<Record<string, ZipRollup>>(
    (signal) =>
      api.zipRollups({ state: drillState ?? '' }, signal, segments, mode, portfolioCriteria).then((payload) => {
        const byZip: Record<string, ZipRollup> = {};
        for (const rollup of payload.rollups) byZip[rollup.zip] = rollup;
        return byZip;
      }),
    {
      queryKey: geoQueryKeys.zipRollups(drillState ?? '', cohort),
      enabled: zipEnabled,
      keepPreviousWhen: (previousKey) => previousKey[ZIP_KEY_STATE_AT] === drillState,
    },
  );

  // The overlay keys on the same unit as the fill it recolours: states at the
  // national view, the drilled state's ZIPs below it. It counts live Lakebase
  // assignments, which change under the user (a lead assigned in the queue),
  // and invalidateOperationalQueries does not cover `geo`: staleTime 0 makes
  // every toggle on read it again, as the pre-query-cache effect did. The
  // cached payload still paints meanwhile. An aggregate read: no audit row.
  const overlayLevel: GeoOverlayLevel = drillState ? 'zip' : 'state';
  const overlayResult = useWarmingUpRetry<GeoAssignmentOverlayResponse>(
    (signal) => api.assignmentOverlay(overlayLevel, { state: drillState, signal }),
    { queryKey: geoQueryKeys.assignmentOverlay(overlayLevel, drillState), enabled: overlayOn, staleTime: 0 },
  );
  const overlay = geoRead(overlayResult, overlayOn);
  // The whole book, keyed once: an aggregate over gold plus a live
  // contactable count, never an audit row, and only in rate mode.
  const rateResult = useWarmingUpRetry<RateSensitivityResponse>(
    (signal) => rateScenarioApi.rateSensitivity(signal),
    { queryKey: geoQueryKeys.rateSensitivity(), enabled: rateOn },
  );
  const overlayDependency = overlay.error instanceof ApiError && overlay.error.dependency
    ? ` (${overlay.error.dependency})`
    : '';

  return {
    usaMap,
    states: geoRead(stateResult, true),
    zips: geoRead(zipResult, zipEnabled),
    overlayData: overlay.data,
    // A failed overlay is an honest degraded note in the legend; the base
    // borrower view stays up — never a silent fallback.
    overlayError: overlay.error ? `Coverage overlay unavailable${overlayDependency}. Showing borrower counts.` : null,
    overlayLoading: overlay.loading || overlay.warmingUp !== null,
    rate: geoRead(rateResult, rateOn),
  };
}
