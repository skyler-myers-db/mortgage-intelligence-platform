/**
 * Where a state's USPS label sits on the geography map (audit dataviz-02:
 * "label populated states").
 *
 * The anchor is the area centroid of the state's largest polygon, so a label
 * lands on the mainland rather than between islands. A centroid can sit on
 * or near the state's own edge, though: Florida's lies 4 viewBox units from
 * the Georgia line, and Louisiana's 6.5 units from its edge. For those
 * states the anchor moves to the pole of inaccessibility (the interior point
 * farthest from any edge), found by a coarse-to-fine grid search. Only
 * states whose centroid lacks clearance pay for the search: about a dozen,
 * mostly small, at load time.
 */
import type { Geometry, Position } from 'geojson';

/** Clearance (viewBox units, about a two-letter label's half-width) below which the centroid is replaced. */
export const LABEL_CLEARANCE = 12;
const GRID = 12;
const ROUNDS = 3;

function segmentDistanceSq(px: number, py: number, a: Position, b: Position): number {
  let [x, y] = a;
  const dx = b[0] - x;
  const dy = b[1] - y;
  if (dx !== 0 || dy !== 0) {
    const t = ((px - x) * dx + (py - y) * dy) / (dx * dx + dy * dy);
    if (t > 1) {
      [x, y] = b;
    } else if (t > 0) {
      x += dx * t;
      y += dy * t;
    }
  }
  return (px - x) ** 2 + (py - y) ** 2;
}

/** Distance from (x, y) to the polygon's nearest edge: positive inside, negative outside. Holes count as outside. */
export function signedEdgeDistance(x: number, y: number, rings: Position[][]): number {
  let inside = false;
  let minSq = Infinity;
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const a = ring[i];
      const b = ring[j];
      if ((a[1] > y) !== (b[1] > y) && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
      minSq = Math.min(minSq, segmentDistanceSq(x, y, a, b));
    }
  }
  return (inside ? 1 : -1) * Math.sqrt(minSq);
}

/** The grid point of the polygon farthest inside it, refined around the best point each round. */
function poleOfInaccessibility(rings: Position[][]): { x: number; y: number; clearance: number } {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const [x, y] of rings[0]) {
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x);
    y1 = Math.max(y1, y);
  }
  let best = { x: (x0 + x1) / 2, y: (y0 + y1) / 2, clearance: -Infinity };
  let width = x1 - x0;
  let height = y1 - y0;
  for (let round = 0; round < ROUNDS; round += 1) {
    const cx = best.x;
    const cy = best.y;
    for (let i = 0; i <= GRID; i += 1) {
      for (let j = 0; j <= GRID; j += 1) {
        const x = cx - width / 2 + (width * i) / GRID;
        const y = cy - height / 2 + (height * j) / GRID;
        const clearance = signedEdgeDistance(x, y, rings);
        if (clearance > best.clearance) best = { x, y, clearance };
      }
    }
    // Next round: a window four cells wide around the best point.
    width = (width / GRID) * 4;
    height = (height / GRID) * 4;
  }
  return best;
}

/**
 * Label anchor for a state geometry: the largest polygon's area centroid, or
 * its pole of inaccessibility when the centroid is closer than
 * LABEL_CLEARANCE to an edge (or outside the polygon). Null for a degenerate
 * or non-polygon geometry.
 */
export function labelAnchor(geom: Geometry): [number, number] | null {
  const polygons = geom.type === 'Polygon'
    ? [geom.coordinates]
    : geom.type === 'MultiPolygon'
      ? geom.coordinates
      : [];
  let best: { area: number; x: number; y: number; rings: Position[][] } | null = null;
  for (const rings of polygons) {
    const ring = rings[0];
    if (!ring || ring.length < 3) continue;
    let twiceArea = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0; i < ring.length; i += 1) {
      const [x0, y0] = ring[i];
      const [x1, y1] = ring[(i + 1) % ring.length];
      const cross = x0 * y1 - x1 * y0;
      twiceArea += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    if (twiceArea === 0) continue;
    const area = Math.abs(twiceArea / 2);
    if (!best || area > best.area) best = { area, x: cx / (3 * twiceArea), y: cy / (3 * twiceArea), rings };
  }
  if (!best) return null;
  let at: [number, number] = [best.x, best.y];
  const centroidClearance = signedEdgeDistance(best.x, best.y, best.rings);
  if (centroidClearance < LABEL_CLEARANCE) {
    const pole = poleOfInaccessibility(best.rings);
    if (pole.clearance > centroidClearance) at = [pole.x, pole.y];
  }
  return [Number(at[0].toFixed(1)), Number(at[1].toFixed(1))];
}

/** Clearance of `at` inside the geometry's largest polygon (tests and diagnostics). */
export function labelClearance(geom: Geometry, at: [number, number]): number {
  const polygons = geom.type === 'Polygon' ? [geom.coordinates] : geom.type === 'MultiPolygon' ? geom.coordinates : [];
  let best: Position[][] | null = null;
  let bestArea = -1;
  for (const rings of polygons) {
    const ring = rings[0] ?? [];
    let twiceArea = 0;
    for (let i = 0; i < ring.length; i += 1) {
      const [x0, y0] = ring[i];
      const [x1, y1] = ring[(i + 1) % ring.length];
      twiceArea += x0 * y1 - x1 * y0;
    }
    if (Math.abs(twiceArea) > bestArea) {
      bestArea = Math.abs(twiceArea);
      best = rings;
    }
  }
  return best ? signedEdgeDistance(at[0], at[1], best) : -Infinity;
}
