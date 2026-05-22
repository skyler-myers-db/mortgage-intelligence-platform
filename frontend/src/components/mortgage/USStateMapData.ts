import type { FeatureCollection } from 'geojson';
import { buildUsaStateMapPayload, type UsaSvgMap } from './USChoroplethMap.utils';

let stateMapPromise: Promise<UsaSvgMap> | null = null;

export function loadUsaStateMap(): Promise<UsaSvgMap> {
  stateMapPromise ??= Promise.all([
    import('topojson-client'),
    import('us-atlas/states-albers-10m.json'),
  ]).then(([topoClient, topologyModule]) => {
    const topology = topologyModule.default as {
      objects: { states: unknown };
    };
    const fc = topoClient.feature(
      topology as never,
      topology.objects.states as never,
    ) as unknown as FeatureCollection;
    return buildUsaStateMapPayload(fc);
  });
  return stateMapPromise;
}
