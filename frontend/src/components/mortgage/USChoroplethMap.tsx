import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router';
import { Icon } from '../Icon';
import { Chip, EvidenceChip } from '../Primitives';
import { api } from '../../lib/api';
import type { GeoAssignmentOverlayResponse, GeoAssignmentOverlayUnit, GeoOverlayLevel } from '../../lib/api';
import { ApiError } from '../../lib/api';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { buildCampaignPrefillSearch, makeCampaignPrefill } from '../../lib/campaignPrefill';
import { useOptionalFootprint } from '../FootprintProvider';
import type { StateRollup, ZipRollup } from '../../types';
import {
  USCODE_TO_FIPS,
  buildLeadQueuePath,
  buildQuantileBucketer,
  lvlFromCount,
  type HoverState,
  type Level,
  type Selected,
  type StateFacts,
  type UsaSvgMap,
} from './USChoroplethMap.utils';
import { loadUsaStateMap } from './USStateMapData';
import { USChoroplethMapTooltip } from './USChoroplethMapTooltip';
import { safeSegmentName } from '../../lib/segmentMetadata';

// State + ZIP hover numbers come from /api/geo/state-rollups and
// /api/geo/zip-rollups (backed by mip.gold.funnel_snapshot_daily and
// mip.gold.zip_rollup). There are no local fixture literals -- any state /
// ZIP not returned in the payload renders "—" on hover (honest null).
// The fill-level bucket is derived from addressable_borrowers below.
/** Densest-N ZIP tiles rendered per state. The grid stays readable, but
 *  the remainder MUST be disclosed — see the reconcile note in
 *  `renderZipLevel`. */
const ZIP_TILE_CAP = 24;

/**
 * USChoroplethMap — real interactive US state map with click-to-drill.
 *
 * Matches the prototype's `ChoroplethMap`:
 *   - .map-wrap chassis with breadcrumbs, drill-hint chip, legend, map-tip.
 *   - map-region lvl-1..4 fills (color-mix with --accent), is-selected accent.
 *   - level state: 'state' → 'zip' → Lead Queue deep-link.
 *
 * Upgrade vs. prototype: the prototype used hand-drawn stylized polygons. We
 * use us-atlas state TopoJSON (Albers USA pre-projected paths) for real US
 * geography so it reads as a product, not a sketch.
 *
 * DESIGN-CONTRACT DEVIATION, 2026-08-08. The prototype's ChoroplethMap
 * drills state → county → ZIP (`design_files/Module 0 Prototype.html`
 * ~L1802-1812: `level === 'county' ? TX_COUNTIES : TRAVIS_ZIPS`). We drop
 * the county step. The Cotality share carries exactly one county FIPS per
 * state, so `county_fips_5` is NULL on all 5,156,184 borrower_360 rows and
 * all 677 zip_rollup rows once the fabricated keys were removed as
 * dishonest. The county level rendered unfilled polygons whose every hover
 * read "outside the footprint", and county boundaries cannot be drawn
 * truthfully at all. The prototype's breadcrumb/hint-chip vocabulary is
 * preserved exactly — only the middle rung is gone. Restore the level when
 * a licensed county dataset lands; `/api/geo/county-rollups` and the county
 * geometry helpers in `./USChoroplethMap.utils` are kept for that.
 *
 * Geography scope is data-driven: click a populated state to drill into
 * whatever ZIPs are present in the live gold rollups. The UI copy uses
 * backend scope labels rather than hardcoded demo assumptions.
 */

/** Selection payload emitted on every state/ZIP click. State is a 2-char
 *  uppercase USPS code so consumers can run predicates against
 *  `LeadSummary.state` directly; `zip` is the 5-digit string. `null` means
 *  the user navigated back to US level.
 *
 *  `county` is always null since 2026-08-08 — the map has no county level.
 *  The field stays on the contract (consumers still forward it to
 *  `/api/leads`, which still accepts a county predicate) so a licensed
 *  county dataset needs no consumer change. */
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

