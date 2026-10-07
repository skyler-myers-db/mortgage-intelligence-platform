/**
 * The committed per-state ZCTA geometry (audit dataviz-01 / visual-09,
 * D-dataviz-geo-e part B; deviation:zcta-level). LAZY-ONLY: only the ZCTA rung
 * (USChoroplethMapZctaLevel, its own chunk) imports this module, so nothing in
 * the initial chunk or the Home closure carries it.
 *
 * Each `frontend/src/geo/zcta/<USPS>.topo.json` (built by
 * tools/geo/build_zcta_topojson.mjs, pre-projected in us-atlas's Albers-1300
 * space) is emitted as a hashed, same-origin asset: the glob below maps each
 * USPS code to its URL and never imports the JSON as a module (`no-inline`
 * keeps even a small file out of the chunk; zctaGeometry.test.ts holds the
 * belt). A state is fetched only when the rung mounts on a drill into it,
 * never on hover or on national load, and the bytes go through the browser's
 * immutable /assets cache. The fetch is a static asset: no audit row, no
 * borrower data, no /api call.
 *
 * The loader, never render, turns a topology into paths: each ZCTA's `d` at
 * 3 decimals (about 5 m), its box and its label point (zctaLabelAt below),
 * plus the state's 500k outline and box. A loaded state is memoized; a failed load is dropped so a
 * later drill retries (the createUsaStateMapLoader pattern).
 */
import type { Feature, Geometry } from 'geojson';
import { feature } from 'topojson-client';
import type { GeometryCollection, GeometryObject, Topology } from 'topojson-specification';
import { labelAnchor, signedEdgeDistance } from './USChoroplethMap.labels';
import { featureBBox, geometryToPath } from './USChoroplethMap.utils';

/** [x0, y0, x1, y1] in the map's Albers-1300 viewBox units. */
export type ZctaBox = readonly [number, number, number, number];

/** One ZCTA ready to paint. */
export interface ZctaArea {
  /** The 5-digit ZCTA code, which the map treats as the ZIP. */
  zip: string;
  d: string;
  box: ZctaBox;
  labelAt: readonly [number, number] | null;
}

/** A drilled state's geometry, ordered by ZIP (the paint and roving order). */
export interface ZctaGeometry {
  usps: string;
  areas: readonly ZctaArea[];
  byZip: ReadonlyMap<string, ZctaArea>;
  /** The state's 500k outline, drawn above the fills. */
  outline: string;
  stateBox: ZctaBox;
}

/**
 * A drilled state's geometry query key, under the app's 'mip' root
 * (queryKeys.all; zctaGeometry.test.ts pins the two together). It lives here,
 * in the lazy rung's chunk: in lib/geoQueryKeys it would ride in the initial
 * bundle, and importing lib/queryKeys from this chunk split that shared
 * initial chunk in two (+0.17 KiB br initial, measured). It is on no
 * persisted-key list: the geometry is a static asset the browser caches.
 */
export const zctaGeometryKey = (usps: string) => ['mip', 'geo', 'zcta-geometry', usps.toUpperCase()] as const;

/** Path precision for ZCTAs and the outline: 3 decimals of a viewBox unit (about 5 m). */
export const ZCTA_PATH_DIGITS = 3;

const GEOMETRY_URLS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(
    import.meta.glob<string>('../../geo/zcta/*.topo.json', { query: '?url&no-inline', import: 'default', eager: true }),
  ).map(([file, url]) => [file.slice(file.lastIndexOf('/') + 1).replace('.topo.json', ''), url]),
);

/** The hashed asset URL of a state's geometry, or null when no file is committed. */
export function zctaGeometryUrl(usps: string): string | null {
  return GEOMETRY_URLS[usps.toUpperCase()] ?? null;
}

export function hasZctaGeometry(usps: string): boolean {
  return zctaGeometryUrl(usps) !== null;
}

/** Every committed state, for the tests' manifest cross-check. */
export function zctaGeometryStates(): string[] {
  return Object.keys(GEOMETRY_URLS).sort();
}

