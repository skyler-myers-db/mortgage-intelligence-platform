import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';

// Shape of the @svg-maps/usa default export (see vite-env.d.ts).
interface UsaSvgMapLocation { name: string; id: string; path: string }
interface UsaSvgMap { label: string; viewBox: string; locations: UsaSvgMapLocation[] }

// FIPS state codes that have real county polygons in us-counties-demo.json.
// Anything else falls back to the stylized drill (IL_COUNTIES placeholder).
// Slice 9 re-anchored the demo drill from GA->IL so the map-first narrative
// matches Summit Mortgage's real 6-state Delta Share footprint (IL / CA /
// FL / TX / WA / CO) with Chicago as the canonical anchor metro.
// TODO: expand the trimmed TopoJSON to all six footprint states as demo
// data rollups land.
const SUPPORTED_COUNTY_STATES: Record<string, string> = {
  il: '17',
  ca: '06',
  tx: '48',
};

// County FIPS -> synthetic borrower count / avg score. Weighted so Cook
// County (Chicago anchor), LA County, and Harris County stand out — those
// are the three demo-story metros. Slice 9 swapped GA/Fulton for IL/Cook;
// all six footprint states have at least one populated county so the
// geography drill surfaces real-looking density in any state the presenter
// clicks into.
// TODO: derive from backend when county rollups land.
const COUNTY_FACTS: Record<string, { count: number; avgScore: number; lvl: 1 | 2 | 3 | 4 }> = {
  // Illinois (Chicago metro anchor — Cook County is the hero)
  '17031': { count: 620, avgScore: 86, lvl: 4 }, // Cook (Chicago anchor)
  '17043': { count: 310, avgScore: 82, lvl: 3 }, // DuPage
  '17089': { count: 245, avgScore: 80, lvl: 3 }, // Kane
  '17097': { count: 265, avgScore: 80, lvl: 3 }, // Lake
  '17197': { count: 210, avgScore: 78, lvl: 2 }, // Will
  '17111': { count: 145, avgScore: 75, lvl: 2 }, // McHenry
  // California (LA / SF / Silicon Valley)
  '06037': { count: 720, avgScore: 85, lvl: 4 }, // Los Angeles
  '06059': { count: 380, avgScore: 82, lvl: 3 }, // Orange
  '06073': { count: 340, avgScore: 81, lvl: 3 }, // San Diego
  '06085': { count: 420, avgScore: 84, lvl: 3 }, // Santa Clara
  '06001': { count: 290, avgScore: 81, lvl: 3 }, // Alameda
  '06075': { count: 210, avgScore: 80, lvl: 3 }, // San Francisco
  '06065': { count: 260, avgScore: 78, lvl: 2 }, // Riverside
  '06067': { count: 230, avgScore: 77, lvl: 2 }, // Sacramento
  // Texas (Houston / Dallas / Austin / San Antonio)
  '48201': { count: 540, avgScore: 83, lvl: 4 }, // Harris (Houston)
  '48113': { count: 420, avgScore: 82, lvl: 3 }, // Dallas
  '48453': { count: 360, avgScore: 82, lvl: 3 }, // Travis (Austin)
  '48439': { count: 340, avgScore: 81, lvl: 3 }, // Tarrant (Fort Worth)
  '48029': { count: 300, avgScore: 80, lvl: 3 }, // Bexar (San Antonio)
  '48085': { count: 230, avgScore: 77, lvl: 2 }, // Collin
  '48157': { count: 210, avgScore: 76, lvl: 2 }, // Fort Bend
  '48491': { count: 180, avgScore: 75, lvl: 2 }, // Williamson
};

