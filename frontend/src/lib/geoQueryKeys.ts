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

/** The per-state rollups, keyed by lowercase USPS code to match the map's location ids. */
export function requestStateRollupsByCode(
  segments: string[] | null,
  mode: 'any' | 'all',
  portfolioCriteria: GeoCriteria | undefined,
  signal?: AbortSignal,
): Promise<Record<string, StateRollup>> {
  return api.stateRollups(segments, signal, mode, portfolioCriteria).then((payload) => {
    const byCode: Record<string, StateRollup> = {};
    for (const rollup of payload.rollups) byCode[rollup.state.toLowerCase()] = rollup;
    return byCode;
  });
}
