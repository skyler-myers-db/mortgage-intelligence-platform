import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import type { Feature, FeatureCollection, Geometry } from 'geojson';
import { Icon } from '../Icon';
import { Chip } from '../Primitives';
import { api } from '../../lib/api';
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
// Keep all 50 states here because FIPS is stable geography metadata, not demo
// scope. The current tenant footprint and live gold rollups decide which
// states are active.
const USCODE_TO_FIPS: Record<string, string> = {
  al: '01', ak: '02', az: '04', ar: '05', ca: '06', co: '08', ct: '09',
  de: '10', dc: '11', fl: '12', ga: '13', hi: '15', id: '16', il: '17', in: '18',
  ia: '19', ks: '20', ky: '21', la: '22', me: '23', md: '24', ma: '25',
  mi: '26', mn: '27', ms: '28', mo: '29', mt: '30', ne: '31', nv: '32',
  nh: '33', nj: '34', nm: '35', ny: '36', nc: '37', nd: '38', oh: '39',
  ok: '40', or: '41', pa: '42', ri: '44', sc: '45', sd: '46', tn: '47',
  tx: '48', ut: '49', vt: '50', va: '51', wa: '53', wv: '54', wi: '55',
  wy: '56',
};

// Slice13-accuracy-validation: county + ZIP hover numbers now come from
// /api/geo/county-rollups + /api/geo/zip-rollups (backed by
// mip.gold.county_rollup + mip.gold.zip_rollup respectively). The prior
// local county/ZIP fixture literals are gone -- any county / ZIP not
// returned in the payload renders "—" on hover (honest null).
// The fill-level bucket is derived from addressable_borrowers below.

/** Fixed-threshold fallback used only when no distribution is available.
 *  The real map uses `buildQuantileBucketer` below so the
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
 *   - level state: 'state' → 'county' → 'zip' → Lead Queue deep-link.
 *
 * Upgrade vs. prototype: the prototype used hand-drawn stylized polygons. We
 * use @svg-maps/usa (Albers USA pre-projected paths, ~141 KB raw / ~30 KB
 * gzipped) for real US geography so it reads as a product, not a sketch.
 *
 * Geography scope is data-driven. Click a populated state to drill into
 * whatever counties are present in the live gold rollups, then ZIP-level
 * rollups. The UI copy uses backend scope labels rather than hardcoded demo
 * assumptions.
 */

// ---------- Live per-state facts ----------

interface StateFacts {
  count: number;
  avgScore: number;
  lvl: 1 | 2 | 3 | 4;
  topSegment?: string;
}

// Map segment code (from activeSegs / segmentFilter) → user-facing names.
// Used for filter hints and, when available, top-segment dim logic.
const SEGMENT_CODE_TO_NAME: Record<string, string> = {
  itm: 'In the Money',
  listed: 'Listed',
  permit: 'Permit Activity',
  investor: 'Investor',
  equity: 'Home Equity',
  retention: 'Retention',
};

function countyDisplayName(rollup: CountyRollup): string {
  const raw = rollup.county_name?.trim();
  if (raw) {
    return raw.toLowerCase().endsWith('county') ? raw : `${raw} County`;
  }
  return rollup.fips_5;
}

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
  id: string; // 5-digit county FIPS
  name: string;
  paths: string; // compound SVG path `d` built from rings
  cx: number;
  cy: number;
}

