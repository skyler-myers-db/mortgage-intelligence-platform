/**
 * The ZIP-stage seam of the geography map (W5c, audit dataviz-01;
 * deviation:zcta-level). Types only: nothing here reaches a bundle.
 *
 * A host that draws the ZIP level as something other than the densest-ZIP
 * tiles hands the map a ZipStage (Segment Intelligence: ZIP_AREAS, the
 * committed Census ZCTA polygons). The map renders `Stage` in place of the
 * tiles, follows the view it reports (the legend's scale and caption, the
 * header's zoom buttons, the busy flag) and calls `warm` when the national
 * stage is pointed at. A host without one (Home) keeps the tiles, and none of
 * the polygon code or geometry is in its closure.
 */
import type { ComponentType, ReactNode } from 'react';
import type { USChoroplethMapZipLevelProps } from './USChoroplethMapZipLevel';

/** What a ZIP stage has on screen, reported to the map. */
export interface ZipStageView {
  /** ZCTA polygons are drawn: the scale covers every populated ZIP, no densest scope. */
  polygons: boolean;
  /** The stage is still loading its chunk or its geometry. */
  busy: boolean;
  /** The header's zoom buttons, or null. */
  controls: ReactNode;
  /** The legend's extra caption clause, or null. */
  caption: string | null;
  /** A line for the ZIP hover card, or null. */
  cardNote: string | null;
}

/** The tile props plus what a polygon stage needs from the map. */
export interface ZipStageProps extends USChoroplethMapZipLevelProps {
  /** Uppercase USPS code of the drilled state. */
  usps: string;
  /** The national viewBox (the drill tween's first frame). */
  nationalViewBox: string;
  onView: (view: ZipStageView) => void;
}

export interface ZipStage {
  Stage: ComponentType<ZipStageProps>;
  /** Warm the stage's code (never its data) before a drill. */
  warm: () => void;
}
