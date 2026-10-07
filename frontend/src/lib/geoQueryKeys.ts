import { api } from './api';
import type { GeoOverlayLevel } from './api';
import { queryKeys } from './queryKeys';
import type { StateRollup } from '../types';

/**
 * The geography map's rollup keys and its state-rollups read, moved out of
 * components/mortgage/useChoroplethLiveFacts.ts (which imports and re-exports
 * them) so the route-data prefetch (lib/routeDataPrefetch, audit delivery-03)
 * can start Home's national state rollups without importing the map's lazy
 * chunk into the initial one. Behaviour is unchanged.
 */

export type GeoCriteria = Record<string, string | number | null | undefined>;

/** Geography rollup keys under the app's `mip` root (never collide with other reads). */
export const geoQueryKeys = {
  stateRollups: (cohort: readonly unknown[]) => [...queryKeys.all, 'geo', 'state-rollups', ...cohort] as const,
  zipRollups: (state: string, cohort: readonly unknown[]) =>
    [...queryKeys.all, 'geo', 'zip-rollups', state, ...cohort] as const,
  assignmentOverlay: (level: GeoOverlayLevel, state: string | null) =>
    [...queryKeys.all, 'geo', 'assignment-overlay', level, state ?? ''] as const,
  rateSensitivity: () => [...queryKeys.all, 'geo', 'rate-sensitivity'] as const,
};

/**
 * The cohort half of every rollup key, built from the values the fetchers
 * read. Without a segment filter the API ignores segment_mode, so it is
 * normalised to `any` and Home's national read shares one entry:
 * `['', 'any', '{}']`.
 */
export function rollupCohort(segments: string[] | null, mode: 'any' | 'all', portfolioCriteria?: GeoCriteria) {
  return [segments?.join(',') ?? '', mode, JSON.stringify(portfolioCriteria ?? {})] as const;
}

/** The state rollups as the map reads them, with the age of a value the server retained (delivery-06). */
export interface StateRollupsByCode {
  /** Keyed by lowercase USPS code, the map's location ids. */
  byCode: Record<string, StateRollup>;
  /** `X-Data-Last-Good-At` of the 2xx response that carried them; null when current. */
  lastGoodAt: string | null;
}

/**
 * The per-state rollups, keyed by lowercase USPS code to match the map's
 * location ids. The route-data prefetch stores this SAME shape under the
 * same key (routeDataPrefetch.test.tsx pins the parity).
 */
export function requestStateRollupsByCode(
  segments: string[] | null,
  mode: 'any' | 'all',
  portfolioCriteria: GeoCriteria | undefined,
  signal?: AbortSignal,
): Promise<StateRollupsByCode> {
  return api.stateRollupsWithFreshness(segments, signal, mode, portfolioCriteria).then(({ data, lastGoodAt }) => {
    const byCode: Record<string, StateRollup> = {};
    for (const rollup of data.rollups) byCode[rollup.state.toLowerCase()] = rollup;
    return { byCode, lastGoodAt };
  });
}