interface CountiesPayload {
  state: string;      // lowercase USPS code
  features: CountyFeature[];
  viewBox: string;    // pre-computed bbox margin applied
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
  /** any = overlap; all = borrower must carry every selected segment. */
  segmentFilterMode?: 'any' | 'all';
  /** Optional reviewed secondary predicates, forwarded to geo rollups and lead links. */
  portfolioCriteria?: Record<string, string | number | null | undefined>;
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
 * records.
 */
export function USChoroplethMap({
  height = 420,
  segmentFilter,
  segmentFilterMode = 'any',
  portfolioCriteria,
  onSelectionChange,
  drillBehavior = 'filter',
}: USChoroplethMapProps) {
  const [level, setLevel] = useState<Level>('state');
  const [selected, setSelected] = useState<Selected | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  // 2026-05-04 fix (#2): clear the floating map-tip whenever the user
  // drills into a different layer or selects a new region. Without this
  // a hover that was active on a state path would stay pinned to the
  // viewport (rendered via createPortal at fixed coords) after the user
  // clicked through to county view, because the path that fired the
  // mouseLeave handler is no longer in the DOM.
  useEffect(() => {
    setHover(null);
  }, [level, selected]);
  const [usaMap, setUsaMap] = useState<UsaSvgMap | null>(null);
  // Which supported state we drilled into (for county rendering).
  const [countyStateId, setCountyStateId] = useState<string | null>(null);
  const [countiesByState, setCountiesByState] = useState<Record<string, CountiesPayload>>({});
  const [countyLoadError, setCountyLoadError] = useState<string | null>(null);
  // Per-state rollups from /api/geo/state-rollups. `null` = loading; `{}`
  // = API unreachable. We keep the static geography interactive, but do
  // not surface static borrower counts while live rollups are loading.
  // Keyed by lowercase state code to match @svg-maps/usa location ids.
  const [liveStateFacts, setLiveStateFacts] = useState<Record<string, StateRollup> | null>(null);
  // Per-state county rollups lazy-loaded on drill. Keyed by uppercase
  // state code. Each value is a dict keyed by 5-char FIPS so the map's
  // county renderer can O(1) look up a county's live count / avg score.
  const [liveCountyFacts, setLiveCountyFacts] = useState<Record<string, Record<string, CountyRollup>>>({});
  // Per-county ZIP rollups lazy-loaded on county drill. Keyed by 5-char
  // county FIPS. Value is a dict keyed by 5-digit ZIP.
  const [liveZipFacts, setLiveZipFacts] = useState<Record<string, Record<string, ZipRollup>>>({});
  // Optional scope note surfaced from /api/geo/county-rollups when the
  // backend carries `scope_note` on the response. Keyed by uppercase
  // state code.
  const [countyScopeByState, setCountyScopeByState] = useState<Record<string, string>>({});
  const navigate = useNavigate();
  const footprint = useOptionalFootprint();

  // Footprint-aware drill allowlist. FIPS is intrinsic geography metadata,
  // while the active state set comes from FootprintProvider. Every configured
  // state can drill; renderCountyLevel handles the two cases:
  //   (a) TopoJSON has polygons for this state   -> render the polys.
  //   (b) TopoJSON does not                      -> render a scope card
  //       from live county rollups and let the user jump to ZIPs.
  const supportedCountyStates = useMemo<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const code of footprint.stateCodes) {
      const lc = code.toLowerCase();
      const fips = USCODE_TO_FIPS[lc];
      if (fips) out[lc] = fips;
    }
    return out;
  }, [footprint.stateCodes]);
  const segmentFilterKey = useMemo(
    () => (segmentFilter && segmentFilter.length > 0 ? segmentFilter.join(',') : ''),
    [segmentFilter],
  );
  const portfolioCriteriaKey = useMemo(
    () => JSON.stringify(portfolioCriteria ?? {}),
    [portfolioCriteria],
  );
  const leadQueuePath = useMemo(() => {
    return (geo: { state?: string; county?: string; zip?: string }) => {
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
    };
  }, [portfolioCriteria, segmentFilter, segmentFilterMode]);

  useEffect(() => {
    setHover(null);
    setLiveCountyFacts({});
    setLiveZipFacts({});
    setCountyScopeByState({});
  }, [segmentFilterKey, segmentFilterMode, portfolioCriteriaKey]);

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

  // Fetch per-state rollups from the backend. Counts, score, tint, and
  // top-segment labels all come from the live response; on error the map
  // stays interactive but metric fields render as unknown.
  //
  // 2026-05-04 (FIX G): the effect now re-runs whenever segmentFilter
  // changes so the per-state counts (and the choropleth bucketer
  // derived from them) reflect the active segment selection. Without
  // a filter we use the cross-segment _ALL row; with a filter we hit
  // the segment-aware path. Segment Intelligence passes mode="all" so
  // selecting another card narrows the population instead of expanding
  // to borrowers in either segment.
  useEffect(() => {
    let cancelled = false;
    setHover(null);
    setLiveStateFacts(null);
    api
      .stateRollups(
        segmentFilter && segmentFilter.length > 0 ? segmentFilter : null,
        undefined,
        segmentFilterMode,
        portfolioCriteria,
      )
      .then((payload) => {
        if (cancelled) return;
        const byCode: Record<string, StateRollup> = {};
        for (const r of payload.rollups) {
          byCode[r.state.toLowerCase()] = r;
        }
        setLiveStateFacts(byCode);
      })
      .catch(() => {
        // Keep the geography interactive, but do not surface static
        // borrower counts as if they were live. The tooltip renders
        // "—" until the rollup endpoint recovers.
        if (!cancelled) setLiveStateFacts({});
      });
    return () => {
      cancelled = true;
    };
  }, [segmentFilter, segmentFilterMode, portfolioCriteria, portfolioCriteriaKey]);

  // Lazy-fetch county rollups on drill. /api/geo/county-rollups?state=XX
  // returns real counts / avg_score / top_segment_code per FIPS from
  // mip.gold.county_rollup. Missing counties render "—" (honest null).
  useEffect(() => {
    if (level !== 'county' || !countyStateId) return;
    const stateUC = countyStateId.toUpperCase();
    if (liveCountyFacts[stateUC]) return;
    let cancelled = false;
    api
      .countyRollups(
        stateUC,
        undefined,
        segmentFilter && segmentFilter.length > 0 ? segmentFilter : null,
        segmentFilterMode,
        portfolioCriteria,
      )
      .then((payload) => {
        if (cancelled) return;
        const byFips: Record<string, CountyRollup> = {};
        for (const r of payload.rollups) byFips[r.fips_5] = r;
        setLiveCountyFacts((cur) => ({ ...cur, [stateUC]: byFips }));
        // Preserve the backend's scope_note verbatim when present so the
        // UI renders discovered geography coverage without fabricating copy.
        const scope =
          (payload as { scope_note?: string | null }).scope_note ?? null;
        if (scope) {
          setCountyScopeByState((cur) => ({ ...cur, [stateUC]: scope }));
        }
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
  }, [level, countyStateId, liveCountyFacts, segmentFilter, segmentFilterMode, portfolioCriteria, portfolioCriteriaKey]);

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
      .zipRollups(
        fips,
        undefined,
        segmentFilter && segmentFilter.length > 0 ? segmentFilter : null,
        segmentFilterMode,
        portfolioCriteria,
      )
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
  }, [level, selected, liveZipFacts, segmentFilter, segmentFilterMode, portfolioCriteria, portfolioCriteriaKey]);

  // Lazy-load real county polygons when drilled into a supported state.
  // us-counties.json is the national us-atlas county TopoJSON shipped as a
  // static asset in public/. topojson-client decodes it at runtime; both fetch
  // + import happen only on first county drill.
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
        // Empty features set = this state is in the footprint but not in
        // the shipped TopoJSON. Skip storing a payload so the live-rollup
        // fallback in renderCountyLevel takes over. Without this guard we'd
        // store an empty CountiesPayload with an Infinity viewBox and render
        // a blank SVG.
        if (stateFeatures.length === 0) return;
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
        const payload: CountiesPayload = {
          state: countyStateId,
          features,
          viewBox,
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

  /** Convert live state rollups into map facts. Borrower counts require
   *  the live endpoint so a fast hover cannot surface stale demo numbers. */
  // 2026-05-04 fix (#1, #7): compute a quantile bucketer over the LIVE
  // state counts (same approach already used at the county and ZIP layers) so the
  // visible color tier reflects actual borrower volume across whichever
  // states the workspace has data for.
  const stateBucketer = useMemo(() => {
    if (!liveStateFacts) return lvlFromCount;
    const counts = Object.values(liveStateFacts).map((r) => r.addressable);
    return buildQuantileBucketer(counts);
  }, [liveStateFacts]);

  /** Live state facts. Pre-rollup / failed-rollup states intentionally
   *  return undefined so the tooltip shows "—" instead of stale fallback
   *  borrower counts. */
  const factsFor = useMemo(() => {
    return (uscode: string): StateFacts | undefined => {
      const live = liveStateFacts?.[uscode];
      const liveTopSegment = live?.top_segment_code
        ? (SEGMENT_CODE_TO_NAME[live.top_segment_code] ?? '')
        : '';
      if (live) {
        return {
          count: live.addressable,
          avgScore: live.avg_score,
          lvl: stateBucketer(live.addressable),
          topSegment: liveTopSegment || undefined,
        };
      }
      return undefined;
    };
  }, [liveStateFacts, stateBucketer]);

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
      return 0;
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
        <div className="map-stage map-stage--empty">
          Loading geography…
        </div>
      );
    }
    return (
    <svg
      viewBox={usaMap.viewBox}
      preserveAspectRatio="xMidYMid meet"
      className="map-svg-stage"
    >
      {usaMap.locations.map((loc) => {
        const facts = factsFor(loc.id);
        const inFootprint = Boolean(supportedCountyStates[loc.id]);
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
            // Always show a tooltip on hover. In-footprint states surface
            // the live rollup; out-of-footprint states surface an honest
            // "outside Cotality evaluation scope" card so the user never
            // hovers a state and sees nothing. Prior behavior short-
            // circuited on `facts &&` which silently suppressed every
            // non-footprint state and was read as a broken hover.
            onMouseEnter={(e) =>
              setHover({
                x: e.clientX,
                y: e.clientY,
                name: loc.name,
                count: facts ? facts.count : null,
                avgScore: facts ? facts.avgScore : null,
                topSegment: facts?.topSegment || undefined,
                sourceHint: inFootprint
                  ? 'mip.gold.state_rollup'
                  : 'Outside Cotality evaluation scope',
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
                  navigate(leadQueuePath({ state: loc.id.toUpperCase() }));
                }
                return;
              }
              if (inFootprint) {
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
                if (facts) navigate(leadQueuePath({ state: loc.id.toUpperCase() }));
                return;
              }
              if (inFootprint) {
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
    </svg>
    );
  };

  // ----- COUNTY level: real county polygons from TopoJSON -----------------
  // Falls back to a live-rollup scope card only if the county polygons for the
  // state are unavailable. The county name/FIPS comes from the backend, not a
  // baked demo lookup.
  const renderCountyLevel = () => {
    const payload = countyStateId ? countiesByState[countyStateId] : null;
    const stateName = countyStateId
      ? usaMap?.locations.find((l) => l.id === countyStateId)?.name ?? ''
      : '';
    const stateUC = countyStateId?.toUpperCase() ?? '';
    const countyFactsForState = liveCountyFacts[stateUC];
    const primaryCounty = countyFactsForState
      ? Object.values(countyFactsForState).sort(
        (a, b) => (b.addressable_borrowers ?? 0) - (a.addressable_borrowers ?? 0),
      )[0]
      : null;
    if (!payload && !countyLoadError && primaryCounty) {
      const count = primaryCounty.addressable_borrowers ?? null;
      const avgScore = primaryCounty.avg_opportunity_score ?? null;
      const countyName = countyDisplayName(primaryCounty);
      const scopeLabel =
        countyScopeByState[stateUC] ??
        footprint.dataScope?.scope_label ??
        'Cotality data coverage discovered from gold county rollups';
      return (
        <div className="map-stage">
          <div className="map-center-card">
            <div className="eyebrow">Cotality data coverage</div>
            <div className="h-3 text-1">
              {countyName}, {stateName}
            </div>
            <div className="body muted map-center-copy">
              {scopeLabel}. Drill into {countyName} to see ZIP-level rollups.
            </div>
            <div className="map-center-stats">
              <span>
                Marketable{' '}
                <span className="text-1">
                  {count !== null ? count.toLocaleString() : '—'}
                </span>
              </span>
              <span>
                Avg. score{' '}
                <span className="text-1">
                  {avgScore !== null ? avgScore : '—'}
                </span>
              </span>
            </div>
            <div className="map-center-actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => {
                  setLevel('zip');
                  setSelected({ level: 'county', id: primaryCounty.fips_5, name: countyName });
                }}
              >
                Drill into {countyName} ZIPs
              </button>
            </div>
          </div>
        </div>
      );
    }
    if (!payload) {
      // Loading or error fallback. Do not render state-specific demo
      // geometry; county shapes and counts must come from the national
      // TopoJSON + live rollup payload.
      return (
        <div className="map-stage map-stage--empty">
          {countyLoadError ? (
            <div className="map-center-card map-center-card--narrow">
              <div className="text-2 mb-2">{countyLoadError}</div>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => {
                  setCountyLoadError(null);
                  setCountiesByState((c) => {
                    const next = { ...c };
                    if (countyStateId) delete next[countyStateId];
                    return next;
                  });
                }}
              >
                Retry county map
              </button>
            </div>
          ) : (
            'Loading counties…'
          )}
        </div>
      );
    }

    return (
      <svg
        viewBox={payload.viewBox}
        preserveAspectRatio="xMidYMid meet"
        className="map-svg-stage"
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
      </svg>
    );
  };

  // ----- ZIP level: tile grid for the active county. -----------------------
  // The ZIP grid is generated from the live ZIP rollup payload for every
  // county, so the component has no county-specific visual exceptions.
  const renderZipLevel = () => {
    const countyFips = selected?.level === 'county' ? selected.id : null;
    const countyName = selected?.level === 'county' ? selected.name : '';
    const stateUC =
      countyStateId?.toUpperCase() ??
      (countyFips ? Object.entries(USCODE_TO_FIPS).find(([, v]) => countyFips.startsWith(v))?.[0].toUpperCase() ?? '' : '');
    if (!countyFips) return null;
    const byZip = liveZipFacts[countyFips];
    const zipsFromApi = byZip ? Object.values(byZip) : [];

    if (byZip && zipsFromApi.length === 0) {
      // API returned empty — county outside the Cotality eval share or
      // the CTAS hasn't populated ZIPs for it. Give the user a graceful
      // fallback path. The Lead Queue preserves the county filter via
      // ?state=XX&county=FFFFF; the Queue resolves "borrowers in this
      // county" client-side by intersecting ZIPs (see
      // /lead-queue.tsx::countyZips).
      return (
        <div className="map-stage">
          <div className="map-center-card map-center-card--narrow">
            <div className="text-2 mb-2">
              No ZIP-level rollup for {countyName}.
            </div>
            <div className="mb-3">
              Browse this county&apos;s lead queue — the filter will narrow to borrowers in {countyName}.
            </div>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => navigate(leadQueuePath({ state: stateUC, county: countyFips }))}
            >
              Open Lead Queue for {countyName}
            </button>
          </div>
        </div>
      );
    }

    if (!byZip) {
      return (
        <div className="map-stage map-stage--empty">
          Loading ZIPs…
        </div>
      );
    }

    // HTML/CSS grid (not SVG) so
    // the tiles use design-system tokens (r-md, sp, typography) and sit
    // cleanly in the map viewport, not overlapping the breadcrumbs above.
    // Sorted descending by count so densest ZIPs land top-left (Pareto).
    // Click deep-links to the filtered Lead Queue — seeing all borrowers
    // in the ZIP is the user's actual goal, not a single random sample.
    const sorted = [...zipsFromApi].sort(
      (a, b) => (b.addressable_borrowers ?? 0) - (a.addressable_borrowers ?? 0),
    );
    const visible = sorted.slice(0, 24);
    return (
      <div className="zip-tiles" role="list" aria-label={`ZIPs in ${countyName}`}>
        {visible.map((rollup) => {
          const count = rollup.addressable_borrowers ?? null;
          const avgScore = rollup.avg_opportunity_score ?? null;
          const topSegCode = rollup.top_segment_code ?? null;
          const topSegment = topSegCode ? SEGMENT_CODE_TO_NAME[topSegCode] : undefined;
          const lvl = zipBucketer(count);
          const isSelected =
            selected?.level === 'zip' && selected.id === rollup.zip;
          const classes = [
            'zip-tile',
            `zip-tile--lvl-${lvl}`,
            isSelected ? 'is-selected' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <button
              key={rollup.zip}
              type="button"
              className={classes}
              role="listitem"
              aria-label={`ZIP ${rollup.zip}, ${count !== null ? `${count.toLocaleString()} borrowers` : 'no data'}`}
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
	                navigate(leadQueuePath({ state: stateUC, county: countyFips, zip: rollup.zip }));
	              }}
	            >
              <span className="zip-tile__code">{rollup.zip}</span>
              <span className="zip-tile__count">
                {count !== null ? count.toLocaleString() : '—'}
              </span>
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div className="map-wrap" style={{ height }}>
      <div className="map-hdr">
        {/* Breadcrumbs */}
        <div className="map-crumbs">
          <div className="eyebrow">Geography drill-down</div>
          <div className="topbar__crumbs map-crumbs__trail">
            <button
              type="button"
              className={`filter filter--compact ${level === 'state' ? 'is-active' : ''}`}
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
                  className={`filter filter--compact ${level === 'county' ? 'is-active' : ''}`}
                  onClick={() => {
                    setLevel('county');
                    const st = countyStateId ?? 'il';
                    const stName = usaMap?.locations.find((l) => l.id === st)?.name ?? 'State';
                    setSelected({ level: 'state', id: st, name: stName });
                  }}
                >
                  <span className="filter__value">
                    {countyStateId
                      ? usaMap?.locations.find((l) => l.id === countyStateId)?.name ?? 'State'
                      : 'State'}
                  </span>
                </button>
              </>
            )}
            {level === 'zip' && selected?.level === 'county' && (
              <>
                <Icon name="chevright" size={11} />
                <span className="filter filter--compact is-active">
                  <span className="filter__value">{selected.name}</span>
                </span>
              </>
            )}
          </div>
        </div>

        {/* Drill hint chip + optional Cotality coverage chip. The scope copy
            comes from backend-discovered gold rollups instead of a fixed demo
            statement. */}
        <div className="map-corner-chips">
          <Chip variant="neutral" icon="pin">
            {level === 'state'
              ? footprint.dataScope?.county_count
                ? `${footprint.dataScope.county_count.toLocaleString()} counties · click to drill`
                : 'Loading coverage…'
              : level === 'county'
                ? (countyStateId && countyScopeByState[countyStateId.toUpperCase()])
                  ? countyScopeByState[countyStateId.toUpperCase()]
                  : footprint.dataScope?.scope_label ?? 'Cotality data coverage'
                : `ZIPs in ${selected?.level === 'county' ? selected.name : 'county'}`}
          </Chip>
          {level !== 'state' && countyStateId && countyScopeByState[countyStateId.toUpperCase()] && level !== 'county' && (
            <Chip variant="neutral" icon="db">
              {countyScopeByState[countyStateId.toUpperCase()]}
            </Chip>
          )}
        </div>
      </div>

      {level === 'state' && renderStateLevel()}
      {level === 'county' && renderCountyLevel()}
      {level === 'zip' && renderZipLevel()}

      {/* Legend — explicit "Colored by" label so the user understands why
          the home map and the segments map can render different hues for
          the same state. On segments-with-filter, the gradient reflects
          quantiles within the filtered segment; on home it's the full
          marketable population. Fix G, 2026-04-23. */}
      <div className="map-legend">
        <div className="map-legend__header">
          <span>
            Borrowers in selection{' '}
            <span className="map-legend__value">
              {totalCount.toLocaleString()}
            </span>
          </span>
        </div>
        <div className="map-legend__bar">
          <span className="lvl-0" />
          <span className="lvl-1" />
          <span className="lvl-2" />
          <span className="lvl-3" />
          <span className="lvl-4" />
        </div>
        <div className="map-legend__range">
          <span>Lower</span>
          <span>Higher</span>
        </div>
        <div className="map-legend__caption">
          Colored by:{' '}
          <span className="text-2">
            {segmentFilter && segmentFilter.length > 0
              ? `opportunity within ${segmentFilter.join(', ')}`
              : 'marketable population'}
          </span>
        </div>
      </div>

      {/* Hover tooltip — infographic-style card. Portaled to document.body
          so `.map-wrap { overflow: hidden }` can never clip it and so the
          stacking context of a containing surface can't pull it beneath
          another card. Absolute position is computed from hover.x/y
          (client coords), clamped to the viewport. Slice: hover-infographic-
          richness + portal-escape, 2026-04-23.

          2026-05-04 (FIX G): the per-state count IS now segment-aware —
          the upstream useEffect re-fetches /api/geo/state-rollups with a
          `segment_codes` query param whenever segmentFilter changes, and
          the backend hits scalar `array_contains` predicates against
          mip.gold.borrower_360. So the tooltip no longer
          needs the "(all segments)" disclaimer or the explicit "Filter:
          shading by …" footer the previous slice introduced — both
          would be misleading now that the number IS the filtered count.
          A small "filtered by …" hint stays so the user remembers the
          context, but it's a single-line clarifier, not a disclaimer. */}
      {hover &&
        createPortal(
          <div
            className="map-tip"
            style={{
              position: 'fixed',
              left: Math.max(160, Math.min(window.innerWidth - 160, hover.x)),
              top: hover.y - 4,
            }}
          >
            <div className="map-tip__name">{hover.name}</div>
            <div className="map-tip__kpis">
              <div className="map-tip__kpi">
                {/* "Marketable borrowers" — the count IS now segment-
                    aware when a filter is active (FIX G, 2026-05-04),
                    so the prior "(all segments)" suffix is gone.
                    A small filtered-by hint appears in the footer
                    instead, and the count itself reflects the filter. */}
                <div className="map-tip__kpi-label">Marketable borrowers</div>
                <div className="map-tip__kpi-value">
                  {hover.count !== null ? hover.count.toLocaleString() : '—'}
                </div>
              </div>
              <div className="map-tip__kpi">
                {/* Label was "Avg. score" — too ambiguous (score of what?).
                    Now "Avg. opportunity score" so the metric ties to the
                    same fn_lead_score primitive used everywhere else.
                    2026-05-04 user feedback. */}
                <div className="map-tip__kpi-label">Avg. opportunity score</div>
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
            {/* Filter hint — present when a segment filter is active.
                Reminds the reader the shown count is the segment-
                filtered population, not the cross-segment total.
                FIX G, 2026-05-04: the count itself is now segment-
                aware (was just "all segments" before), so the copy
                reads "filtered by …" not "shading by …". */}
            {activeSegNames !== null && (
              <div
                className="map-tip__row map-tip__row--compact map-tip__row--muted"
              >
                <span>Filter</span>
                <span className="v map-tip__value--small">
                  filtered by {Array.from(activeSegNames).join(', ')}
                </span>
              </div>
            )}
            <div
              className="map-tip__row map-tip__row--compact"
            >
              <span>Source</span>
              <span className="v mono map-tip__value--small">
                {hover.sourceHint ?? 'mip.gold'}
              </span>
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
