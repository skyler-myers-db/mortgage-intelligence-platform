import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { api } from '../../lib/api';
import { usePrefersReducedMotion } from '../../lib/usePrefersReducedMotion';
import { useOptionalFootprint } from '../FootprintProvider';
import type { CountyRollup, StateRollup, ZipRollup } from '../../types';

// Shape of the @svg-maps/usa default export (see vite-env.d.ts).
interface UsaSvgMapLocation { name: string; id: string; path: string }
interface UsaSvgMap { label: string; viewBox: string; locations: UsaSvgMapLocation[] }

// USPS (lowercase) -> FIPS-2 map. Values are intrinsic per-state constants,
// NOT tenant-configurable — a state's FIPS code is an act of Congress, not a
// customer setting. The *set of active* entries is filtered at render time
// by the `FootprintProvider` so each tenant sees only their footprint's
// counties light up as drillable.
//
// The shipped us-counties.json TopoJSON (public/us-counties.json) is trimmed
// to the 6-state Summit footprint (IL/CA/FL/TX/WA/CO). A tenant whose
// footprint extends outside this set will still get that state's state-level
// hover + summary, but county drill for the extra state will show the
// "Loading counties…" / fallback state until the TopoJSON is regenerated
// with that state's counties included.
const USCODE_TO_FIPS: Record<string, string> = {
  il: '17',
  ca: '06',
  fl: '12',
  tx: '48',
  wa: '53',
  co: '08',
};

// Slice13-accuracy-validation: county + ZIP hover numbers now come from
// /api/geo/county-rollups + /api/geo/zip-rollups (backed by
// mip.gold.county_rollup + mip.gold.zip_rollup respectively). The prior
// hardcoded COUNTY_FACTS + CHI_ZIP_TILES literals are gone -- any county /
// ZIP not returned in the payload renders "—" on hover (honest null).
// The fill-level bucket is derived from addressable_borrowers below.

/** Fixed-threshold fallback (used only when no distribution is available
 *  -- e.g. the synthetic IL_COUNTIES rectangles before the TopoJSON
 *  resolves). The real map uses `buildQuantileBucketer` below so the
 *  gradient reads visually regardless of the absolute count range
 *  (Cotality TX counties are in the tens of thousands; synthetic demo
 *  counties are in the hundreds). */
function lvlFromCount(count: number | null | undefined): 1 | 2 | 3 | 4 {
  if (count === null || count === undefined || count <= 0) return 1;
  if (count >= 500) return 4;
  if (count >= 250) return 3;
  if (count >= 100) return 2;
  return 1;
}

/** Build a quantile-based bucketer from the live count distribution for
 *  the currently-drilled layer (counties of the active state, or ZIPs
 *  of the active county). Splits non-zero counts into 4 equal-size
 *  buckets: top 25% → lvl-4, next 25% → lvl-3, next 25% → lvl-2,
 *  bottom 25% → lvl-1. Zero / missing stays at lvl-1 (the CSS floor)
 *  but those regions render "—" in the hover card, so the visual still
 *  distinguishes "no data" from "low density."
 *
 *  Rationale: the synthetic-demo thresholds (>=500, >=250, >=100) bucket
 *  every real Cotality TX county into lvl-4 because every county has
 *  10k+ marketable borrowers. A quantile bucketer keeps the gradient
 *  readable whether the underlying numbers are in the hundreds or the
 *  hundred-thousands. */
