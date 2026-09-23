import type { Feature, FeatureCollection, Geometry } from 'geojson';
import type { CountyRollup } from '../../types';

// Shared shape consumed by the state-level map renderers. The payload is
// built from us-atlas state TopoJSON, keeping IDs in the existing lowercase
// USPS format so rollup lookups and drill links do not change.
export interface UsaSvgMapLocation {
  name: string;
  id: string;
  path: string;
  /** Where the state's label sits (area centroid of its largest polygon), in viewBox units. */
  labelAt?: [number, number];
}
export interface UsaSvgMap { label: string; viewBox: string; locations: UsaSvgMapLocation[] }

// USPS (lowercase) -> FIPS-2 map. Values are intrinsic per-state constants,
// NOT tenant-configurable. The active footprint is filtered at render time.
export const USCODE_TO_FIPS: Record<string, string> = {
  al: '01', ak: '02', az: '04', ar: '05', ca: '06', co: '08', ct: '09',
  de: '10', dc: '11', fl: '12', ga: '13', hi: '15', id: '16', il: '17', in: '18',
  ia: '19', ks: '20', ky: '21', la: '22', me: '23', md: '24', ma: '25',
  mi: '26', mn: '27', ms: '28', mo: '29', mt: '30', ne: '31', nv: '32',
  nh: '33', nj: '34', nm: '35', ny: '36', nc: '37', nd: '38', oh: '39',
  ok: '40', or: '41', pa: '42', ri: '44', sc: '45', sd: '46', tn: '47',
  tx: '48', ut: '49', vt: '50', va: '51', wa: '53', wv: '54', wi: '55',
  wy: '56',
};

export const FIPS_TO_USCODE: Record<string, string> = Object.fromEntries(
  Object.entries(USCODE_TO_FIPS).map(([code, fips]) => [fips, code]),
);

function stateDisplayName(name: string, code: string): string {
  if (code === 'dc') return 'Washington, DC';
  return name;
}

/** Convert us-atlas state features into the map payload used by the UI. */
export function buildUsaStateMapPayload(fc: FeatureCollection, pad = 0): UsaSvgMap {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const locations: UsaSvgMapLocation[] = [];

  for (const feature of fc.features) {
    const fips = String(feature.id ?? '').padStart(2, '0');
    const code = FIPS_TO_USCODE[fips];
    if (!code || !feature.geometry) continue;
    const path = geometryToPath(feature.geometry);
    if (!path) continue;
    const [x0, y0, x1, y1] = featureBBox(feature);
    if (!Number.isFinite(x0) || !Number.isFinite(y0) || !Number.isFinite(x1) || !Number.isFinite(y1)) {
      continue;
    }
    if (x0 < minX) minX = x0;
    if (y0 < minY) minY = y0;
    if (x1 > maxX) maxX = x1;
    if (y1 > maxY) maxY = y1;
    const rawName = (feature.properties as { name?: string } | null)?.name ?? code.toUpperCase();
    locations.push({
      id: code,
      name: stateDisplayName(rawName, code),
      path,
      labelAt: labelAnchor(feature.geometry) ?? undefined,
    });
  }

  locations.sort((a, b) => a.id.localeCompare(b.id));
  if (locations.length === 0) {
    return {
      label: 'United States',
      viewBox: '0 0 1 1',
      locations: [],
    };
  }
  const vx = minX - pad;
  const vy = minY - pad;
  const vw = maxX - minX + pad * 2;
  const vh = maxY - minY + pad * 2;
  return {
    label: 'United States',
    viewBox: `${vx.toFixed(1)} ${vy.toFixed(1)} ${vw.toFixed(1)} ${vh.toFixed(1)}`,
    locations,
  };
}

/**
 * Area centroid of the largest polygon's outer ring (shoelace formula), so a
 * state label lands on the mainland rather than between islands. Null for a
 * degenerate or non-polygon geometry.
 */
