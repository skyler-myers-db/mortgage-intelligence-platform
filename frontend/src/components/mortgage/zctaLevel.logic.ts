/**
 * Pure helpers of the ZCTA rung (audit dataviz-01; deviation:zcta-level): the
 * fitted view, the label choice and the reconcile counts. Lazy-only, like
 * the rung that imports them.
 */
import type { ZipRollup } from '../../types';
import type { ZctaArea, ZctaBox, ZctaGeometry } from './zctaGeometry';
import type { ViewBox } from './useSvgViewBox';

/** A label is drawn only on a populated ZCTA at least this wide on screen. */
export const LABEL_MIN_SCREEN_PX = 36;

export const isPopulated = (rollup: ZipRollup | undefined) => (rollup?.addressable_borrowers ?? 0) > 0;

export const boxView = ([x0, y0, x1, y1]: ZctaBox): ViewBox => ({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 });

/** "x y w h" (a viewBox attribute) as a ViewBox; null when it does not parse. */
export function parseViewBox(text: string): ViewBox | null {
  const [x, y, w, h] = text.trim().split(/[\s,]+/).map(Number);
  return [x, y, w, h].every(Number.isFinite) && w > 0 && h > 0 ? { x, y, w, h } : null;
}

/**
 * The drill's first view: the populated ZCTAs' box plus 10% (5% a side), at
 * least a sixth of the state's box on each axis (centred), never larger than
 * the state's box and kept inside it. The state's box when nothing is
 * populated. Live coverage is a fraction of a state's ZIPs, so a statewide
 * view would read as mostly empty polygons (the county rung's failure).
 */
export function fitZctaView(areas: readonly ZctaArea[], byZip: Record<string, ZipRollup>, stateBox: ZctaBox): ViewBox {
  const state = boxView(stateBox);
  let box: [number, number, number, number] | null = null;
  for (const area of areas) {
    if (!isPopulated(byZip[area.zip])) continue;
    const [x0, y0, x1, y1] = area.box;
    box = box ? [Math.min(box[0], x0), Math.min(box[1], y0), Math.max(box[2], x1), Math.max(box[3], y1)] : [x0, y0, x1, y1];
  }
  if (!box) return state;
  const axis = (low: number, high: number, stateLow: number, stateSize: number): [number, number] => {
    const size = Math.min(stateSize, Math.max((high - low) * 1.1, stateSize / 6));
    const start = Math.min(stateLow + stateSize - size, Math.max(stateLow, (low + high) / 2 - size / 2));
    return [start, size];
  };
  const [x, w] = axis(box[0], box[2], state.x, state.w);
  const [y, h] = axis(box[1], box[3], state.y, state.h);
  return { x, y, w, h };
}

/** Screen pixels per viewBox unit for a `meet` svg of `width` x `height` px showing `view`. */
export const screenScale = (view: ViewBox, width: number, height: number) =>
  Math.min(width / view.w, height / view.h);

/** The populated ZCTAs wide enough on screen for a label, at `scale` px per unit. */
export function labelledZips(areas: readonly ZctaArea[], byZip: Record<string, ZipRollup>, scale: number): ZctaArea[] {
  if (!(scale > 0)) return [];
  return areas.filter(
    (area) => area.labelAt !== null && isPopulated(byZip[area.zip]) && (area.box[2] - area.box[0]) * scale >= LABEL_MIN_SCREEN_PX,
  );
}

/** Rollup ZIPs with borrowers that have no ZCTA polygon (PO-box and unique ZIPs): how many, and their borrowers. */
export function zipsWithoutBoundary(byZip: Record<string, ZipRollup>, geometry: ZctaGeometry): { zips: number; borrowers: number } {
  let zips = 0;
  let borrowers = 0;
  for (const rollup of Object.values(byZip)) {
    if (!isPopulated(rollup) || geometry.byZip.has(rollup.zip)) continue;
    zips += 1;
    borrowers += rollup.addressable_borrowers ?? 0;
  }
  return { zips, borrowers };
}