function buildQuantileBucketer(counts: number[]): (count: number | null | undefined) => 1 | 2 | 3 | 4 {
  const nonZero = counts.filter((c) => c > 0).sort((a, b) => a - b);
  if (nonZero.length < 4) {
    // Too few data points for meaningful quantiles — fall back to the
    // fixed scale.
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

/**
 * USChoroplethMap — real interactive US state map with click-to-drill.
 *
 * Matches the prototype's `ChoroplethMap`:
 *   - .map-wrap chassis with breadcrumbs, drill-hint chip, legend, map-tip.
 *   - map-region lvl-1..4 fills (color-mix with --accent), is-selected accent.
 *   - level state: 'state' → 'county' → 'zip' → borrower deep-link.
 *   - drill path: US → Illinois → Cook County → 60611/60614/60647/60657.
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
 * from the API instead of hardcoded synthetic counts.
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
// surface at lvl:4 and anchor the narrative of a real multi-state book.
// Counts are synthetic but proportional to the Apr-2026 share probe
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

// ---------- Stylized county/ZIP drill-downs (fallback rendering) --------

/** Stylized fallback polygon geometry for the county + ZIP drills. Only
 *  the geometry (id, name, path `d`, optional ZIP deep-link anchor) is
 *  baked in -- count + avgScore now come from /api/geo/county-rollups +
 *  /api/geo/zip-rollups. A `lvl` default is provided for the county
 *  fallback renderer (used when the TopoJSON hasn't resolved yet); the
 *  live renderer computes lvl from the real borrower count. */
interface DrillRegion {
  id: string;
  name: string;
  d: string;
  lvl?: 1 | 2 | 3 | 4;
  // Optional visual-fallback fields kept on the stylized IL/ZIP tiles.
  // The live render ignores these; they're consumed only when the
  // TopoJSON / rollup API hasn't resolved yet.
  count?: number;
  avgScore?: number;
  /** Sample borrower deep-link for ZIP tiles (ZIP rollup agent may swap
   *  this for a backend-sourced sample_borrower_id). */
  borrowerId?: string;
}

// Chicago metro counties (stylized — prototype-style polygons keyed to the
// IL county drill). Only rendered when the real TopoJSON hasn't resolved
// yet. Grid laid out in a 340x310 canvas to match the prototype's county
// viewBox. lvl is a visual fallback; the live renderer reads real counts.
const IL_COUNTIES: DrillRegion[] = [
  { id: 'lake',    name: 'Lake',    d: 'M40,40 L160,35 L165,115 L45,120 Z',     lvl: 3 },
  { id: 'mchenry', name: 'McHenry', d: 'M165,35 L300,40 L305,115 L165,115 Z',   lvl: 2 },
  { id: 'cook',    name: 'Cook',    d: 'M40,120 L165,115 L170,220 L45,225 Z',   lvl: 4 },
  { id: 'dupage',  name: 'DuPage',  d: 'M170,115 L305,115 L310,220 L170,220 Z', lvl: 3 },
  { id: 'other',   name: 'Other IL', d: 'M40,225 L310,225 L310,280 L40,280 Z',  lvl: 1 },
];

// ZIP tiles within Cook County (Chicago). Slice13-accuracy-validation:
// count + avgScore literals are gone -- those come from
// /api/geo/zip-rollups?fips=17031. `sample_borrower_id` is also pulled
// from the payload, not baked in here. The tile geometry (x/y/width/
// height/label) is kept as a purely visual layout hint.
//
// 2026-04-23 audit note: the three tiles below (60611/60647/60613) are
// the ones the CTAS currently returns a sample_borrower_id for. If the
// gold CTAS later lands a sample for 60614/60610/60657, add tiles for
// them here -- the live fetch will resolve the rest.
const CHI_ZIP_TILES: DrillRegion[] = [
  { id: '60611', name: '60611', d: 'M30,30 L110,30 L110,100 L30,100 Z'   },
  { id: '60647', name: '60647', d: 'M110,30 L200,30 L200,100 L110,100 Z' },
  { id: '60613', name: '60613', d: 'M200,30 L280,30 L280,100 L200,100 Z' },
];

type Level = 'state' | 'county' | 'zip';

interface Selected {
  level: Level;
  id: string;
  name: string;
}

// Real-county feature (post-topojson decode). Coordinates are planar Albers
// pixel space because us-counties.json was trimmed from the "-albers-"
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

/** Selection payload emitted on every state/county/ZIP click. State is
 *  2-char uppercase USPS code so consumers can run predicates against
 *  `LeadSummary.state` directly. `county` is the 5-digit FIPS; `zip` is
 *  the 5-digit string. `null` means the user navigated back to US level. */
export interface MapSelection {
  state: string | null;
  county: string | null;
  zip: string | null;
}

interface USChoroplethMapProps {
  height?: number;
  /** Optional segment-code filter. Non-matching states dim. */
  segmentFilter?: string[];
  /** Fires every time the user drills or navigates back. Always fires
   *  with the current selection — an empty selection (all nulls) when
   *  the user returns to US level. */
  onSelectionChange?: (selection: MapSelection) => void;
  /** State-level drill behavior. `"filter"` keeps the drill in-place and
   *  relies on `onSelectionChange` (used by segment-intelligence to
   *  filter the LeadTable). `"navigate"` deep-links to
   *  `/lead-queue?state=XX` so the home-page map acts as a teaser. */
  drillBehavior?: 'filter' | 'navigate';
}

interface HoverState {
  x: number;
  y: number;
  name: string;
  /** `null` means "we don't have a rollup for this geometry". The
   *  tooltip renders "—" instead of fabricating a number. Used at the
   *  county + ZIP levels where we don't have gold-backed rollups. */
  count: number | null;
  avgScore: number | null;
  topSegment?: string;
  /** Source label displayed in the hover card footer. */
  sourceHint?: string;
}

/**
 * US Choropleth Map — state → county → ZIP drill-down over Cotality public
 * records. Exported under both names: `USChoroplethMap` (primary) and
 * `MapPlaceholder` (deprecated alias kept for any lingering external
 * imports; remove after the next slice).
 */
export function USChoroplethMap({
  height = 420,
  segmentFilter,
  onSelectionChange,
  drillBehavior = 'filter',
}: USChoroplethMapProps) {
  const [level, setLevel] = useState<Level>('state');
  const [selected, setSelected] = useState<Selected | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  // Reduced-motion guard for SVG SMIL pulses (IllinoisBeacon + Cook
  // centroid). CSS `prefers-reduced-motion` doesn't cover <animate>;
  // we conditionally render the animating child when the user prefers
  // reduced motion. Hole-finder finding #18, 2026-04-23.
  const reduceMotion = usePrefersReducedMotion();
  // Which supported state we drilled into (for county rendering).
  const [countyStateId, setCountyStateId] = useState<string | null>(null);
  const [countiesByState, setCountiesByState] = useState<Record<string, CountiesPayload>>({});
  const [countyLoadError, setCountyLoadError] = useState<string | null>(null);
  // Per-state rollups from /api/geo/state-rollups. `null` = loading; `{}`
  // = API unreachable (fall back to STATE_FACTS literal). Keyed by
  // lowercase state code to match @svg-maps/usa location ids.
  const [liveStateFacts, setLiveStateFacts] = useState<Record<string, StateRollup> | null>(null);
  // Per-state county rollups lazy-loaded on drill. Keyed by uppercase
  // state code. Each value is a dict keyed by 5-char FIPS so the map's
  // county renderer can O(1) look up a county's live count / avg score.
  const [liveCountyFacts, setLiveCountyFacts] = useState<Record<string, Record<string, CountyRollup>>>({});
  // Per-county ZIP rollups lazy-loaded on county drill. Keyed by 5-char
  // county FIPS. Value is a dict keyed by 5-digit ZIP.
  const [liveZipFacts, setLiveZipFacts] = useState<Record<string, Record<string, ZipRollup>>>({});
  const navigate = useNavigate();
  const footprint = useOptionalFootprint();

  // Footprint-aware drill allowlist. Intersects the tenant's footprint
  // (lowercased USPS codes) with USCODE_TO_FIPS so only states that (a)
  // are in the tenant's footprint AND (b) have polygons in the shipped
  // TopoJSON drill. Memoised so render-only state flips don't recompute
  // the set per frame.
  const supportedCountyStates = useMemo<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const code of footprint.stateCodes) {
      const lc = code.toLowerCase();
      const fips = USCODE_TO_FIPS[lc];
      if (fips) out[lc] = fips;
    }
    return out;
  }, [footprint.stateCodes]);

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

  // Fetch per-state rollups from the backend once on mount. The repo
  // response replaces the `count` + `avgScore` pair in STATE_FACTS with
  // real rollups; the `lvl` / `topSegment` metadata stays synthetic
  // because those aren't in gold. On error we silently fall back to the
  // hardcoded STATE_FACTS so the map still renders rather than going
  // blank — the honest UX trade-off when the backend is down.
  useEffect(() => {
    let cancelled = false;
    api
      .stateRollups()
      .then((payload) => {
        if (cancelled) return;
        const byCode: Record<string, StateRollup> = {};
        for (const r of payload.rollups) {
          byCode[r.state.toLowerCase()] = r;
        }
        setLiveStateFacts(byCode);
      })
      .catch(() => {
        // Silent fallback to STATE_FACTS. This is the one place in the
        // map where we prefer "synthetic but static" over "degraded
        // banner" — the map is an ambient surface and a failing hover
        // shouldn't block the whole page. Everything else on the page
        // that uses the same /api/* path DOES surface an error banner.
        if (!cancelled) setLiveStateFacts({});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Lazy-fetch county rollups on drill. /api/geo/county-rollups?state=XX
  // returns real counts / avg_score / top_segment_code per FIPS from
  // mip.gold.county_rollup. Missing counties render "—" (honest null).
  useEffect(() => {
    if (level !== 'county' || !countyStateId) return;
    const stateUC = countyStateId.toUpperCase();
    if (liveCountyFacts[stateUC]) return;
    let cancelled = false;
    api
      .countyRollups(stateUC)
      .then((payload) => {
        if (cancelled) return;
        const byFips: Record<string, CountyRollup> = {};
        for (const r of payload.rollups) byFips[r.fips_5] = r;
        setLiveCountyFacts((cur) => ({ ...cur, [stateUC]: byFips }));
      })
      .catch(() => {
        if (!cancelled) {
          // Empty dict means "we tried, nothing came back" -- hover falls
          // back to "—" instead of blocking re-fetches on every render.
          setLiveCountyFacts((cur) => ({ ...cur, [stateUC]: {} }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [level, countyStateId, liveCountyFacts]);

  // Lazy-fetch ZIP rollups when the user drills into any county. The
  // CTAS only populates ZIPs for counties with borrower rows, so
  // out-of-footprint counties resolve to an empty list; the UI renders
  // an "—" hover + no-op click for those.
  useEffect(() => {
    if (level !== 'zip') return;
    const fips = selected?.level === 'county' ? selected.id : null;
    if (!fips) return;
    if (liveZipFacts[fips]) return;
    let cancelled = false;
    api
      .zipRollups(fips)
      .then((payload) => {
        if (cancelled) return;
        const byZip: Record<string, ZipRollup> = {};
        for (const r of payload.rollups) byZip[r.zip] = r;
        setLiveZipFacts((cur) => ({ ...cur, [fips]: byZip }));
      })
      .catch(() => {
        if (!cancelled) setLiveZipFacts((cur) => ({ ...cur, [fips]: {} }));
      });
    return () => {
      cancelled = true;
    };
  }, [level, selected, liveZipFacts]);

  // Lazy-load real county polygons when drilled into a supported state.
  // us-counties.json is a pre-trimmed TopoJSON (~170KB raw / ~57KB
  // gzipped) shipped as a static asset in public/. topojson-client decodes it
  // at runtime; both fetch + import happen only on first county drill.
  useEffect(() => {
    if (level !== 'county' || !countyStateId) return;
    if (countiesByState[countyStateId]) return;
    const fips = supportedCountyStates[countyStateId];
    if (!fips) return;
    let cancelled = false;
    (async () => {
      try {
        const [topoClient, topoRes] = await Promise.all([
          import('topojson-client'),
          fetch('/us-counties.json'),
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
      } catch (err) {
        if (!cancelled) {
          setCountyLoadError(
            err instanceof Error
              ? `Couldn't load county polygons: ${err.message}`
              : "Couldn't load county polygons.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [level, countyStateId, countiesByState, supportedCountyStates]);

  const activeSegNames = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return null;
    return new Set(
      segmentFilter.map((c) => SEGMENT_CODE_TO_NAME[c]).filter((n): n is string => Boolean(n)),
    );
  }, [segmentFilter]);

  /** Merge the live state rollup (count / avg_score / top_segment_code
   *  from /api/geo/state-rollups) with the static STATE_FACTS metadata
   *  (lvl). Prefers the live top_segment_code when the backend returns
   *  one; falls back to the hardcoded STATE_FACTS[*].topSegment only
   *  when the endpoint errored (slice13-accuracy-validation). */
  const factsFor = useMemo(() => {
    return (uscode: string): StateFacts | undefined => {
      const live = liveStateFacts?.[uscode];
      const stat = STATE_FACTS[uscode];
      const liveTopSegment = live?.top_segment_code
        ? (SEGMENT_CODE_TO_NAME[live.top_segment_code] ?? '')
        : '';
      if (live && stat) {
        return {
          count: live.addressable,
          avgScore: live.avg_score || stat.avgScore,
          lvl: stat.lvl,
          // Prefer the live segment label; only fall back to the static
          // literal when the API did not return a segment code.
          topSegment: liveTopSegment || stat.topSegment,
        };
      }
      if (live) {
        // State not in STATE_FACTS literal — synthesize minimal facts so
        // the hover still shows the real count. lvl defaults to 2 (mid
        // tier); topSegment uses the live value when present else blank
        // so the tooltip row hides.
        return {
          count: live.addressable,
          avgScore: live.avg_score,
          lvl: 2,
          topSegment: liveTopSegment,
        };
      }
      return stat;
    };
  }, [liveStateFacts]);

  // Quantile bucketer for the county layer of the currently-drilled
  // state. Computed per-payload so the gradient reads whether the real
  // counts are in the hundreds or the hundred-thousands (see
  // `buildQuantileBucketer` docstring).
  const countyBucketer = useMemo(() => {
    const stateUC = countyStateId?.toUpperCase() ?? '';
    const byFips = liveCountyFacts[stateUC];
    if (!byFips) return lvlFromCount;
    const counts = Object.values(byFips).map((r) => r.addressable_borrowers ?? 0);
    return buildQuantileBucketer(counts);
  }, [countyStateId, liveCountyFacts]);

  // Quantile bucketer for the ZIP layer of the currently-drilled county.
  const zipBucketer = useMemo(() => {
    const fips = selected?.level === 'county' ? selected.id : '';
    const byZip = liveZipFacts[fips];
    if (!byZip) return lvlFromCount;
    const counts = Object.values(byZip).map((r) => r.addressable_borrowers ?? 0);
    return buildQuantileBucketer(counts);
  }, [selected, liveZipFacts]);

  // Fire the selection callback when the user drills into / out of a
  // level. Collecting into a single effect keeps the producer logic in
  // the click handlers pure (they just call setState).
  useEffect(() => {
    if (!onSelectionChange) return;
    const stateCode =
      selected?.level === 'state'
        ? selected.id.toUpperCase()
        : countyStateId
          ? countyStateId.toUpperCase()
          : null;
    const countyFips = selected?.level === 'county' ? selected.id : null;
    const zip = selected?.level === 'zip' ? selected.id : null;
    onSelectionChange({ state: stateCode, county: countyFips, zip });
  }, [selected, countyStateId, onSelectionChange]);

  const totalCount = useMemo(() => {
    if (level === 'state') {
      if (liveStateFacts && Object.keys(liveStateFacts).length > 0) {
        return Object.values(liveStateFacts).reduce((a, b) => a + b.addressable, 0);
      }
      return Object.values(STATE_FACTS).reduce((a, b) => a + b.count, 0);
    }
    if (level === 'county') {
      const stateUC = countyStateId?.toUpperCase() ?? '';
      const liveByFips = liveCountyFacts[stateUC];
      if (liveByFips) {
        return Object.values(liveByFips).reduce(
          (a, r) => a + (r.addressable_borrowers ?? 0),
          0,
        );
      }
      // Pre-fetch placeholder: show the state-level count so the legend
      // doesn't flash 0 while the county payload is in flight.
      return liveStateFacts?.[countyStateId ?? '']?.addressable ?? 0;
    }
    // ZIP level — key off the selected county, not a hardcoded FIPS.
    const fips = selected?.level === 'county' ? selected.id : null;
    const liveByZip = fips ? liveZipFacts[fips] : undefined;
    if (liveByZip) {
      return Object.values(liveByZip).reduce(
        (a, r) => a + (r.addressable_borrowers ?? 0),
        0,
      );
    }
    return 0;
  }, [level, countyStateId, selected, liveStateFacts, liveCountyFacts, liveZipFacts]);

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
        const facts = factsFor(loc.id);
        const lvl = facts?.lvl ?? 1;
        const dim =
          activeSegNames !== null && facts && facts.topSegment && !activeSegNames.has(facts.topSegment);
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
            role="button"
            tabIndex={0}
            aria-label={loc.name}
            onMouseEnter={(e) =>
              facts &&
              setHover({
                x: e.clientX,
                y: e.clientY,
                name: loc.name,
                count: facts.count,
                avgScore: facts.avgScore,
                topSegment: facts.topSegment || undefined,
                sourceHint: 'mip.gold.state_rollup',
              })
            }
            onMouseMove={(e) =>
              setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
            }
            onMouseLeave={() => setHover(null)}
            onClick={() => {
              if (drillBehavior === 'navigate') {
                // Home-page teaser → deep-link to the filtered queue. Only
                // navigate when we actually have data for the state; clicking
                // an unsupported state is a no-op.
                if (facts) {
                  navigate(`/lead-queue?state=${loc.id.toUpperCase()}`);
                }
                return;
              }
              if (supportedCountyStates[loc.id]) {
                setLevel('county');
                setCountyStateId(loc.id);
                setSelected({ level: 'state', id: loc.id, name: loc.name });
              } else if (facts) {
                setSelected({ level: 'state', id: loc.id, name: loc.name });
              }
            }}
            onKeyDown={(e) => {
              // A11y: role="button" + tabIndex=0 require Enter/Space to
              // behave like a click for keyboard users. Without this the
              // map was reachable via Tab but drill-only via mouse.
              if (e.key !== 'Enter' && e.key !== ' ') return;
              e.preventDefault();
              if (drillBehavior === 'navigate') {
                if (facts) navigate(`/lead-queue?state=${loc.id.toUpperCase()}`);
                return;
              }
              if (supportedCountyStates[loc.id]) {
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
      {/* Pulse beacon over Illinois to telegraph the county drill. The
          animated rings are suppressed when the user prefers reduced
          motion; a static dot renders instead. */}
      <IllinoisBeacon reduceMotion={reduceMotion} />
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
            const classes = ['map-region', `lvl-${c.lvl ?? 1}`].join(' ');
            return <path key={c.id} d={c.d} className={classes} />;
          })}
          <text
            x="170"
            y="160"
            textAnchor="middle"
            fontSize="11"
            fill={countyLoadError ? 'var(--signal-danger)' : 'var(--text-3)'}
            pointerEvents="none"
          >
            {countyLoadError ?? 'Loading counties…'}
          </text>
          {countyLoadError && (
            <text
              x="170"
              y="180"
              textAnchor="middle"
              fontSize="10"
              fill="var(--text-3)"
              pointerEvents="none"
              style={{ cursor: 'pointer' }}
              onClick={() => {
                setCountyLoadError(null);
                setCountiesByState((c) => {
                  const next = { ...c };
                  if (countyStateId) delete next[countyStateId];
                  return next;
                });
              }}
            >
              Click to retry
            </text>
          )}
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
          const stateUC = countyStateId?.toUpperCase() ?? '';
          const liveFacts = liveCountyFacts[stateUC]?.[f.id];
          const count = liveFacts?.addressable_borrowers ?? null;
          const avgScore = liveFacts?.avg_opportunity_score ?? null;
          const topSegCode = liveFacts?.top_segment_code ?? null;
          const topSegment = topSegCode ? (SEGMENT_CODE_TO_NAME[topSegCode] ?? undefined) : undefined;
          const lvl = countyBucketer(count);
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
              role="button"
              tabIndex={0}
              aria-label={`${f.name} County`}
              onMouseEnter={(e) =>
                setHover({
                  x: e.clientX,
                  y: e.clientY,
                  name: `${f.name} County, ${stateName}`,
                  // Honest null when the live payload hasn't returned a
                  // row for this county (either pre-fetch or the CTAS
                  // excluded it as out-of-footprint). Tooltip renders "—".
                  count,
                  avgScore,
                  topSegment,
                  sourceHint: 'mip.gold.county_rollup',
                })
              }
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
              onClick={() => {
                // Every county drills to ZIP level. The /api/geo/zip-rollups
                // fetch may return an empty list for out-of-footprint
                // counties -- the ZIP render handles that with an empty
                // state + "open in Lead Queue" fallback.
                setLevel('zip');
                setSelected({ level: 'county', id: f.id, name: `${f.name} County` });
              }}
              onKeyDown={(e) => {
                if (e.key !== 'Enter' && e.key !== ' ') return;
                e.preventDefault();
                setLevel('zip');
                setSelected({ level: 'county', id: f.id, name: `${f.name} County` });
              }}
            />
          );
        })}
        {/* Pulse beacon over Cook when drilled into IL — telegraphs the
             "Chicago drill" path. Animated rings are suppressed when the
             user prefers reduced motion; a static dot renders instead.
             Hole-finder finding #18, 2026-04-23. */}
        {payload.cookCentroid && (
          <g pointerEvents="none">
            {!reduceMotion && (
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
            )}
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

  // ----- ZIP level: tile grid for the active county. -----------------------
  // Cook County retains its stylized 3-tile layout from the prototype; every
  // other county auto-generates a responsive tile grid from the live ZIP
  // rollup payload so the drill works for the whole footprint.
  const renderZipLevel = () => {
    const countyFips = selected?.level === 'county' ? selected.id : null;
    const countyName = selected?.level === 'county' ? selected.name : '';
    const stateUC =
      countyStateId?.toUpperCase() ??
      (countyFips ? Object.entries(USCODE_TO_FIPS).find(([, v]) => countyFips.startsWith(v))?.[0].toUpperCase() ?? '' : '');
    if (!countyFips) return null;
    const byZip = liveZipFacts[countyFips];
    const zipsFromApi = byZip ? Object.values(byZip) : [];

    // Cook County keeps its prototype stylized geometry when the API
    // returned ZIPs for the three Chicago anchors; otherwise we auto-tile.
    const useStyled = countyFips === '17031' && zipsFromApi.length > 0;
    const styledTiles = CHI_ZIP_TILES.filter((t) => byZip?.[t.id]);
    const useStyledRender = useStyled && styledTiles.length >= 3;

    if (byZip && zipsFromApi.length === 0) {
      // API returned empty — county outside footprint or CTAS hasn't
      // populated ZIPs for it. Give the user a graceful fallback path.
      return (
        <div
          style={{
            marginTop: 36,
            height: 'calc(100% - 36px)',
            display: 'grid',
            placeItems: 'center',
            padding: 'var(--sp-4)',
          }}
        >
          <div style={{ textAlign: 'center', color: 'var(--text-3)', fontSize: 12, maxWidth: 300 }}>
            <div style={{ color: 'var(--text-2)', marginBottom: 'var(--sp-2)' }}>
              No ZIP-level rollup for {countyName}.
            </div>
            <button
              type="button"
              className="btn btn--ghost"
              style={{ fontSize: 12 }}
              onClick={() => navigate(`/lead-queue?state=${stateUC}&county=${countyFips}`)}
            >
              Open Lead Queue for this county
            </button>
          </div>
        </div>
      );
    }

    if (!byZip) {
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
          Loading ZIPs…
        </div>
      );
    }

    if (useStyledRender) {
      // Prototype-style Chicago ZIP layout.
      return (
        <svg viewBox="0 0 310 210" preserveAspectRatio="xMidYMid meet" style={{ marginTop: 36, height: 'calc(100% - 36px)' }}>
          {styledTiles.map((z) => {
            const liveFacts = byZip[z.id];
            const count = liveFacts?.addressable_borrowers ?? null;
            const avgScore = liveFacts?.avg_opportunity_score ?? null;
            const topSegCode = liveFacts?.top_segment_code ?? null;
            const topSegment = topSegCode ? SEGMENT_CODE_TO_NAME[topSegCode] : undefined;
            const sampleBorrowerId = liveFacts?.sample_borrower_id ?? null;
            const classes = [
              'map-region',
              `lvl-${zipBucketer(count)}`,
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
                      name: `ZIP ${z.name}, ${countyName}`,
                      count,
                      avgScore,
                      topSegment,
                      sourceHint: 'mip.gold.zip_rollup',
                    })
                  }
                  onMouseMove={(e) =>
                    setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
                  }
                  onMouseLeave={() => setHover(null)}
                  onClick={() => {
                    setSelected({ level: 'zip', id: z.id, name: z.name });
                    if (sampleBorrowerId) {
                      navigate(`/borrower-360/${sampleBorrowerId}`);
                    }
                  }}
                />
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
    }

    // Auto-tiled grid for every other county. Sort descending by count so
    // the densest ZIPs land in the top-left -- reads like a Pareto.
    const sorted = [...zipsFromApi].sort(
      (a, b) => (b.addressable_borrowers ?? 0) - (a.addressable_borrowers ?? 0),
    );
    const visible = sorted.slice(0, 24);
    const cols = Math.min(6, Math.ceil(Math.sqrt(Math.max(1, visible.length))));
    const rows = Math.ceil(visible.length / cols);
    const tileW = 80;
    const tileH = 56;
    const gap = 6;
    const vbW = cols * tileW + (cols + 1) * gap;
    const vbH = rows * tileH + (rows + 1) * gap;

    return (
      <svg
        viewBox={`0 0 ${vbW} ${vbH}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ marginTop: 36, height: 'calc(100% - 36px)' }}
      >
        {visible.map((rollup, i) => {
          const col = i % cols;
          const row = Math.floor(i / cols);
          const x = gap + col * (tileW + gap);
          const y = gap + row * (tileH + gap);
          const count = rollup.addressable_borrowers ?? null;
          const avgScore = rollup.avg_opportunity_score ?? null;
          const topSegCode = rollup.top_segment_code ?? null;
          const topSegment = topSegCode ? SEGMENT_CODE_TO_NAME[topSegCode] : undefined;
          const sampleBorrowerId = rollup.sample_borrower_id ?? null;
          const classes = [
            'map-region',
            `lvl-${zipBucketer(count)}`,
            selected?.level === 'zip' && selected.id === rollup.zip ? 'is-selected' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <g key={rollup.zip}>
              <rect
                x={x}
                y={y}
                width={tileW}
                height={tileH}
                rx={6}
                ry={6}
                className={classes}
                role="button"
                tabIndex={0}
                aria-label={`ZIP ${rollup.zip}`}
                onMouseEnter={(e) =>
                  setHover({
                    x: e.clientX,
                    y: e.clientY,
                    name: `ZIP ${rollup.zip}, ${countyName}`,
                    count,
                    avgScore,
                    topSegment,
                    sourceHint: 'mip.gold.zip_rollup',
                  })
                }
                onMouseMove={(e) =>
                  setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
                }
                onMouseLeave={() => setHover(null)}
                onClick={() => {
                  setSelected({ level: 'zip', id: rollup.zip, name: rollup.zip });
                  if (sampleBorrowerId) {
                    navigate(`/borrower-360/${sampleBorrowerId}`);
                  }
                }}
              />
              <text
                x={x + tileW / 2}
                y={y + tileH / 2 - 2}
                textAnchor="middle"
                fontSize="11"
                fontFamily="var(--font-mono)"
                fill="var(--text-1)"
                pointerEvents="none"
              >
                {rollup.zip}
              </text>
              <text
                x={x + tileW / 2}
                y={y + tileH / 2 + 14}
                textAnchor="middle"
                fontSize="9"
                fontFamily="var(--font-mono)"
                fill="var(--text-3)"
                pointerEvents="none"
              >
                {count !== null ? count.toLocaleString() : '—'}
              </text>
            </g>
          );
        })}
      </svg>
    );
  };

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
          {level === 'zip' && selected?.level === 'county' && (
            <>
              <Icon name="chevright" size={11} />
              <span className="filter is-active" style={{ padding: '3px 8px' }}>
                <span className="filter__value">{selected.name}</span>
              </span>
            </>
          )}
        </div>
      </div>

      {/* Drill hint chip */}
      <div style={{ position: 'absolute', top: 12, right: 14, zIndex: 2 }}>
        <Chip variant="neutral" icon="pin">
          {level === 'state'
            ? `Click ${Object.keys(supportedCountyStates).map((s) => s.toUpperCase()).join(', ')} to drill`
            : level === 'county'
              ? 'Click any county to drill'
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

      {/* Hover tooltip — infographic-style card. Wider layout (280px) with
          a dual-KPI grid (marketable population + avg score) + a top-segment
          callout + source lineage footer. Slice: hover-infographic-richness,
          2026-04-23. */}
      {hover && (
        <div
          className="map-tip"
          style={{
            left: Math.max(160, Math.min(window.innerWidth - 160, hover.x)),
            top: hover.y - 4,
          }}
        >
          <div className="map-tip__name">{hover.name}</div>
          <div className="map-tip__kpis">
            <div className="map-tip__kpi">
              <div className="map-tip__kpi-label">Marketable</div>
              <div className="map-tip__kpi-value">
                {hover.count !== null ? hover.count.toLocaleString() : '—'}
              </div>
            </div>
            <div className="map-tip__kpi">
              <div className="map-tip__kpi-label">Avg. score</div>
              <div className="map-tip__kpi-value">
                {hover.avgScore !== null ? hover.avgScore : '—'}
              </div>
            </div>
          </div>
          {hover.topSegment && (
            <div className="map-tip__seg">
              <span className="map-tip__seg-label">Top segment</span>
              <span className="map-tip__seg-value">{hover.topSegment}</span>
            </div>
          )}
          <div
            className="map-tip__row"
            style={{ marginTop: 0, borderTop: '1px dashed var(--line-1)', paddingTop: 'var(--sp-2)' }}
          >
            <span>Source</span>
            <span className="v mono" style={{ fontSize: 10 }}>
              {hover.sourceHint ?? 'mip.gold'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// Deprecated alias — kept so any downstream consumer importing the old
// name keeps compiling. Remove after the next release window.
export { USChoroplethMap as MapPlaceholder };

// ---------- Helpers -------------------------------------------------------

/**
 * Pulse beacon rendered over Illinois at the state level. The x/y coords are
 * in @svg-maps/usa units (viewBox "192 9 1028 746"). IL's path bbox centers
 * around x≈833 y≈300 in that projection — roughly Chicago's pixel position
 * on the Albers map, which doubles as the anchor metro hint for the
 * default drill. Slice 9 swapped this in for the former GeorgiaBeacon.
 *
 * When `reduceMotion` is `true` (user OS opt-out via
 * `prefers-reduced-motion: reduce`) we render only the static dot and
 * omit the SMIL `<animate>` children. CSS reduced-motion rules don't
 * cover SVG SMIL, so this guard has to live in the JSX. Hole-finder
 * finding #18, 2026-04-23.
 */
function IllinoisBeacon({ reduceMotion }: { reduceMotion: boolean }) {
  return (
    <g pointerEvents="none">
      {!reduceMotion && (
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
      )}
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
