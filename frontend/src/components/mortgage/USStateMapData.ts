import type { FeatureCollection } from 'geojson';
import { feature } from 'topojson-client';
import { buildUsaStateMapPayload, type UsaSvgMap } from './USChoroplethMap.utils';

interface StateTopologyModule {
  default: unknown;
}

/**
 * Builds a single-flight loader for the pre-projected state geometry.
 *
 * The promise is shared by every caller (the Home hero map, the Genie answer
 * map, the Home route preloader in lib/routePreloaders.ts), so the geometry is
 * fetched and converted once per page load. A rejected load is dropped so a
 * transient failure during a speculative preload cannot poison the map's own
 * later attempt. Exported so a test can inject the topology import.
 */
export function createUsaStateMapLoader(
  importTopology: () => Promise<StateTopologyModule>,
): () => Promise<UsaSvgMap> {
  let stateMapPromise: Promise<UsaSvgMap> | null = null;
  return () => {
    stateMapPromise ??= importTopology()
      .then((topologyModule) => {
        const topology = topologyModule.default as {
          objects: { states: unknown };
        };
        const fc = feature(
          topology as never,
          topology.objects.states as never,
        ) as unknown as FeatureCollection;
        return buildUsaStateMapPayload(fc);
      })
      .catch((err: unknown) => {
        stateMapPromise = null;
        throw err;
      });
    return stateMapPromise;
  };
}

/**
 * The us-atlas states-albers-10m topology (~80 KiB raw / ~25 KiB br) stays a
 * dynamic import so it is its own hashed, immutable chunk that never enters
 * the initial bundle. `feature` is imported by name (audit bundle-07) so only
 * topojson-client's feature code travels with this module instead of the
 * whole 13-module namespace, and it arrives with the route chunk rather than
 * as a further sequential fetch.
 */
export const loadUsaStateMap = createUsaStateMapLoader(
  () => import('us-atlas/states-albers-10m.json'),
);
