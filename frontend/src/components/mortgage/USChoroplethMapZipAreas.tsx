/**
 * USChoroplethMapZipAreas — Segment Intelligence's ZIP stage (W5c, audit
 * dataviz-01 / visual-09 / motion-10; deviation:zcta-level): a map-view drill
 * draws the drilled state's committed Census ZCTAs (the lazy
 * USChoroplethMapZctaLevel rung) instead of the densest-ZIP tiles.
 *
 * Only the Segment Intelligence route imports this module, so Home's closure
 * carries none of it: Home keeps the tiles and never loads the rung or any
 * geometry. The rung's chunk loads on the drill (and is warmed, code only,
 * when the national stage is pointed at). The tiles are the degraded
 * fallback, never a mock: the rung's chunk fails, or the rung reports
 * 'tiles' (no committed file for the state, or a geometry read that failed
 * its retries), and the unchanged tiles render under a status line. A state
 * with an empty ZIP rollup is not a fallback: the tiles' empty card renders
 * as it does on Home, with no status line and no rung or geometry load. The view
 * reported to the map makes the legend's scale and caption, the header's zoom
 * buttons, the ZIP card's line and the busy flag follow what is on screen.
 */
import { useLayoutEffect, useState, type ReactNode } from 'react';
import { preloadBestEffort } from '../../lib/lazyPreload';
import { claimDrillFocus, moveRovingFocus, zipAriaLabel } from './USChoroplethMap.a11y';
import type { ZipStage, ZipStageProps } from './USChoroplethMap.zipStage';
import type { ZctaKit, ZipRung } from './USChoroplethMapZctaLevel';
import { USChoroplethMapZipLevel } from './USChoroplethMapZipLevel';
import { useLazyModule } from './useLazyModule';
import { ZCTA_LEVEL } from './zctaLevel.lazy';
import './USChoroplethMapZipAreas.css';

/** The legend's clause while ZCTAs are drawn. */
export const ZCTA_CAPTION = 'ZIP areas are Census 2020 ZCTAs, an approximation of USPS delivery areas';
/** The tiles' status line when they stand in for the ZCTA polygons. */
export const ZCTA_TILES_STATUS = 'ZIP boundaries could not load; showing the densest ZIPs as tiles.';

/** The map's helpers the rung reuses without importing the map's modules. */
const KIT: ZctaKit = { claimDrillFocus, moveRovingFocus, zipAriaLabel };

function ZipAreasStage({ onView, usps, nationalViewBox, ...tiles }: ZipStageProps) {
  // An empty ZIP rollup has no area to draw: the tiles' own empty card shows
  // unchanged (its "Open Lead Queue for {State}" action is a keyboard drill's
  // focus target), with no status line, and no rung chunk or geometry loads.
  const hasRows = Object.keys(tiles.byZip).length > 0;
  const chunk = useLazyModule(ZCTA_LEVEL, hasRows);
  const [rung, setRung] = useState<ZipRung | null>(null);
  const [controls, setControls] = useState<ReactNode>(null);
  const tilesOnly = chunk.failed || rung === 'tiles';
  const polygons = hasRows && !tilesOnly && rung === 'polygons';
  const busy = hasRows && !tilesOnly && !polygons;
  const shownControls = polygons ? controls : null;
  // A cross-line ZCTA is drawn in each state it touches; its counts are this state's.
  const cardNote = polygons ? `Counts are ${tiles.drillStateName} borrowers in this ZIP area.` : null;
  useLayoutEffect(() => {
    onView({ polygons, busy, controls: shownControls, caption: polygons ? ZCTA_CAPTION : null, cardNote });
  }, [busy, cardNote, onView, polygons, shownControls]);
  if (!hasRows) return <USChoroplethMapZipLevel {...tiles} />;
  if (tilesOnly) {
    return (
      <>
        <p className="zip-tiles__status text-2" role="status">{ZCTA_TILES_STATUS}</p>
        <USChoroplethMapZipLevel {...tiles} />
      </>
    );
  }
  const Rung = chunk.module?.USChoroplethMapZctaLevel ?? null;
  if (!Rung) return <div className="map-stage map-stage--empty map-stage--zcta">Loading ZIP areas…</div>;
  return (
    <Rung {...tiles} usps={usps} nationalViewBox={nationalViewBox} kit={KIT} onRung={setRung} onZoomControls={setControls} />
  );
}

export const ZIP_AREAS: ZipStage = {
  Stage: ZipAreasStage,
  warm: () => preloadBestEffort(ZCTA_LEVEL.load),
};
