/**
 * MapUnavailable — the stage the geography map shows instead of the fill when
 * the rollup its level needs is down (audit dataviz-04): the shared
 * WarmingUpBlock while a 503 retry loop runs, then a "could not load" card
 * with Retry. Never an empty country or a confident zero. Its own module so
 * USChoroplethMap.tsx holds exactly one component (the React Compiler
 * coverage control in USChoroplethMap.compiler.test.ts reads the file).
 */
import type { ReactNode } from 'react';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import type { GeoRead } from './useChoroplethLiveFacts';

/** The stage shown instead of the map when the rollup this level needs is down. */
export function MapUnavailable({ read, what, fallback }: { read: GeoRead<unknown>; what: string; fallback?: ReactNode }) {
  if (read.warmingUp) {
    return (
      <div className="map-stage map-stage--status">
        <WarmingUpBlock state={read.warmingUp} title={what} compact />
      </div>
    );
  }
  return (
    <div className="map-stage map-stage--status">
      <div className="map-center-card">
        <div className="text-2">{what} could not load.</div>
        <div className="map-center-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={read.retry}>
            Retry
          </button>
          {fallback}
        </div>
      </div>
    </div>
  );
}
