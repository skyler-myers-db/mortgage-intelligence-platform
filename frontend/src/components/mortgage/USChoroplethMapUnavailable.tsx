/**
 * MapUnavailable — the stage the geography map shows instead of the fill when
 * the rollup its level needs is down (audit dataviz-04): the shared
 * WarmingUpBlock while a 503 retry loop runs, then a "could not load" card
 * with Retry. Never an empty country or a confident zero. Its own module so
 * USChoroplethMap.tsx holds exactly one component (the React Compiler
 * coverage control in USChoroplethMap.compiler.test.ts reads the file).
 *
 * Keyboard drill (audit a11y-04): when a keyboard drill lands here, focus
 * parks on the stage while it warms up, or on Retry once the read failed,
 * instead of falling to <body>. That is not the drill's final target, so the
 * request stays open: when the rollup arrives the ZIP tiles (or the table)
 * take focus from here.
 */
import { useEffect, useRef, type ReactNode } from 'react';
import { WarmingUpBlock } from '../ui/WarmingUpBlock';
import { claimDrillFocus } from './USChoroplethMap.a11y';
import type { GeoRead } from './useChoroplethLiveFacts';

interface MapUnavailableProps {
  read: GeoRead<unknown>;
  what: string;
  fallback?: ReactNode;
  /** A keyboard drill is waiting on this read: park focus here meanwhile. */
  autoFocus?: boolean;
}

/** The stage shown instead of the map when the rollup this level needs is down. */
export function MapUnavailable({ read, what, fallback, autoFocus = false }: MapUnavailableProps) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const retryRef = useRef<HTMLButtonElement | null>(null);
  const warming = read.warmingUp !== null;
  useEffect(() => {
    if (!autoFocus) return;
    // Warming -> failed keeps this stage mounted: focus parked on it moves on to Retry.
    claimDrillFocus(warming ? stageRef.current : retryRef.current, stageRef.current);
  }, [autoFocus, warming]);

  return (
    <div ref={stageRef} className="map-stage map-stage--status" role="group" aria-label={what} tabIndex={-1}>
      {read.warmingUp ? (
        <WarmingUpBlock state={read.warmingUp} title={what} compact />
      ) : (
        <div className="map-center-card">
          <div className="text-2">{what} could not load.</div>
          <div className="map-center-actions">
            <button ref={retryRef} type="button" className="btn btn--ghost btn--sm" onClick={read.retry}>
              Retry
            </button>
            {fallback}
          </div>
        </div>
      )}
    </div>
  );
}