export type ZctaTopology = Topology<{ zcta: GeometryCollection; state: GeometryObject }>;

function boxOf(input: Feature<Geometry>): ZctaBox {
  const [x0, y0, x1, y1] = featureBBox(input);
  return [x0, y0, x1, y1];
}

/**
 * A ZCTA's label point: the area centroid of its largest polygon, at full
 * precision. labelAnchor's 12-unit clearance and 1-decimal rounding suit a
 * state, not a ZIP area a few units wide (it would run the pole search on
 * nearly every ZCTA and place labels up to 0.05 units off, which is visible at
 * 8x); it is the fallback only when the centroid falls outside the polygon.
 */
function zctaLabelAt(geometry: Geometry): readonly [number, number] | null {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  let best: { area: number; x: number; y: number; rings: number[][][] } | null = null;
  for (const rings of polygons) {
    const ring = rings[0] ?? [];
    let twice = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0; i < ring.length; i += 1) {
      const [x0, y0] = ring[i];
      const [x1, y1] = ring[(i + 1) % ring.length];
      const cross = x0 * y1 - x1 * y0;
      twice += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    if (twice !== 0 && (!best || Math.abs(twice) > best.area)) {
      best = { area: Math.abs(twice), x: cx / (3 * twice), y: cy / (3 * twice), rings };
    }
  }
  if (!best) return null;
  return signedEdgeDistance(best.x, best.y, best.rings) > 0 ? [best.x, best.y] : labelAnchor(geometry);
}

/** Paths, boxes and label points for one state's topology (exported for tests). */
export function buildZctaGeometry(usps: string, topology: ZctaTopology): ZctaGeometry {
  const zctas = feature(topology, topology.objects.zcta).features;
  const state = feature(topology, topology.objects.state) as Feature<Geometry>;
  const areas: ZctaArea[] = [];
  for (const zcta of zctas) {
    if (!zcta.geometry) continue;
    areas.push({
      zip: String(zcta.id),
      d: geometryToPath(zcta.geometry, ZCTA_PATH_DIGITS),
      box: boxOf(zcta as Feature<Geometry>),
      labelAt: zctaLabelAt(zcta.geometry),
    });
  }
  areas.sort((a, b) => (a.zip < b.zip ? -1 : a.zip > b.zip ? 1 : 0));
  return {
    usps,
    areas,
    byZip: new Map(areas.map((area) => [area.zip, area])),
    outline: geometryToPath(state.geometry, ZCTA_PATH_DIGITS),
    stateBox: boxOf(state),
  };
}

type Fetch = (url: string, init: RequestInit) => Promise<Response>;

/**
 * A memoized per-state loader over `urlOf` (exported so a test can inject the
 * URL table and the fetch): one same-origin fetch per state per page, and a
 * failed or aborted load is forgotten so the next drill retries.
 */
export function createZctaGeometryLoader(urlOf: (usps: string) => string | null, fetchImpl: Fetch) {
  const loaded = new Map<string, Promise<ZctaGeometry>>();
  return (usps: string, signal?: AbortSignal): Promise<ZctaGeometry> => {
    const key = usps.toUpperCase();
    const cached = loaded.get(key);
    if (cached) return cached;
    const url = urlOf(key);
    if (!url) return Promise.reject(new Error('No ZIP-area geometry for this state'));
    const pending = fetchImpl(url, { signal, credentials: 'same-origin' })
      .then((response) => {
        if (!response.ok) throw new Error(`ZIP-area geometry answered ${response.status}`);
        return response.json() as Promise<ZctaTopology>;
      })
      .then((topology) => buildZctaGeometry(key, topology))
      .catch((error: unknown) => {
        loaded.delete(key);
        throw error;
      });
    loaded.set(key, pending);
    return pending;
  };
}

/** The drilled state's committed geometry (see createZctaGeometryLoader). */
export const loadZctaGeometry = createZctaGeometryLoader(zctaGeometryUrl, (url, init) => fetch(url, init));