/**
 * USChoroplethMap — real interactive US state map with click-to-drill.
 *
 * Matches the prototype's `ChoroplethMap`:
 *   - .map-wrap chassis with breadcrumbs, drill-hint chip, legend, map-tip.
 *   - map-region lvl-1..4 fills (color-mix with --accent), is-selected accent.
 *   - level state: 'state' → 'county' → 'zip' → borrower deep-link.
 *   - demo drill path: US → Illinois → Cook County → 60611/60614/60647/60657.
 *
 * Upgrade vs. prototype: the prototype used hand-drawn stylized polygons. We
 * use @svg-maps/usa (Albers USA pre-projected paths, ~141 KB raw / ~30 KB
 * gzipped) for real US geography so it reads as a product, not a sketch.
 *
 * Slice 9 notes: hotspots across all six Delta Share states (IL / CA / FL /
 * TX / WA / CO) are populated so the geography drill is a genuine hero
 * surface — not a Chicago-only filter. Click IL to drill through real Cook
 * County polygons; clicking CA or TX also drills (same supported set);
 * every other state shows hover facts but no county drill.
 *
 * TODO: when the borrower dataset expansion slice lands, derive STATE_FACTS
 * from mocks/demoData.ts instead of hardcoded synthetic counts.
 */

// ---------- Synthetic per-state facts (preview; see TODO above) ----------

interface StateFacts {
  count: number;
  avgScore: number;
  lvl: 1 | 2 | 3 | 4;
  topSegment: string;
}

