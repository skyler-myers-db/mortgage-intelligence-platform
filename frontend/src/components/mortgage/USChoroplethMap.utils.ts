import type { Feature, FeatureCollection, Geometry } from 'geojson';
import type { CountyRollup } from '../../types';

// Shared shape consumed by the state-level map renderers. The payload is
// built from us-atlas state TopoJSON, keeping IDs in the existing lowercase
// USPS format so rollup lookups and drill links do not change.
export interface UsaSvgMapLocation { name: string; id: string; path: string }
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

/** Fixed-threshold fallback used only when no distribution is available. */
export function lvlFromCount(count: number | null | undefined): 1 | 2 | 3 | 4 {
  if (count === null || count === undefined || count <= 0) return 1;
  if (count >= 500) return 4;
  if (count >= 250) return 3;
  if (count >= 100) return 2;
  return 1;
}

/** Build a quantile-based bucketer from the live count distribution. */
export function buildQuantileBucketer(counts: number[]): (count: number | null | undefined) => 1 | 2 | 3 | 4 {
  const nonZero = counts.filter((c) => c > 0).sort((a, b) => a - b);
  if (nonZero.length < 4) {
    return lvlFromCount;
  }
  const q = (p: number) => nonZero[Math.min(nonZero.length - 1, Math.floor(nonZero.length * p))];
  const q25 = q(0.25);
  const q50 = q(0.5);
  const q75 = q(0.75);
  return (count) => {
    if (count === null || count === undefined || count <= 0) return 1;
    if (count >= q75) return 4;
    if (count >= q50) return 3;
    if (count >= q25) return 2;
    return 1;
  };
}

export interface StateFacts {
  count: number;
  avgScore: number;
  lvl: 1 | 2 | 3 | 4;
  topSegment?: string;
}

export const SEGMENT_CODE_TO_NAME: Record<string, string> = {
  itm: 'Prime Refi Candidates',
  listed: 'Listed for Sale',
  permit: 'HELOC Intent',
  investor: 'Investor / Multi-Property',
  equity: 'Home Equity Candidate',
  retention: 'Retention Risk',
};

export function countyDisplayName(rollup: CountyRollup): string {
  const raw = rollup.county_name?.trim();
  if (raw) {
    return raw.toLowerCase().endsWith('county') ? raw : `${raw} County`;
  }
  return rollup.fips_5;
}

export type Level = 'state' | 'county' | 'zip';

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

export interface HoverState {
  x: number;
  y: number;
  name: string;
  count: number | null;
  avgScore: number | null;
  topSegment?: string;
  sourceHint?: string;
}

export interface LeadQueuePathInput {
  geo: { state?: string; county?: string; zip?: string };
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
  if (geo.state) params.set('state', geo.state);
  if (geo.county) params.set('county', geo.county);
  if (geo.zip) params.set('zip', geo.zip);
  if (segmentFilter && segmentFilter.length > 0) {
    params.set('segment_codes', segmentFilter.join(','));
    params.set('segment_mode', segmentFilterMode);
  }
  Object.entries(portfolioCriteria ?? {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    params.set(key, String(value));
  });
  return `/lead-queue?${params.toString()}`;
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
