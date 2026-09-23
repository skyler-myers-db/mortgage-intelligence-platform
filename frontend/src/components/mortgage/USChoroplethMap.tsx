import { useCallback, useLayoutEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import { buildCampaignPrefillSearch, makeCampaignPrefill } from '../../lib/campaignPrefill';
import { useOptionalFootprint } from '../FootprintProvider';
import {
  USCODE_TO_FIPS,
  buildLeadQueuePath,
  type HoverState,
  type Level,
  type UsaSvgMapLocation,
} from './USChoroplethMap.utils';
import { buildChoroplethScale, classify } from './USChoroplethMap.scale';
import {
  EMPTY_MAP_SELECTION,
  sameMapSelection,
  type MapSelection,
  type MapSelectionChangeOptions,
} from './USChoroplethMap.selection';
import { USChoroplethMapHeader, type MapView } from './USChoroplethMapHeader';
import { USChoroplethMapLegend } from './USChoroplethMapLegend';
import { USChoroplethMapStates } from './USChoroplethMapStates';
import { USChoroplethMapTable, type MapTableRow } from './USChoroplethMapTable';
import { MapUnavailable } from './USChoroplethMapUnavailable';
import { USChoroplethMapTooltip } from './USChoroplethMapTooltip';
import { USChoroplethMapZipLevel } from './USChoroplethMapZipLevel';
import { useChoroplethLiveFacts, type GeoRead } from './useChoroplethLiveFacts';
import { safeSegmentName } from '../../lib/segmentMetadata';
import './USChoroplethMap.css';

export type { MapSelection } from './USChoroplethMap.selection';

// State + ZIP hover numbers come from /api/geo/state-rollups and
// /api/geo/zip-rollups (backed by mip.gold.funnel_snapshot_daily and
// mip.gold.zip_rollup). There are no local fixture literals -- any state /
// ZIP not returned in the payload renders "—" (honest null), and a rollup
// that is warming up or failed says so in the stage instead of painting 0.

/**
 * USChoroplethMap — real interactive US state map with click-to-drill.
 *
 * Matches the prototype's `ChoroplethMap`:
 *   - .map-wrap chassis with breadcrumbs, drill-hint chip, legend, map-tip.
 *   - map-region lvl-1..4 fills, is-selected accent.
 *   - level state: 'state' → 'zip' → Lead Queue deep-link.
 *
 * Upgrade vs. prototype: the prototype used hand-drawn stylized polygons. We
 * use us-atlas state TopoJSON (Albers USA pre-projected paths) for real US
 * geography so it reads as a product, not a sketch.
 *
 * The map is CONTROLLED (audit dataviz-04): the route owns the selection in
 * the URL (`useMapSelectionParams`, `?geo_state=TX&zip=`), so the route's
 * "Clear geography", Back / Forward and a shared link all agree with the
 * drill. Without a `selection` prop it keeps its own (tests, Storybook).
 *
 * This module owns the drill orchestration and the derived facts; the rest
 * lives in focused siblings — `useChoroplethLiveFacts` (topology and the
 * rollup reads), `USChoroplethMap.scale` (class breaks), `...Header`,
 * `...States`, `...ZipLevel`, `...Table`, `...Legend`, `...Tooltip`.
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
 * DESIGN-CONTRACT DEVIATION, 2026-09-23 (accessibility, audit dataviz-02 /
 * responsive-05). The prototype ramp mixes `--accent` 15/30/50/70% into
 * `--bg-3` (Module 0 Prototype.html:863-866, legend :1864-1871). Adjacent
 * classes measured 0.009-0.038 OKLab L apart and the light theme topped out
 * at 1.6:1, so the fill could not be read. The map, the ZIP tiles and the
 * legend now share one token ramp (`--map-ramp-0..4`, tokens.css) with
 * equal OKLab steps in both themes, and the legend prints real breaks.
 */

interface USChoroplethMapProps {
  height?: number;
  /** Optional segment-code filter. Non-matching states dim. */
  segmentFilter?: string[];
  /** any = overlap; all = borrower must carry every selected segment. */
  segmentFilterMode?: 'any' | 'all';
  /** Optional reviewed secondary predicates, forwarded to geo rollups and lead links. */
  portfolioCriteria?: Record<string, string | number | null | undefined>;
  /** The drill, owned by the route (see `useMapSelectionParams`). Omit to let the map keep its own. */
  selection?: MapSelection;
  /** Fires on every user drill / back-out with the next selection. */
  onSelectionChange?: (selection: MapSelection, options?: MapSelectionChangeOptions) => void;
  /** State-level drill behavior. `"filter"` drills in place and reports the
   *  selection; `"navigate"` deep-links to `/lead-queue?state=XX`. */
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
  selection,
  onSelectionChange,
  drillBehavior = 'filter',
}: USChoroplethMapProps) {
  const [ownSelection, setOwnSelection] = useState<MapSelection>(EMPTY_MAP_SELECTION);
  const current = selection ?? ownSelection;
  const changeSelection = useCallback(
    (next: MapSelection, options?: MapSelectionChangeOptions) => {
      if (sameMapSelection(next, current)) return;
      if (selection === undefined) setOwnSelection(next);
      onSelectionChange?.(next, options);
    },
    [current, onSelectionChange, selection],
  );
  const drillStateUC = current.state ?? '';
  const drillStateId = current.state ? current.state.toLowerCase() : null;
  const level: Level = drillStateId ? 'zip' : 'state';

  const [hover, setHover] = useState<HoverState | null>(null);
  const [overlayOn, setOverlayOn] = useState(false);
  const [view, setView] = useState<MapView>('map');
  // A keyboard drill moves focus on to the ZIP tiles (the state path it came
  // from is gone); a pointer drill or a Back navigation does not.
  const [keyboardDrill, setKeyboardDrill] = useState(false);
  const navigate = useNavigate();
  const footprint = useOptionalFootprint();

  // Footprint-aware drill allowlist: the active state set comes from
  // FootprintProvider, filtered through the intrinsic USPS->FIPS table so a
  // malformed configured code can never become a drillable region.
  const footprintStates = useMemo<Record<string, string>>(() => {
    const out: Record<string, string> = {};
    for (const code of footprint.stateCodes) {
      const lc = code.toLowerCase();
      const fips = USCODE_TO_FIPS[lc];
      if (fips) out[lc] = fips;
    }
    return out;
  }, [footprint.stateCodes]);
  const leadQueuePath = useMemo(() => {
    return (geo: { state?: string; county?: string; zip?: string }) => {
      return buildLeadQueuePath({ geo, segmentFilter, segmentFilterMode, portfolioCriteria });
    };
  }, [portfolioCriteria, segmentFilter, segmentFilterMode]);

  const { usaMap, states, zips, overlayData, overlayError, overlayLoading } = useChoroplethLiveFacts({
    drillState: current.state,
    segmentFilter,
    segmentFilterMode,
    portfolioCriteria,
    overlayOn,
  });
  const stateFacts = states.data;
  const zipFacts = zips.data;

  // The overlay only drives colouring once its payload is loaded; while
  // loading or degraded the base borrower colouring stays up.
  const overlayActive = overlayOn && overlayData !== null;
  // Overlay units keyed by unit_id (USPS lowercase at state level to match
  // map location ids; ZIP verbatim otherwise) for O(1) lookup.
  const overlayByUnit = useMemo(() => {
    const out: Record<string, GeoAssignmentOverlayUnit> = {};
    if (!overlayData) return out;
    for (const unit of overlayData.units) {
      const key = overlayData.level === 'state' ? unit.unit_id.toLowerCase() : unit.unit_id;
      out[key] = unit;
    }
    return out;
  }, [overlayData]);

  // One scale per painted level: the legend prints the same breaks the
  // fill uses (see USChoroplethMap.scale).
  const scale = useMemo(() => {
    if (overlayActive && overlayData) return buildChoroplethScale(overlayData.units.map((u) => u.unattended_count));
    if (level === 'zip') return zipFacts ? buildChoroplethScale(Object.values(zipFacts).map((r) => r.addressable_borrowers)) : null;
    return stateFacts ? buildChoroplethScale(Object.values(stateFacts).map((r) => r.addressable)) : null;
  }, [level, overlayActive, overlayData, stateFacts, zipFacts]);

  const activeSegNames = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return null;
    return new Set(
      segmentFilter.map((c) => safeSegmentName(c)).filter((n): n is string => Boolean(n)),
    );
  }, [segmentFilter]);

  // Clear the floating map-tip whenever the drill, the view or the facts
  // under it change: the element that would fire mouseLeave may be gone. A
  // layout effect, so it runs before a keyboard drill's autofocus (a passive
  // effect in the ZIP rung) opens the card on the first tile.
  useLayoutEffect(() => {
    setHover(null);
  }, [level, current.state, current.zip, view, stateFacts, overlayActive]);

  // Borrowers in view; null (rendered "—") until the level's rollup is known.
  const totalCount = useMemo(() => {
    if (level === 'state') {
      return stateFacts ? Object.values(stateFacts).reduce((a, b) => a + b.addressable, 0) : null;
    }
    return zipFacts ? Object.values(zipFacts).reduce((a, r) => a + (r.addressable_borrowers ?? 0), 0) : null;
  }, [level, stateFacts, zipFacts]);
  const primary: GeoRead<unknown> = level === 'zip' ? zips : states;
  const mapBusy = !usaMap || primary.loading || (level === 'zip' && states.loading);
  // Borrowers in the drilled state that the ZIP layer cannot show. The
  // backend derives it as (state total - sum of ZIP tiles) off one refresh
  // anchor, so it IS the on-screen gap rather than a second estimate of it.
  const zipUnassignedForDrill = drillStateId ? stateFacts?.[drillStateId]?.zip_unassigned_count ?? 0 : 0;
  const drillStateName = useMemo(() => {
    if (!drillStateId) return '';
    return usaMap?.locations.find((l) => l.id === drillStateId)?.name ?? drillStateUC;
  }, [drillStateId, drillStateUC, usaMap]);
  const levelWhat = level === 'zip' ? `ZIP rollups for ${drillStateName || 'state'}` : 'State borrower rollups';
  const mapStatus = !usaMap
    ? 'Loading geography.'
    : primary.warmingUp
      ? `${levelWhat}: ${primary.warmingUp.label}, retrying.`
      : primary.error
        ? `${levelWhat} could not load.`
        : states.loading && level === 'state'
          ? 'Loading state borrower rollups.'
          : zips.loading
            ? `Loading ZIP rollups for ${drillStateName || 'state'}.`
            : 'Geography rollups loaded.';
  const segmentCaption = useMemo(() => {
    if (!segmentFilter || segmentFilter.length === 0) return 'marketable population';
    const labels = segmentFilter.map((code) => safeSegmentName(code) ?? 'Unknown segment');
    return `opportunity within ${labels.join(', ')}`;
  }, [segmentFilter]);

  // S9 "Start campaign from this geography" link target. The drilled state
  // stays the campaign context while its ZIP grid is on screen; a ZIP tile
  // click deep-links to the Lead Queue instead. Carries the segment filter +
  // mode and, when the overlay is loaded, the state's lead / unattended
  // snapshot counts.
  const activeStateCode = current.state;
  const campaignPrefillPath = useMemo(() => {
    if (!activeStateCode || current.zip) return null;
    let leadCount: number | null = null;
    let unattendedCount: number | null = null;
    if (overlayOn && overlayData) {
      if (overlayData.level === 'zip') {
        // ZIP grid on screen: the state snapshot is the sum of its ZIP
        // units — the totals the overlay response already carries.
        leadCount = overlayData.total_leads;
        unattendedCount = overlayData.total_unattended;
      } else {
        const unit = overlayByUnit[activeStateCode.toLowerCase()];
        leadCount = unit ? unit.lead_count : null;
        unattendedCount = unit ? unit.unattended_count : null;
      }
    }
    // Hoisted out of the `try`: React Compiler 1.0 cannot lower a value block
    // (`??`, `?:`, `?.`) inside try/catch and silently bails out of the WHOLE
    // component when it meets one. `node tools/react_compiler_coverage.mjs`
    // reports it; keep the try body free of such expressions.
    const segmentCodes = segmentFilter ?? [];
    try {
      const prefill = makeCampaignPrefill({
        level: 'state',
        state: activeStateCode,
        countyFips: null,
        countyName: null,
        segmentCodes,
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
  }, [activeStateCode, current.zip, overlayOn, overlayData, overlayByUnit, segmentFilter, segmentFilterMode]);

  const activateState = useCallback(
    (location: UsaSvgMapLocation, hasFacts: boolean, viaKeyboard: boolean) => {
      if (drillBehavior === 'navigate') {
        // Home-page teaser → deep-link to the filtered queue, only for a
        // state with data; clicking an unsupported state is a no-op.
        if (hasFacts) navigate(leadQueuePath({ state: location.id.toUpperCase() }));
        return;
      }
      if (!footprintStates[location.id] && !hasFacts) return;
      // Straight to ZIPs — there is no honest county rung.
      setKeyboardDrill(viaKeyboard);
      changeSelection({ state: location.id.toUpperCase(), county: null, zip: null });
    },
    [changeSelection, drillBehavior, footprintStates, leadQueuePath, navigate],
  );

  // Table rows for the level on screen: the numbers the map paints.
  const tableRows = useMemo<MapTableRow[]>(() => {
    const value = (count: number, unitKey: string) =>
      overlayActive ? overlayByUnit[unitKey]?.unattended_count : count;
    if (level === 'state') {
      if (!stateFacts || !usaMap) return [];
      return usaMap.locations
        .filter((location) => stateFacts[location.id])
        .map((location) => {
          const rollup = stateFacts[location.id];
          return {
            id: location.id,
            name: location.name,
            count: rollup.addressable,
            avgScore: rollup.avg_score,
            topSegment: rollup.top_segment_code ? safeSegmentName(rollup.top_segment_code) ?? undefined : undefined,
            contactable: rollup.contactable,
            unattended: overlayActive ? overlayByUnit[location.id]?.unattended_count ?? null : undefined,
            cls: classify(scale, value(rollup.addressable, location.id)),
            onOpen: drillBehavior === 'filter' ? () => activateState(location, true, false) : undefined,
          };
        });
    }
    return Object.values(zipFacts ?? {}).map((rollup) => ({
      id: rollup.zip,
      name: rollup.zip,
      count: rollup.addressable_borrowers ?? 0,
      avgScore: rollup.avg_opportunity_score ?? null,
      topSegment: rollup.top_segment_code ? safeSegmentName(rollup.top_segment_code) ?? undefined : undefined,
      unattended: overlayActive ? overlayByUnit[rollup.zip]?.unattended_count ?? null : undefined,
      cls: classify(scale, value(rollup.addressable_borrowers ?? 0, rollup.zip)),
    }));
  }, [activateState, drillBehavior, level, overlayActive, overlayByUnit, scale, stateFacts, usaMap, zipFacts]);

  const renderStage = () => {
    if (!usaMap) {
      return <div className="map-stage map-stage--empty">Loading geography…</div>;
    }
    if (primary.warmingUp || primary.error) {
      return (
        <MapUnavailable
          read={primary}
          what={levelWhat}
          fallback={level === 'zip' ? (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate(leadQueuePath({ state: drillStateUC }))}>
              Open Lead Queue for {drillStateName}
            </button>
          ) : undefined}
        />
      );
    }
    if (view === 'table') {
      if (level === 'state' ? !stateFacts : !zipFacts) {
        return <div className="map-stage map-stage--empty">Loading rollups…</div>;
      }
      return (
        <USChoroplethMapTable
          unitLabel={level === 'state' ? 'State' : 'ZIP'}
          caption={level === 'state'
            ? `Marketable borrowers by state, ${segmentCaption}`
            : `Marketable borrowers by ZIP in ${drillStateName}, ${segmentCaption}`}
          rows={tableRows}
          overlayActive={overlayActive}
        />
      );
    }
    if (level === 'state') {
      return (
        <USChoroplethMapStates
          usaMap={usaMap}
          stateFacts={stateFacts}
          scale={scale}
          overlayByUnit={overlayActive ? overlayByUnit : null}
          footprintStates={footprintStates}
          activeSegNames={activeSegNames}
          selectedId={drillBehavior === 'navigate' ? null : drillStateId}
          setHover={setHover}
          onActivate={activateState}
        />
      );
    }
    if (!zipFacts) {
      return <div className="map-stage map-stage--empty">Loading ZIPs…</div>;
    }
    return (
      <USChoroplethMapZipLevel
        drillStateName={drillStateName}
        byZip={zipFacts}
        stateFacts={drillStateId ? stateFacts?.[drillStateId] : undefined}
        scale={scale}
        overlayActive={overlayActive}
        overlayByUnit={overlayByUnit}
        selectedZip={current.zip}
        autoFocus={keyboardDrill}
        onAutoFocused={() => setKeyboardDrill(false)}
        setHover={setHover}
        onSelectZip={(zip) => {
          // Record the ZIP on this entry, then open its queue: Back returns
          // to this drill with the tile selected.
          changeSelection({ state: drillStateUC, county: null, zip }, { replace: true });
          navigate(leadQueuePath({ state: drillStateUC, zip }));
        }}
        onOpenStateQueue={() => navigate(leadQueuePath({ state: drillStateUC }))}
      />
    );
  };

  return (
    <div className="map-wrap" style={{ height }} data-map-view={view}>
      <USChoroplethMapHeader
        drilled={level === 'zip'}
        drillStateUC={drillStateUC}
        drillStateName={drillStateName}
        onBackToUs={() => changeSelection(EMPTY_MAP_SELECTION)}
        coverageZipCount={footprint.dataScope?.zip_count ?? null}
        zipUnassigned={zipUnassignedForDrill}
        overlayOn={overlayOn}
        setOverlayOn={setOverlayOn}
        view={view}
        setView={setView}
        campaignPrefillPath={campaignPrefillPath}
        onStartCampaign={(path) => navigate(path)}
      />

      {/* Animated level transitions (Buyer-Wow #4): keying on `level`
          re-mounts this wrapper on each drill so the CSS zoom/fade enter
          replays. `.map-levels` occupies `.map-wrap`'s flex:1 slot and its
          child stage fills it. Not busy while a rollup is warming up or
          failed: aria-busy would silence the WarmingUpBlock's live region. */}
      <div className="map-levels" key={level} aria-busy={mapBusy}>
        <div className="map-status" role="status" aria-live="polite">
          {mapStatus}
        </div>
        {renderStage()}
      </div>

      {/* Legend — explicit "Colored by" label so the user understands why
          the home map and the segments map can render different hues for
          the same state. */}
      <USChoroplethMapLegend
        overlayOn={overlayOn}
        overlayData={overlayData}
        overlayLoading={overlayLoading}
        overlayError={overlayError}
        totalCount={totalCount}
        scale={scale}
        segmentCaption={segmentCaption}
        segmentFilter={segmentFilter}
      />

      {/* Hover / focus card, portaled to document.body so `.map-wrap
          { overflow: hidden }` can never clip it. The per-state count is
          segment-aware (the state rollup re-reads with `segment_codes`). */}
      {hover && <USChoroplethMapTooltip hover={hover} activeSegNames={activeSegNames} />}
    </div>
  );
}