export function labelAnchor(geom: Geometry): [number, number] | null {
  const rings = geom.type === 'Polygon'
    ? [geom.coordinates[0]]
    : geom.type === 'MultiPolygon'
      ? geom.coordinates.map((poly) => poly[0])
      : [];
  let best: { area: number; x: number; y: number } | null = null;
  for (const ring of rings) {
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
    if (!best || area > best.area) best = { area, x: cx / (3 * twiceArea), y: cy / (3 * twiceArea) };
  }
  return best ? [Number(best.x.toFixed(1)), Number(best.y.toFixed(1))] : null;
}

export interface StateFacts {
  count: number;
  avgScore: number;
  topSegment?: string;
  /** See `HoverState.contactable`. Null when the rollup predates the field. */
  contactable?: number | null;
  /** See `HoverState.zipUnassigned`. Null when the rollup predates the
   *  disclosure field or the state has full ZIP coverage. */
  zipUnassigned?: number | null;
}

export function countyDisplayName(rollup: CountyRollup): string {
  const raw = rollup.county_name?.trim();
  if (raw) {
    return raw.toLowerCase().endsWith('county') ? raw : `${raw} County`;
  }
  return rollup.fips_5;
}

/**
 * Map drill levels. The county level was removed on 2026-08-08: the
 * Cotality share carries exactly one county FIPS per state, so
 * `county_fips_5` is NULL across gold and every county-keyed rollup
 * returns zero rows — the level rendered a grid of unfilled polygons
 * that dead-ended. The honest drill is state -> ZIP.
 *
 * The county *geometry* helpers below (`buildCountiesPayload`,
 * `countyDisplayName`, `CountiesPayload`) are deliberately kept: they are
 * pure and tested, and a licensed county dataset would light the level
 * back up. See `/api/geo/county-rollups`, which is kept for the same
 * reason.
 */
export type Level = 'state' | 'zip';

export interface Selected {
  level: Level;
  id: string;
  name: string;
}

export interface CountyFeature {
  id: string;
  name: string;
  paths: string;
  cx: number;
  cy: number;
}

export interface CountiesPayload {
  state: string;
  features: CountyFeature[];
  viewBox: string;
}

/** S9 overlay facts attached to a hover when the assignment overlay is ON. */
export interface HoverOverlay {
  leadCount: number | null;
  assignedCount: number | null;
  unattendedCount: number | null;
  coveringOfficerCount: number | null;
  /** Officer display names — surfaced only on the drilled/selected unit. */
  coveringOfficers?: string[];
}

export interface HoverState {
  x: number;
  y: number;
  name: string;
  count: number | null;
  avgScore: number | null;
  topSegment?: string;
  sourceHint?: string;
  /** Present when the assignment overlay is active (S9). */
  overlay?: HoverOverlay;
  /** Contact-eligible subset of `count` for this unit — the borrowers the
   *  Lead Queue behind this tile actually shows, because the queue applies
   *  the eligibility predicate and the tile's headline does not. Live
   *  2026-08-11 IL is 76,711 of 1,851,040, so stating only the headline
   *  sends the reader to a queue 24x smaller than the number they clicked.
   *  Null means "not reported" (an older payload), never "nobody". */
  contactable?: number | null;
  /** Borrowers in this state that the ZIP drill cannot show (no usable
   *  5-digit ZIP in the share). Set only when > 0 — the disclosure exists
   *  to explain why the ZIP tiles sum below the state tile. */
  zipUnassigned?: number | null;
}

export interface LeadQueuePathInput {
  /** `cities` carries `CITY~ST` pairs — see `lib/cityStateFilter`. Always a
   *  pair: a bare name is wrong by 14,631x on the minority side of CYPRESS. */
  geo: { state?: string; county?: string; zip?: string; cities?: string[] };
  segmentFilter?: string[] | null;
  segmentFilterMode?: 'any' | 'all';
  portfolioCriteria?: Record<string, string | number | null | undefined>;
}