/**
 * US Choropleth Map — state → ZIP drill-down over Cotality public records.
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
  // Which state we drilled into (lowercase, matching map location ids).
  // At ZIP level this is the drilled context and stays put while the user
  // hovers/clicks individual tiles.
  const [drillStateId, setDrillStateId] = useState<string | null>(null);
  // Per-state rollups from /api/geo/state-rollups. `null` = loading; `{}`
  // = API unreachable. We keep the static geography interactive, but do
  // not surface static borrower counts while live rollups are loading.
  // Keyed by lowercase state code to match state map location ids.
  const [liveStateFacts, setLiveStateFacts] = useState<Record<string, StateRollup> | null>(null);
  // Per-state ZIP rollups lazy-loaded on drill. Keyed by UPPERCASE state
  // code (the API key); each value is a dict keyed by 5-digit ZIP so the
  // tile renderer can O(1) look up a ZIP's live count / avg score.
  const [liveZipFacts, setLiveZipFacts] = useState<Record<string, Record<string, ZipRollup>>>({});
  // S9 assigned-vs-unattended overlay. `overlayOn` toggles the recolor +
  // tooltip extension. `overlayData` is the response for the CURRENT drill
  // level; `null` = not yet loaded, `overlayError` = fetch failed (degraded
  // note in the legend, base borrower view stays functional).
  const [overlayOn, setOverlayOn] = useState(false);
  const [overlayData, setOverlayData] = useState<GeoAssignmentOverlayResponse | null>(null);
  const [overlayError, setOverlayError] = useState<string | null>(null);
  const [overlayLoading, setOverlayLoading] = useState(false);
  const navigate = useNavigate();
  const footprint = useOptionalFootprint();

  // Footprint-aware drill allowlist: the active state set comes from
  // FootprintProvider, filtered through the intrinsic USPS->FIPS table so a
  // malformed configured code can never become a drillable region. Every
  // configured state drills straight to its ZIP tiles.
  const footprintStates = useMemo<Record<string, string>>(() => {
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
      return buildLeadQueuePath({ geo, segmentFilter, segmentFilterMode, portfolioCriteria });
    };
  }, [portfolioCriteria, segmentFilter, segmentFilterMode]);

  useEffect(() => {
    setHover(null);
    setLiveZipFacts({});
  }, [segmentFilterKey, segmentFilterMode, portfolioCriteriaKey]);

  // Lazy-load the state geography so the TopoJSON conversion lands in its
  // own code-split chunk instead of the main bundle.
  useEffect(() => {
    let cancelled = false;
    loadUsaStateMap().then((map) => {
      if (!cancelled) setUsaMap(map);
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
  // the segment-aware path. `segmentFilterMode="any"` counts a
  // de-duplicated OR cohort; `segmentFilterMode="all"` counts borrowers
  // that match every selected segment.
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

  // Lazy-fetch ZIP rollups when the user drills into a state.
  // /api/geo/zip-rollups?state=XX reads mip.gold.zip_rollup on its real
  // grain. A state with no ZIP rows resolves to an empty list; the ZIP
  // renderer shows an honest empty state with a Lead Queue fallback.
  useEffect(() => {
    if (level !== 'zip' || !drillStateId) return;
    const stateUC = drillStateId.toUpperCase();
    if (liveZipFacts[stateUC]) return;
    let cancelled = false;
    api
      .zipRollups(
        { state: stateUC },
        undefined,
        segmentFilter && segmentFilter.length > 0 ? segmentFilter : null,
        segmentFilterMode,
        portfolioCriteria,
      )
      .then((payload) => {
        if (cancelled) return;
        const byZip: Record<string, ZipRollup> = {};
        for (const r of payload.rollups) byZip[r.zip] = r;
        setLiveZipFacts((cur) => ({ ...cur, [stateUC]: byZip }));
      })
      .catch(() => {
        // Empty dict means "we tried, nothing came back" -- the renderer
        // shows the empty state instead of re-fetching on every render.
        if (!cancelled) setLiveZipFacts((cur) => ({ ...cur, [stateUC]: {} }));
      });
    return () => {
      cancelled = true;
    };
  }, [level, drillStateId, liveZipFacts, segmentFilter, segmentFilterMode, portfolioCriteria, portfolioCriteriaKey]);

  // S9 overlay fetch. Re-runs on drill level + toggle. Each level maps to a
  // distinct /api/geo/assignment-overlay call (state | zip+state). A fetch
  // failure sets an honest degraded note and leaves the base borrower view
  // untouched -- never a silent fallback.
  useEffect(() => {
    if (!overlayOn) {
      setOverlayData(null);
      setOverlayError(null);
      setOverlayLoading(false);
      return;
    }
    // Determine the request for the current drill level. Skip until the
    // parent state needed for a ZIP request is known. The ZIP overlay keys
    // on the same state as the tiles it recolors.
    let request: { level: GeoOverlayLevel; state?: string | null; countyFips?: string | null } | null = null;
    if (level === 'state') {
      request = { level: 'state' };
    } else if (level === 'zip' && drillStateId) {
      request = { level: 'zip', state: drillStateId.toUpperCase() };
    }
    if (!request) {
      setOverlayData(null);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    setOverlayLoading(true);
    setOverlayError(null);
    setOverlayData(null);
    api
      .assignmentOverlay(request.level, {
        state: request.state,
        countyFips: request.countyFips,
        signal: controller.signal,
      })
      .then((payload) => {
        if (cancelled) return;
        setOverlayData(payload);
        setOverlayLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled || (err instanceof ApiError && err.aborted)) return;
        const dep = err instanceof ApiError && err.dependency ? ` (${err.dependency})` : '';
        setOverlayError(`Coverage overlay unavailable${dep}. Showing borrower counts.`);
        setOverlayData(null);
        setOverlayLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [overlayOn, level, drillStateId]);

  // The overlay only drives coloring once its payload is actually loaded;
  // while loading or degraded the base borrower coloring stays up (the
  // legend explains the state) — toggling the overlay must never blank
  // the map.
  const overlayActive = overlayOn && overlayData !== null;

  // Overlay units keyed by unit_id (USPS lowercase at state level to match
  // map location ids; FIPS / ZIP verbatim otherwise) for O(1) lookup.
  const overlayByUnit = useMemo(() => {
    const out: Record<string, GeoAssignmentOverlayUnit> = {};
    if (!overlayData) return out;
    for (const unit of overlayData.units) {
      const key = overlayData.level === 'state' ? unit.unit_id.toLowerCase() : unit.unit_id;
      out[key] = unit;
    }
    return out;
  }, [overlayData]);

  // Quantile bucketer over unattended counts for the active overlay level.
  const overlayBucketer = useMemo(() => {
    if (!overlayData) return lvlFromCount;
    const counts = overlayData.units.map((u) => u.unattended_count);
    return buildQuantileBucketer(counts);
  }, [overlayData]);

  const activeSegNames = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return null;
    return new Set(
      segmentFilter.map((c) => safeSegmentName(c)).filter((n): n is string => Boolean(n)),
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
        ? (safeSegmentName(live.top_segment_code) ?? '')
        : '';
      if (live) {
        return {
          count: live.addressable,
          avgScore: live.avg_score,
          lvl: stateBucketer(live.addressable),
          topSegment: liveTopSegment || undefined,
          contactable: live.contactable ?? null,
          zipUnassigned: live.zip_unassigned_count ?? null,
        };
      }
      return undefined;
    };
  }, [liveStateFacts, stateBucketer]);

  // Quantile bucketer for the ZIP layer of the currently-drilled state.
  // Computed per-payload so the gradient reads whether the real counts are
  // in the hundreds or the hundred-thousands (see `buildQuantileBucketer`).
  const zipBucketer = useMemo(() => {
    const stateUC = drillStateId?.toUpperCase() ?? '';
    const byZip = liveZipFacts[stateUC];
    if (!byZip) return lvlFromCount;
    const counts = Object.values(byZip).map((r) => r.addressable_borrowers ?? 0);
    return buildQuantileBucketer(counts);
  }, [drillStateId, liveZipFacts]);

  // Fire the selection callback when the user drills into / out of a
  // level. Collecting into a single effect keeps the producer logic in
  // the click handlers pure (they just call setState).
  useEffect(() => {
    if (!onSelectionChange) return;
    const stateCode =
      selected?.level === 'state'
        ? selected.id.toUpperCase()
        : drillStateId
          ? drillStateId.toUpperCase()
          : null;
    const zip = selected?.level === 'zip' ? selected.id : null;
    // `county` is always null: the map has no county level (see
    // MapSelection). Consumers keep forwarding the field unchanged.
    onSelectionChange({ state: stateCode, county: null, zip });
  }, [selected, drillStateId, onSelectionChange]);

  const totalCount = useMemo(() => {
    if (level === 'state') {
      if (liveStateFacts && Object.keys(liveStateFacts).length > 0) {
        return Object.values(liveStateFacts).reduce((a, b) => a + b.addressable, 0);
      }
      return 0;
    }
    // ZIP level — key off the drilled state.
    const stateUC = drillStateId?.toUpperCase() ?? '';
    const liveByZip = stateUC ? liveZipFacts[stateUC] : undefined;
    if (liveByZip) {
      return Object.values(liveByZip).reduce(
        (a, r) => a + (r.addressable_borrowers ?? 0),
        0,
      );
    }
    return 0;
  }, [level, drillStateId, liveStateFacts, liveZipFacts]);
  const mapBusy = useMemo(() => {
    if (!usaMap || liveStateFacts === null) return true;
    if (level === 'zip') {
      const stateUC = drillStateId?.toUpperCase() ?? '';
      return Boolean(stateUC && liveZipFacts[stateUC] === undefined);
    }
    return false;
  }, [drillStateId, level, liveStateFacts, liveZipFacts, usaMap]);
  // Borrowers in the drilled state that the ZIP layer cannot show. The
  // backend derives it as (state total - sum of ZIP tiles) off one refresh
  // anchor, so it IS the on-screen gap rather than a second estimate of it.
  const zipUnassignedForDrill = useMemo(() => {
    if (!drillStateId) return 0;
    return liveStateFacts?.[drillStateId]?.zip_unassigned_count ?? 0;
  }, [drillStateId, liveStateFacts]);
  const drillStateName = useMemo(() => {
    if (!drillStateId) return '';
    return (
      usaMap?.locations.find((l) => l.id === drillStateId)?.name
      ?? drillStateId.toUpperCase()
    );
  }, [drillStateId, usaMap]);
  const mapStatus = useMemo(() => {
    if (!usaMap) return 'Loading geography.';
    if (liveStateFacts === null) return 'Loading state borrower rollups.';
    if (level === 'zip') {
      const stateUC = drillStateId?.toUpperCase() ?? '';
      if (stateUC && liveZipFacts[stateUC] === undefined) {
        return `Loading ZIP rollups for ${drillStateName || 'state'}.`;
      }
    }
    return 'Geography rollups loaded.';
  }, [drillStateId, drillStateName, level, liveStateFacts, liveZipFacts, usaMap]);
  const segmentCaption = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return 'marketable population';
    const labels = segmentFilter.map((code) => safeSegmentName(code) ?? 'Unknown segment');
    return `opportunity within ${labels.join(', ')}`;
  }, [segmentFilter]);

  // The state USPS code for the currently-drilled geography, resolved from
  // whichever level the user is on. Used for the "Start campaign" prefill.
  const activeStateCode = useMemo(() => {
    if (selected?.level === 'state') return selected.id.toUpperCase();
    if (drillStateId) return drillStateId.toUpperCase();
    return null;
  }, [selected, drillStateId]);

  // S9 "Start campaign from this geography" link target. The state is the
  // drilled unit at both levels — it stays the campaign context while its
  // ZIP grid is on screen, because a zip-tile click deep-links to the Lead
  // Queue by the existing map contract (a persistent per-ZIP selection does
  // not exist today; the typed prefill contract still carries `zip` for
  // S10). Carries the current segment filter + mode and, when the overlay
  // is loaded, the state's lead / unattended snapshot counts.
  const campaignPrefillPath = useMemo(() => {
    if (!activeStateCode || selected?.level !== 'state') return null;
    let leadCount: number | null = null;
    let unattendedCount: number | null = null;
    if (overlayOn && overlayData) {
      if (overlayData.level === 'zip') {
        // ZIP grid on screen: the state snapshot is the sum of its ZIP
        // units — the totals the overlay response already carries.
        leadCount = overlayData.total_leads;
        unattendedCount = overlayData.total_unattended;
      } else {
        const unit = overlayByUnit[selected.id.toLowerCase()];
        leadCount = unit ? unit.lead_count : null;
        unattendedCount = unit ? unit.unattended_count : null;
      }
    }
    try {
      const prefill = makeCampaignPrefill({
        level: 'state',
        state: activeStateCode,
        countyFips: null,
        countyName: null,
        segmentCodes: segmentFilter ?? [],
        segmentMode: segmentFilterMode,
        leadCount,
        unattendedCount,
      });
      return `/portfolio-builder?${buildCampaignPrefillSearch(prefill).toString()}`;
    } catch {
      // A geography we can't encode coherently just hides the affordance
      // rather than shipping a broken link.
      return null;
    }
  }, [
    activeStateCode,
    selected,
    overlayOn,
    overlayData,
    overlayByUnit,
    segmentFilter,
    segmentFilterMode,
  ]);

  // ----- STATE level: real US paths via us-atlas ---------------------------
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
        const inFootprint = Boolean(footprintStates[loc.id]);
        const stateFactsLoading = liveStateFacts === null;
        const overlayUnit = overlayActive ? overlayByUnit[loc.id] : undefined;
        // When the overlay is loaded, the fill tier reflects UNATTENDED
        // leads, not addressable borrowers. Base borrower facts stay in
        // the hover.
        const overlayLvl =
          overlayUnit && overlayUnit.unattended_count > 0
            ? overlayBucketer(overlayUnit.unattended_count)
            : null;
        const lvl = overlayActive ? overlayLvl ?? 1 : facts?.lvl ?? 1;
        const hasFill = overlayActive ? overlayLvl !== null : Boolean(facts);
        const dim =
          activeSegNames !== null && facts && facts.topSegment && !activeSegNames.has(facts.topSegment);
        const classes = [
          'map-region',
          stateFactsLoading ? 'is-loading' : '',
          !stateFactsLoading && hasFill ? 'has-data' : '',
          !stateFactsLoading && !hasFill ? 'is-empty' : '',
          hasFill ? `lvl-${lvl}` : '',
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
            data-target-size-exempt="geographic-shape"
            aria-label={loc.name}
            aria-keyshortcuts="Enter"
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
                  ? 'mip.gold.funnel_snapshot_daily + mip.gold.state_top_segment'
                  : 'Outside Cotality evaluation scope',
                // Both gaps are disclosed on the tile, BEFORE the click,
                // so the smaller number the user lands on is expected
                // rather than discovered: `contactable` is the subset the
                // Lead Queue behind this tile will show (the queue applies
                // the eligibility predicate, the tile count does not), and
                // `zipUnassigned` is the subset the ZIP drill cannot show.
                contactable: facts?.contactable ?? null,
                zipUnassigned: facts?.zipUnassigned ?? null,
                overlay: overlayUnit
                  ? {
                      leadCount: overlayUnit.lead_count,
                      assignedCount: overlayUnit.assigned_count,
                      unattendedCount: overlayUnit.unattended_count,
                      coveringOfficerCount: overlayUnit.covering_officer_count,
                      coveringOfficers:
                        selected?.level === 'state' && selected.id === loc.id
                          ? overlayUnit.covering_officers
                          : undefined,
                    }
                  : undefined,
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
                // Straight to ZIPs — there is no honest county rung.
                setLevel('zip');
                setDrillStateId(loc.id);
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
                // Straight to ZIPs — there is no honest county rung.
                setLevel('zip');
                setDrillStateId(loc.id);
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

  // ----- ZIP level: tile grid for the drilled state. -----------------------
  // The ZIP grid is generated from the live ZIP rollup payload for every
  // state, so the component has no state-specific visual exceptions.
  const renderZipLevel = () => {
    const stateUC = drillStateId?.toUpperCase() ?? '';
    if (!stateUC) return null;
    const byZip = liveZipFacts[stateUC];
    const zipsFromApi = byZip ? Object.values(byZip) : [];

    if (byZip && zipsFromApi.length === 0) {
      // API returned empty — the state is outside the Cotality eval share
      // or the CTAS hasn't populated ZIPs for it. Give the user a graceful
      // fallback path into the state's lead queue.
      return (
        <div className="map-stage">
          <div className="map-center-card map-center-card--narrow">
            <div className="text-2 mb-2">
              No ZIP-level rollup for {drillStateName}.
            </div>
            <div className="mb-3">
              Browse this state&apos;s lead queue — the filter will narrow to borrowers in {drillStateName}.
            </div>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => navigate(leadQueuePath({ state: stateUC }))}
            >
              Open Lead Queue for {drillStateName}
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
    const visible = sorted.slice(0, ZIP_TILE_CAP);
    // Reconcile what the tiles show against what the STATE tile claimed.
    // Two independent ways the drill under-counts, both previously silent:
    //   1. the tile cap hides the tail (IL: 24 of 212 ZIPs = 29.9% of the
    //      state's borrowers, 1,297,115 invisible — live audit 2026-08-10);
    //   2. borrowers with no ZIP never appear in zip_rollup at all
    //      (79,496 across the footprint; CO 8.7%, WA 5.8%).
    // A drill-down that silently shows a third of the population reads as a
    // broken widget, so state it.
    const visibleSum = visible.reduce(
      (total, rollup) => total + (rollup.addressable_borrowers ?? 0),
      0,
    );
    // liveStateFacts is keyed by LOWERCASE state code (see the rollup fetch).
    const stateFacts = liveStateFacts?.[stateUC.toLowerCase()];
    const stateTotal = stateFacts?.addressable ?? null;
    const unassigned = stateFacts?.zip_unassigned_count ?? null;
    const hiddenZipCount = sorted.length - visible.length;
    return (
      <>
      <div className="zip-tiles" role="list" aria-label={`ZIPs in ${drillStateName}`}>
        {visible.map((rollup, tileIndex) => {
          const count = rollup.addressable_borrowers ?? null;
          const avgScore = rollup.avg_opportunity_score ?? null;
          const topSegCode = rollup.top_segment_code ?? null;
          const topSegment = topSegCode ? (safeSegmentName(topSegCode) ?? undefined) : undefined;
          const overlayUnit = overlayActive ? overlayByUnit[rollup.zip] : undefined;
          const unattended = overlayUnit ? overlayUnit.unattended_count : null;
          const lvl = overlayActive
            ? (overlayUnit ? overlayBucketer(overlayUnit.unattended_count) : 1)
            : zipBucketer(count);
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
              // Staggered "settle into the grid" entrance (Buyer-Wow #4):
              // the stage cap keeps later tiles from lagging; CSS gates the
              // animation behind prefers-reduced-motion.
              style={{ '--tile-i': Math.min(tileIndex, 24) } as CSSProperties}
              role="listitem"
              aria-label={`ZIP ${rollup.zip}, ${count !== null ? `${count.toLocaleString()} borrowers` : 'no data'}`}
              onMouseEnter={(e) =>
                setHover({
                  x: e.clientX,
                  y: e.clientY,
                  name: `ZIP ${rollup.zip}, ${drillStateName}`,
                  count,
                  avgScore,
                  topSegment,
                  sourceHint: 'mip.gold.zip_rollup',
                  overlay: overlayUnit
                    ? {
                        leadCount: overlayUnit.lead_count,
                        assignedCount: overlayUnit.assigned_count,
                        unattendedCount: overlayUnit.unattended_count,
                        coveringOfficerCount: overlayUnit.covering_officer_count,
                        coveringOfficers:
                          isSelected ? overlayUnit.covering_officers : undefined,
                      }
                    : undefined,
                })
              }
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
	              onClick={() => {
	                setSelected({ level: 'zip', id: rollup.zip, name: rollup.zip });
	                navigate(leadQueuePath({ state: stateUC, zip: rollup.zip }));
	              }}
	            >
              <span className="zip-tile__code">{rollup.zip}</span>
              <span className="zip-tile__count">
                {count !== null ? count.toLocaleString() : '—'}
              </span>
              {overlayActive && (
                <span className="zip-tile__overlay">
                  {unattended !== null ? unattended.toLocaleString() : '—'} unattended
                </span>
              )}
            </button>
          );
        })}
      </div>
      {(hiddenZipCount > 0 || (unassigned ?? 0) > 0) && (
        <div className="zip-tiles__reconcile text-2" role="note">
          {hiddenZipCount > 0 && (
            <span>
              Showing the {visible.length} densest of {sorted.length.toLocaleString()} ZIPs
              {' — '}
              {visibleSum.toLocaleString()}
              {stateTotal !== null ? ` of ${stateTotal.toLocaleString()}` : ''} borrowers in view.
            </span>
          )}
          {(unassigned ?? 0) > 0 && (
            <span>
              {' '}
              {(unassigned ?? 0).toLocaleString()} borrowers in {drillStateName} carry no ZIP and
              appear in no tile.
            </span>
          )}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => navigate(leadQueuePath({ state: stateUC }))}
          >
            Open all {stateTotal !== null ? stateTotal.toLocaleString() : ''} in Lead Queue
          </button>
        </div>
      )}
      </>
    );
  };

  return (
    <div className="map-wrap" style={{ height }}>
      <div className="map-hdr">
        {/* Breadcrumbs */}
        <div className="map-crumbs">
          <div className="eyebrow">Geography drill-down</div>
          <div className="topbar__crumbs map-crumbs__trail">
            {/* US is the single back step — with the county rung gone,
                zip -> state IS zip -> national. */}
            <button
              type="button"
              className={`filter filter--compact ${level === 'state' ? 'is-active' : ''}`}
              onClick={() => {
                setLevel('state');
                setDrillStateId(null);
                setSelected(null);
              }}
            >
              <span className="filter__value">US</span>
            </button>
            {level === 'zip' && (
              <>
                <Icon name="chevright" size={11} />
                <span className="filter filter--compact is-active">
                  <span className="filter__value">{drillStateName || 'State'}</span>
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
              ? footprint.dataScope?.zip_count
                ? `${footprint.dataScope.zip_count.toLocaleString()} ZIPs · click a state to drill`
                : 'Loading coverage…'
              : `ZIPs in ${drillStateName || 'state'}`}
          </Chip>
          {/* Drill-gap disclosure: the ZIP tiles below sum to LESS than the
              state tile the user just clicked, because the share carries no
              usable ZIP for these borrowers. Say it where the gap appears. */}
          {level === 'zip' && zipUnassignedForDrill > 0 && (
            <Chip variant="neutral" icon="db">
              {zipUnassignedForDrill.toLocaleString()} borrowers without ZIP assignment
            </Chip>
          )}
          {/* S9 overlay toggle — two .filter-style buttons in the prototype's
              vocabulary. "Borrowers" is the default (current behavior);
              "Unattended leads" recolors + extends the tooltip from the
              assigned-vs-unattended overlay. */}
          <div className="map-overlay-toggle" role="group" aria-label="Map coloring">
            <button
              type="button"
              className={`filter filter--compact ${overlayOn ? '' : 'is-active'}`}
              aria-pressed={!overlayOn}
              onClick={() => setOverlayOn(false)}
            >
              <span className="filter__value">Borrowers</span>
            </button>
            <button
              type="button"
              className={`filter filter--compact ${overlayOn ? 'is-active' : ''}`}
              aria-pressed={overlayOn}
              onClick={() => setOverlayOn(true)}
            >
              <span className="filter__value">Unattended leads</span>
            </button>
          </div>
          {campaignPrefillPath && (
            <button
              type="button"
              className="btn btn--primary btn--sm map-start-campaign"
              onClick={() => navigate(campaignPrefillPath)}
            >
              <Icon name="send" size={12} />
              Start campaign
            </button>
          )}
        </div>
      </div>

      {/* Animated level transitions (Buyer-Wow #4): keying on `level`
          re-mounts this wrapper on each drill so the CSS zoom/fade enter
          replays (same pattern as the app's route-transition). `.map-levels`
          preserves the flex layout — it occupies `.map-wrap`'s flex:1 slot
          and its child stage fills it — so the wrapper never collapses the
          map. Reduced-motion disables the animation. The drill state machine
          (level + the three renderers) is untouched. */}
      <div className="map-levels" key={level} aria-busy={mapBusy}>
        <div className="map-status" role="status" aria-live="polite">
          {mapStatus}
        </div>
        {level === 'state' && renderStateLevel()}
        {level === 'zip' && renderZipLevel()}
      </div>

      {/* Legend — explicit "Colored by" label so the user understands why
          the home map and the segments map can render different hues for
          the same state. On segments-with-filter, the gradient reflects
          quantiles within the filtered segment; on home it's the full
          marketable population. Fix G, 2026-04-23. */}
      <div className="map-legend">
        <div className="map-legend__header">
          <span>
            {overlayOn ? 'Unattended leads in selection' : 'Borrowers in selection'}{' '}
            <span className="map-legend__value">
              {overlayOn
                ? overlayData
                  ? overlayData.total_unattended.toLocaleString()
                  : '—'
                : totalCount.toLocaleString()}
            </span>
          </span>
        </div>
        {overlayOn && overlayData && (
          // S9 overlay facts: the two inputs of the subtraction, plus the
          // evidence affordance tracing them to Lakebase + Unity Catalog.
          <div className="map-legend__overlay-facts">
            <span>
              {overlayData.total_leads.toLocaleString()} leads ·{' '}
              {overlayData.total_assigned.toLocaleString()} assigned
            </span>
            <EvidenceChip source={DRAWER_SOURCES.assignmentOverlay}>
              {DRAWER_SOURCES.assignmentOverlay.short}
            </EvidenceChip>
          </div>
        )}
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
            {overlayOn
              ? overlayData
                ? `unattended leads — ${overlayData.lead_definition} minus active assignments${
                    // N2 honesty: the overlay is segment-agnostic. When a
                    // segment filter is shading the borrower view, say so
                    // instead of letting the two numbers read as one scope.
                    segmentFilter && segmentFilter.length > 0
                      ? ' · overlay counts cover ALL marketing-eligible leads, not just the selected segments'
                      : ''
                  }`
                : overlayLoading
                  ? 'unattended leads (loading coverage overlay…)'
                  : 'unattended leads'
              : segmentCaption}
          </span>
        </div>
        {overlayOn && overlayError && (
          // Explicit degraded state: the overlay dependency is down; the
          // base borrower view stays fully functional. Never a silent
          // fallback.
          <div className="map-legend__caption map-legend__caption--degraded" role="status">
            {overlayError}
          </div>
        )}
        {/* Keyboard affordance: always in the DOM for screen readers,
            revealed visually by .map-wrap:focus-within when a region is
            focused. Copy matches the actual handlers — Enter/Space drill
            in (onKeyDown on each geography); there is no Esc handler, so
            backing out is via the breadcrumb trail above the map. */}
        <div className="map-legend__hint">
          <kbd>Enter</kbd> or <kbd>Space</kbd> drills in · use the breadcrumbs to go back
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
      {hover && <USChoroplethMapTooltip hover={hover} activeSegNames={activeSegNames} />}
    </div>
  );
}