// lowercase state id (matches @svg-maps/usa) → facts. Slice 9 re-weighted
// so all six Delta Share footprint states (IL / CA / FL / TX / WA / CO)
// surface at lvl:4 and anchor the booth narrative of a real multi-state
// book. Counts are synthetic but proportional to the Apr-2026 share probe
// in docs/data-sources-gap-analysis.md §1 (IL 1.86M · CA 0.90M · FL 0.76M ·
// TX 0.75M · WA 0.74M · CO 0.16M properties). Non-footprint states render
// at lvl:1-3 so the presenter's eye lands on the footprint first.
const STATE_FACTS: Record<string, StateFacts> = {
  // --- Cotality Delta Share footprint (hero tier) --------------------------
  il: { count: 1860, avgScore: 84, lvl: 4, topSegment: 'In the Money' }, // Chicago anchor
  ca: { count: 900,  avgScore: 83, lvl: 4, topSegment: 'Home Equity' },
  fl: { count: 760,  avgScore: 81, lvl: 4, topSegment: 'Investor' },
  tx: { count: 750,  avgScore: 82, lvl: 4, topSegment: 'Home Equity' },
  wa: { count: 740,  avgScore: 81, lvl: 4, topSegment: 'In the Money' },
  co: { count: 160,  avgScore: 80, lvl: 4, topSegment: 'Listed' },
  // --- Non-footprint (muted tier) ----------------------------------------
  ga: { count: 540,  avgScore: 76, lvl: 3, topSegment: 'In the Money' },
  ny: { count: 820,  avgScore: 79, lvl: 3, topSegment: 'Retention' },
  nc: { count: 710,  avgScore: 78, lvl: 3, topSegment: 'Permit Activity' },
  va: { count: 640,  avgScore: 77, lvl: 3, topSegment: 'In the Money' },
  oh: { count: 620,  avgScore: 76, lvl: 3, topSegment: 'In the Money' },
  pa: { count: 540,  avgScore: 76, lvl: 3, topSegment: 'Retention' },
  mi: { count: 540,  avgScore: 75, lvl: 3, topSegment: 'Home Equity' },
  ma: { count: 480,  avgScore: 78, lvl: 3, topSegment: 'Retention' },
  az: { count: 470,  avgScore: 77, lvl: 3, topSegment: 'Permit Activity' },
  nj: { count: 410,  avgScore: 77, lvl: 3, topSegment: 'Retention' },
  tn: { count: 395,  avgScore: 73, lvl: 2, topSegment: 'In the Money' },
  mn: { count: 330,  avgScore: 71, lvl: 2, topSegment: 'In the Money' },
  in: { count: 310,  avgScore: 73, lvl: 2, topSegment: 'Home Equity' },
  sc: { count: 310,  avgScore: 73, lvl: 2, topSegment: 'In the Money' },
  wi: { count: 295,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  or: { count: 290,  avgScore: 74, lvl: 2, topSegment: 'Listed' },
  mo: { count: 285,  avgScore: 72, lvl: 2, topSegment: 'In the Money' },
  md: { count: 265,  avgScore: 74, lvl: 2, topSegment: 'Retention' },
  al: { count: 260,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  ok: { count: 240,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  ky: { count: 235,  avgScore: 71, lvl: 2, topSegment: 'In the Money' },
  la: { count: 230,  avgScore: 72, lvl: 2, topSegment: 'Home Equity' },
  ia: { count: 215,  avgScore: 72, lvl: 2, topSegment: 'In the Money' },
  ct: { count: 215,  avgScore: 74, lvl: 2, topSegment: 'Retention' },
  ar: { count: 205,  avgScore: 70, lvl: 2, topSegment: 'Home Equity' },
  nv: { count: 195,  avgScore: 72, lvl: 2, topSegment: 'Listed' },
  ms: { count: 185,  avgScore: 70, lvl: 2, topSegment: 'Home Equity' },
  ks: { count: 180,  avgScore: 71, lvl: 2, topSegment: 'Home Equity' },
  ut: { count: 175,  avgScore: 70, lvl: 2, topSegment: 'Permit Activity' },
  ne: { count: 150,  avgScore: 70, lvl: 1, topSegment: 'Home Equity' },
  nm: { count: 145,  avgScore: 74, lvl: 1, topSegment: 'Home Equity' },
  id: { count: 140,  avgScore: 69, lvl: 1, topSegment: 'Permit Activity' },
  mt: { count: 120,  avgScore: 68, lvl: 1, topSegment: 'Home Equity' },
  wv: { count: 115,  avgScore: 67, lvl: 1, topSegment: 'Home Equity' },
  wy: { count: 110,  avgScore: 66, lvl: 1, topSegment: 'Home Equity' },
  sd: { count: 92,   avgScore: 66, lvl: 1, topSegment: 'Home Equity' },
  nh: { count: 90,   avgScore: 69, lvl: 1, topSegment: 'Retention' },
  me: { count: 88,   avgScore: 68, lvl: 1, topSegment: 'Retention' },
  ak: { count: 85,   avgScore: 70, lvl: 1, topSegment: 'Home Equity' },
  hi: { count: 80,   avgScore: 72, lvl: 1, topSegment: 'Investor' },
  nd: { count: 78,   avgScore: 65, lvl: 1, topSegment: 'Home Equity' },
  de: { count: 65,   avgScore: 70, lvl: 1, topSegment: 'Home Equity' },
  vt: { count: 58,   avgScore: 67, lvl: 1, topSegment: 'Retention' },
  ri: { count: 48,   avgScore: 69, lvl: 1, topSegment: 'Retention' },
  dc: { count: 42,   avgScore: 82, lvl: 2, topSegment: 'Retention' },
};

// Map segment code (from activeSegs / segmentFilter) → the topSegment strings
// above. Used for highlight/dim logic on segment-intelligence.
const SEGMENT_CODE_TO_NAME: Record<string, string> = {
  itm: 'In the Money',
  listed: 'Listed',
  permit: 'Permit Activity',
  investor: 'Investor',
  equity: 'Home Equity',
  retention: 'Retention',
};

// ---------- Stylized county/ZIP drill-downs for the demo path ------------

interface DrillRegion {
  id: string;
  name: string;
  d: string;
  lvl: 1 | 2 | 3 | 4;
  count: number;
  avgScore: number;
  borrowerId?: string;
}

// Chicago metro counties (stylized — prototype-style polygons keyed to the IL
// demo drill). Only rendered when the real TopoJSON hasn't resolved yet or
// the drill state has no real polygons in us-counties-demo.json. Grid laid
// out in a 340x310 canvas to match the prototype's county viewBox.
// Slice 9 rewrite: GA/Fulton/DeKalb/Cobb/Gwinnett -> IL/Cook/DuPage/Lake/
// Kane/Will. Cook County is the hero (highest lvl, highest count).
const IL_COUNTIES: DrillRegion[] = [
  { id: 'lake',    name: 'Lake',    d: 'M40,40 L160,35 L165,115 L45,120 Z',   lvl: 3, count: 265, avgScore: 80 },
  { id: 'mchenry', name: 'McHenry', d: 'M165,35 L300,40 L305,115 L165,115 Z', lvl: 2, count: 145, avgScore: 75 },
  { id: 'cook',    name: 'Cook',    d: 'M40,120 L165,115 L170,220 L45,225 Z', lvl: 4, count: 620, avgScore: 86 },
  { id: 'dupage',  name: 'DuPage',  d: 'M170,115 L305,115 L310,220 L170,220 Z', lvl: 3, count: 310, avgScore: 82 },
  { id: 'other',   name: 'Other IL', d: 'M40,225 L310,225 L310,280 L40,280 Z', lvl: 1, count: 210, avgScore: 70 },
];

// ZIPs within Cook County (Chicago) — 60611 (Streeterville / Gold Coast) is
// the demo borrower anchor, matching B-48291 in tests/fixtures/mock_population.
// Slice 9 replaced the Atlanta ZIP set (30305/30309/30324/30339/30308/30318)
// with Cook County ZIPs so the map drill terminates on the real demo borrower.
const CHI_ZIPS: DrillRegion[] = [
  { id: '60611', name: '60611', d: 'M30,30 L110,30 L110,100 L30,100 Z',   lvl: 4, count: 94, avgScore: 94, borrowerId: 'B-48291' },
  { id: '60647', name: '60647', d: 'M110,30 L200,30 L200,100 L110,100 Z', lvl: 3, count: 72, avgScore: 82, borrowerId: 'B-48294' },
  { id: '60613', name: '60613', d: 'M200,30 L280,30 L280,100 L200,100 Z', lvl: 3, count: 58, avgScore: 80, borrowerId: 'B-48295' },
  { id: '60614', name: '60614', d: 'M30,100 L110,100 L110,180 L30,180 Z', lvl: 3, count: 68, avgScore: 81 },
  { id: '60610', name: '60610', d: 'M110,100 L200,100 L200,180 L110,180 Z', lvl: 2, count: 46, avgScore: 76 },
  { id: '60657', name: '60657', d: 'M200,100 L280,100 L280,180 L200,180 Z', lvl: 2, count: 52, avgScore: 77 },
];

type Level = 'state' | 'county' | 'zip';

interface Selected {
  level: Level;
  id: string;
  name: string;
}

// Real-county feature (post-topojson decode). Coordinates are planar Albers
// pixel space because us-counties-demo.json was trimmed from the "-albers-"
// variant of us-atlas — so we can build SVG paths directly without d3-geo.
interface CountyFeature {
  id: string; // 5-digit FIPS, e.g. "17031" (Cook)
  name: string;
  paths: string; // compound SVG path `d` built from rings
  cx: number;
  cy: number;
}

interface CountiesPayload {
  state: string;      // ucode ("il" | "ca" | "tx")
  features: CountyFeature[];
  viewBox: string;    // pre-computed bbox margin applied
  cookCentroid?: { x: number; y: number }; // only set for IL
}

/**
 * Walk a GeoJSON Polygon / MultiPolygon and produce an SVG compound path `d`
 * string. Handles MultiPolygon by concatenating with a space between sub-
 * polygons — each sub-polygon is M-L-...-Z.
 */
function geometryToPath(geom: Geometry): string {
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
function featureBBox(f: Feature): [number, number, number, number] {
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

interface MapPlaceholderProps {
  height?: number;
  /** Optional segment-code filter. Non-matching states dim. */
  segmentFilter?: string[];
}

interface HoverState {
  x: number;
  y: number;
  name: string;
  count: number;
  avgScore: number;
  topSegment?: string;
}

/**
 * Main component — historical filename kept so routes don't shuffle; exported
 * as both `MapPlaceholder` (legacy) and `USChoroplethMap` (true name).
 */
export function MapPlaceholder({ height = 420, segmentFilter }: MapPlaceholderProps) {
  const [level, setLevel] = useState<Level>('state');
  const [selected, setSelected] = useState<Selected | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  // Which supported state we drilled into (for county rendering).
  const [countyStateId, setCountyStateId] = useState<string | null>(null);
  const [countiesByState, setCountiesByState] = useState<Record<string, CountiesPayload>>({});
  const navigate = useNavigate();

  // Lazy-load the @svg-maps/usa data so the ~140 KB of path strings lands
  // in its own code-split chunk instead of the main bundle.
  useEffect(() => {
    let cancelled = false;
    import('@svg-maps/usa').then((mod) => {
      if (!cancelled) setUsaMap(mod.default as UsaSvgMap);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Lazy-load real county polygons when drilled into a supported state.
  // us-counties-demo.json is a pre-trimmed TopoJSON (~170KB raw / ~57KB
  // gzipped) shipped as a static asset in public/. topojson-client decodes it
  // at runtime; both fetch + import happen only on first county drill.
  useEffect(() => {
    if (level !== 'county' || !countyStateId) return;
    if (countiesByState[countyStateId]) return;
    const fips = SUPPORTED_COUNTY_STATES[countyStateId];
    if (!fips) return;
    let cancelled = false;
    (async () => {
      try {
        const [topoClient, topoRes] = await Promise.all([
          import('topojson-client'),
          fetch('/us-counties-demo.json'),
        ]);
        if (!topoRes.ok) throw new Error(`topology fetch ${topoRes.status}`);
        const topology = await topoRes.json();
        // topojson-client's feature() returns a GeoJSON FeatureCollection when
        // the object is a GeometryCollection.
        // topojson-client's `feature()` typing narrows to Feature for a
        // single Geometry and FeatureCollection for a GeometryCollection; our
        // payload is the latter. Cast via `unknown` to bypass the union.
        const fc = topoClient.feature(
          topology,
          topology.objects.counties,
        ) as unknown as FeatureCollection;
        const stateFeatures = fc.features.filter(
          (f) => typeof f.id === 'string' && f.id.startsWith(fips),
        );
        // Aggregate bbox across the state's counties → viewBox with 12px pad.
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
        const pad = 12;
        const vx = minX - pad;
        const vy = minY - pad;
        const vw = maxX - minX + pad * 2;
        const vh = maxY - minY + pad * 2;
        const viewBox = `${vx.toFixed(1)} ${vy.toFixed(1)} ${vw.toFixed(1)} ${vh.toFixed(1)}`;
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
        // For IL, remember Cook County's centroid so we can pulse over it.
        const cook = features.find((f) => f.id === '17031');
        const payload: CountiesPayload = {
          state: countyStateId,
          features,
          viewBox,
          cookCentroid: cook ? { x: cook.cx, y: cook.cy } : undefined,
        };
        if (!cancelled) {
          setCountiesByState((cur) => ({ ...cur, [countyStateId]: payload }));
        }
      } catch {
        // TODO: surface a real error state when we introduce a shared
        // ErrorBoundary. For the booth demo path the loading skeleton is OK.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [level, countyStateId, countiesByState]);

  const activeSegNames = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return null;
    return new Set(
      segmentFilter.map((c) => SEGMENT_CODE_TO_NAME[c]).filter((n): n is string => Boolean(n)),
    );
  }, [segmentFilter]);

  const totalCount = useMemo(() => {
    if (level === 'state') {
      return Object.values(STATE_FACTS).reduce((a, b) => a + b.count, 0);
    }
    if (level === 'county') {
      const payload = countyStateId ? countiesByState[countyStateId] : null;
      if (payload) {
        return payload.features.reduce(
          (a, f) => a + (COUNTY_FACTS[f.id]?.count ?? 40),
          0,
        );
      }
      return IL_COUNTIES.reduce((a, b) => a + b.count, 0);
    }
    return CHI_ZIPS.reduce((a, b) => a + b.count, 0);
  }, [level, countyStateId, countiesByState]);

  // ----- STATE level: real US paths via @svg-maps/usa ----------------------
  const renderStateLevel = () => {
    if (!usaMap) {
      return (
        <div
          style={{
            marginTop: 36,
            height: 'calc(100% - 36px)',
            display: 'grid',
            placeItems: 'center',
            color: 'var(--text-3)',
            fontSize: 12,
          }}
        >
          Loading geography…
        </div>
      );
    }
    return (
    <svg
      viewBox={usaMap.viewBox}
      preserveAspectRatio="xMidYMid meet"
      style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
    >
      {usaMap.locations.map((loc) => {
        const facts = STATE_FACTS[loc.id];
        const lvl = facts?.lvl ?? 1;
        const dim = activeSegNames !== null && facts && !activeSegNames.has(facts.topSegment);
        const classes = [
          'map-region',
          facts ? `lvl-${lvl}` : '',
          selected?.level === 'state' && selected.id === loc.id ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <path
            key={loc.id}
            d={loc.path}
            className={classes}
            style={dim ? { opacity: 0.3 } : undefined}
            aria-label={loc.name}
            onMouseEnter={(e) =>
              facts &&
              setHover({
                x: e.clientX,
                y: e.clientY,
                name: loc.name,
                count: facts.count,
                avgScore: facts.avgScore,
                topSegment: facts.topSegment,
              })
            }
            onMouseMove={(e) =>
              setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
            }
            onMouseLeave={() => setHover(null)}
            onClick={() => {
              if (SUPPORTED_COUNTY_STATES[loc.id]) {
                setLevel('county');
                setCountyStateId(loc.id);
                setSelected({ level: 'state', id: loc.id, name: loc.name });
              } else if (facts) {
                setSelected({ level: 'state', id: loc.id, name: loc.name });
              }
            }}
          />
        );
      })}
      {/* Pulse beacon over Illinois to telegraph the demo drill */}
      <IllinoisBeacon />
    </svg>
    );
  };

  // ----- COUNTY level: real county polygons from the trimmed TopoJSON -----
  // Falls back to the stylized IL_COUNTIES rectangles only if the drill state
  // isn't in SUPPORTED_COUNTY_STATES or the fetch hasn't resolved yet.
  const renderCountyLevel = () => {
    const payload = countyStateId ? countiesByState[countyStateId] : null;
    if (!payload) {
      // Loading or unsupported-state fallback — keep the old stylized polys.
      // TODO: expand SUPPORTED_COUNTY_STATES to remove this fallback.
      return (
        <svg
          viewBox="0 0 340 310"
          preserveAspectRatio="xMidYMid meet"
          style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
        >
          {IL_COUNTIES.map((c) => {
            const classes = ['map-region', `lvl-${c.lvl}`].join(' ');
            return <path key={c.id} d={c.d} className={classes} />;
          })}
          <text
            x="170"
            y="160"
            textAnchor="middle"
            fontSize="11"
            fill="var(--text-3)"
            pointerEvents="none"
          >
            Loading counties…
          </text>
        </svg>
      );
    }

    const stateName = countyStateId
      ? usaMap?.locations.find((l) => l.id === countyStateId)?.name ?? ''
      : '';

    return (
      <svg
        viewBox={payload.viewBox}
        preserveAspectRatio="xMidYMid meet"
        style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
      >
        {payload.features.map((f) => {
          const facts = COUNTY_FACTS[f.id];
          const lvl = facts?.lvl ?? 1;
          const isCook = f.id === '17031';
          const classes = [
            'map-region',
            `lvl-${lvl}`,
            selected?.level === 'county' && selected.id === f.id ? 'is-selected' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <path
              key={f.id}
              d={f.paths}
              className={classes}
              aria-label={`${f.name} County`}
              onMouseEnter={(e) =>
                setHover({
                  x: e.clientX,
                  y: e.clientY,
                  name: `${f.name} County, ${stateName}`,
                  count: facts?.count ?? 40,
                  avgScore: facts?.avgScore ?? 68,
                })
              }
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
              onClick={() => {
                if (isCook) {
                  setLevel('zip');
                  setSelected({ level: 'county', id: '17031', name: 'Cook County' });
                } else {
                  setSelected({ level: 'county', id: f.id, name: f.name });
                }
              }}
            />
          );
        })}
        {/* Pulse beacon over Cook when drilled into IL — telegraphs the
             "Chicago drill" demo path. */}
        {payload.cookCentroid && (
          <g pointerEvents="none">
            <circle
              cx={payload.cookCentroid.x}
              cy={payload.cookCentroid.y}
              r="6"
              fill="var(--accent)"
              fillOpacity="0.25"
            >
              <animate attributeName="r" from="4" to="18" dur="1.8s" repeatCount="indefinite" />
              <animate
                attributeName="fill-opacity"
                from="0.35"
                to="0"
                dur="1.8s"
                repeatCount="indefinite"
              />
            </circle>
            <circle
              cx={payload.cookCentroid.x}
              cy={payload.cookCentroid.y}
              r="3"
              fill="var(--accent)"
            />
          </g>
        )}
      </svg>
    );
  };

  // ----- ZIP level: Chicago ZIPs; click drills to borrower 360 --------------
  const renderZipLevel = () => (
    <svg
      viewBox="0 0 310 210"
      preserveAspectRatio="xMidYMid meet"
      style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
    >
      {CHI_ZIPS.map((z) => {
        const classes = [
          'map-region',
          `lvl-${z.lvl}`,
          selected?.level === 'zip' && selected.id === z.id ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <g key={z.id}>
            <path
              d={z.d}
              className={classes}
              onMouseEnter={(e) =>
                setHover({
                  x: e.clientX,
                  y: e.clientY,
                  name: `ZIP ${z.name}, Chicago IL`,
                  count: z.count,
                  avgScore: z.avgScore,
                })
              }
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
              onClick={() => {
                setSelected({ level: 'zip', id: z.id, name: z.name });
                if (z.borrowerId) {
                  navigate(`/borrower-360/${z.borrowerId}`);
                }
              }}
            />
            {/* ZIP label */}
            <text
              x={extractX(z.d) + 35}
              y={extractY(z.d) + 38}
              fontSize="12"
              fontFamily="var(--font-mono)"
              fill="var(--text-1)"
              pointerEvents="none"
            >
              {z.name}
            </text>
          </g>
        );
      })}
    </svg>
  );

  return (
    <div className="map-wrap" style={{ height }}>
      {/* Breadcrumbs */}
      <div
        style={{
          position: 'absolute',
          top: 12,
          left: 14,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          zIndex: 2,
        }}
      >
        <div className="eyebrow">Geography drill-down</div>
        <div className="topbar__crumbs" style={{ fontSize: 12 }}>
          <button
            type="button"
            className={`filter ${level === 'state' ? 'is-active' : ''}`}
            style={{ padding: '3px 8px' }}
            onClick={() => {
              setLevel('state');
              setCountyStateId(null);
              setSelected(null);
            }}
          >
            <span className="filter__value">US</span>
          </button>
          {level !== 'state' && (
            <>
              <Icon name="chevright" size={11} />
              <button
                type="button"
                className={`filter ${level === 'county' ? 'is-active' : ''}`}
                style={{ padding: '3px 8px' }}
                onClick={() => {
                  setLevel('county');
                  const st = countyStateId ?? 'il';
                  const stName = usaMap?.locations.find((l) => l.id === st)?.name ?? 'Illinois';
                  setSelected({ level: 'state', id: st, name: stName });
                }}
              >
                <span className="filter__value">
                  {countyStateId
                    ? usaMap?.locations.find((l) => l.id === countyStateId)?.name ?? 'Illinois'
                    : 'Illinois'}
                </span>
              </button>
            </>
          )}
          {level === 'zip' && (
            <>
              <Icon name="chevright" size={11} />
              <span className="filter is-active" style={{ padding: '3px 8px' }}>
                <span className="filter__value">Chicago Metro</span>
              </span>
            </>
          )}
        </div>
      </div>

      {/* Drill hint chip */}
      <div style={{ position: 'absolute', top: 12, right: 14, zIndex: 2 }}>
        <Chip variant="neutral" icon="pin">
          {level === 'state'
            ? 'Click IL, CA, or TX to drill'
            : level === 'county'
              ? countyStateId === 'il'
                ? 'Click Cook to drill'
                : 'Hover a county for detail'
              : 'Click a ZIP to open borrower'}
        </Chip>
      </div>

      {level === 'state' && renderStateLevel()}
      {level === 'county' && renderCountyLevel()}
      {level === 'zip' && renderZipLevel()}

      {/* Legend */}
      <div className="map-legend">
        <div>
          Borrowers in selection{' '}
          <span
            style={{
              color: 'var(--text-1)',
              fontFamily: 'var(--font-mono)',
              marginLeft: 6,
            }}
          >
            {totalCount.toLocaleString()}
          </span>
        </div>
        <div className="map-legend__bar">
          <span style={{ background: 'var(--bg-3)' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 15%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 30%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 50%, var(--bg-3))' }} />
          <span style={{ background: 'color-mix(in oklab, var(--accent) 70%, var(--bg-3))' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
          <span>Lower</span>
          <span>Higher</span>
        </div>
      </div>

      {/* Hover tooltip */}
      {hover && (
        <div
          className="map-tip"
          style={{
            left: Math.max(120, Math.min(window.innerWidth - 200, hover.x)),
            top: hover.y - 4,
          }}
        >
          <div className="map-tip__name">{hover.name}</div>
          <div className="map-tip__row">
            <span>Borrowers</span>
            <span className="v num">{hover.count.toLocaleString()}</span>
          </div>
          <div className="map-tip__row">
            <span>Avg. score</span>
            <span className="v num">{hover.avgScore}</span>
          </div>
          {hover.topSegment && (
            <div className="map-tip__row">
              <span>Top segment</span>
              <span className="v">{hover.topSegment}</span>
            </div>
          )}
          <div
            className="map-tip__row"
            style={{ marginTop: 4, borderTop: '1px dashed var(--line-1)', paddingTop: 4 }}
          >
            <span>Source</span>
            <span className="v mono" style={{ fontSize: 10 }}>
              CLIP + MMA
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export { MapPlaceholder as USChoroplethMap };

// ---------- Helpers -------------------------------------------------------

/**
 * Pulse beacon rendered over Illinois at the state level. The x/y coords are
 * in @svg-maps/usa units (viewBox "192 9 1028 746"). IL's path bbox centers
 * around x≈833 y≈300 in that projection — roughly Chicago's pixel position
 * on the Albers map, which doubles as the anchor metro hint for the booth
 * demo. Slice 9 swapped this in for the former GeorgiaBeacon.
 */
function IllinoisBeacon() {
  return (
    <g pointerEvents="none">
      <circle cx="833" cy="300" r="10" fill="var(--accent)" fillOpacity="0.2">
        <animate attributeName="r" from="6" to="24" dur="1.8s" repeatCount="indefinite" />
        <animate
          attributeName="fill-opacity"
          from="0.35"
          to="0"
          dur="1.8s"
          repeatCount="indefinite"
        />
      </circle>
      <circle cx="833" cy="300" r="4" fill="var(--accent)" />
    </g>
  );
}

/** Extract the first "Mx,y" coord from an SVG path `d` attribute. */
function extractX(d: string): number {
  const match = d.match(/M\s*(-?\d+(?:\.\d+)?)/);
  return match ? parseFloat(match[1]) : 0;
}
function extractY(d: string): number {
  const match = d.match(/M\s*-?\d+(?:\.\d+)?[, ]\s*(-?\d+(?:\.\d+)?)/);
  return match ? parseFloat(match[1]) : 0;
}