export function buildLeadQueuePath({
  geo,
  segmentFilter,
  segmentFilterMode = 'any',
  portfolioCriteria,
}: LeadQueuePathInput): string {
  const params = new URLSearchParams();
  // Ahead of `state` and mutually exclusive with it: the pair already
  // carries the state, and falling back to it is the 2.3x substitution
  // (Chicago's 523,010 opening all 1,181,043 of IL).
  if (geo.cities && geo.cities.length > 0) {
    params.set('cities', geo.cities.join(','));
  } else if (geo.state) {
    params.set('state', geo.state);
  }
  if (geo.county) params.set('county', geo.county);
  if (geo.zip) params.set('zip', geo.zip);
  if (segmentFilter && segmentFilter.length > 0) {
    if (segmentFilter.length === 1) {
      params.set('segment', segmentFilter[0]);
    } else {
      params.set('segment_codes', segmentFilter.join(','));
      params.set('segment_mode', segmentFilterMode);
    }
  }
  Object.entries(portfolioCriteria ?? {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    params.set(key, String(value));
  });
  // `URLSearchParams` percent-encodes `~` to `%7E`; Python's `urlencode` does
  // not. Both decode identically and `~` is RFC-3986 unreserved, so restoring
  // it is a no-op for parsing — but it keeps a frontend-built link
  // byte-identical to the one the backend emits for the same cohort, which is
  // what makes `cities=CHICAGO~IL` readable in a shared URL.
  return `/lead-queue?${params.toString().replace(/%7E/g, '~')}`;
}

export function buildCountiesPayload(
  fc: FeatureCollection,
  stateId: string,
  fips: string,
  pad = 12,
): CountiesPayload | null {
  const stateFeatures = fc.features.filter(
    (f) => typeof f.id === 'string' && f.id.startsWith(fips),
  );
  if (stateFeatures.length === 0) return null;

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const f of stateFeatures) {
    const [x0, y0, x1, y1] = featureBBox(f);
    if (x0 < minX) minX = x0;
    if (y0 < minY) minY = y0;
    if (x1 > maxX) maxX = x1;
    if (y1 > maxY) maxY = y1;
  }

  const vx = minX - pad;
  const vy = minY - pad;
  const vw = maxX - minX + pad * 2;
  const vh = maxY - minY + pad * 2;
  const features: CountyFeature[] = stateFeatures.map((f) => {
    const [x0, y0, x1, y1] = featureBBox(f);
    return {
      id: String(f.id),
      name: (f.properties as { name?: string } | null)?.name ?? '',
      paths: geometryToPath(f.geometry),
      cx: (x0 + x1) / 2,
      cy: (y0 + y1) / 2,
    };
  });

  return {
    state: stateId,
    features,
    viewBox: `${vx.toFixed(1)} ${vy.toFixed(1)} ${vw.toFixed(1)} ${vh.toFixed(1)}`,
  };
}

/**
 * Walk a GeoJSON Polygon / MultiPolygon and produce an SVG compound path `d`
 * string. Handles MultiPolygon by concatenating with a space between sub-
 * polygons.
 */
export function geometryToPath(geom: Geometry): string {
  if (geom.type === 'Polygon') {
    return geom.coordinates.map(ringToPath).join(' ');
  }
  if (geom.type === 'MultiPolygon') {
    return geom.coordinates
      .map((poly) => poly.map(ringToPath).join(' '))
      .join(' ');
  }
  return '';
}

function ringToPath(ring: number[][]): string {
  if (ring.length === 0) return '';
  const [x0, y0] = ring[0];
  let d = `M${x0.toFixed(1)},${y0.toFixed(1)}`;
  for (let i = 1; i < ring.length; i += 1) {
    const [x, y] = ring[i];
    d += `L${x.toFixed(1)},${y.toFixed(1)}`;
  }
  return `${d}Z`;
}

export function featureBBox(f: Feature): [number, number, number, number] {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const visit = (ring: number[][]) => {
    for (const [x, y] of ring) {
      if (x < minX) minX = x;
      if (y < minY) minY = y;
      if (x > maxX) maxX = x;
      if (y > maxY) maxY = y;
    }
  };
  if (f.geometry.type === 'Polygon') {
    f.geometry.coordinates.forEach(visit);
  } else if (f.geometry.type === 'MultiPolygon') {
    f.geometry.coordinates.forEach((poly) => poly.forEach(visit));
  }
  return [minX, minY, maxX, maxY];
}
