import { useCallback, useDeferredValue, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { useOptionalFootprint } from '../FootprintProvider';
import {
  ZIP_TILE_CAP,
  buildLeadQueuePath,
  densestZips,
  footprintStateMap,
  overlayUnitsById,
  type Level,
  type UsaSvgMapLocation,
} from './USChoroplethMap.utils';
import { buildChoroplethScale } from './USChoroplethMap.scale';
import {
  EMPTY_MAP_SELECTION,
  sameMapSelection,
  type MapSelection,
  type MapSelectionChangeOptions,
} from './USChoroplethMap.selection';
import { USChoroplethMapHeader, type MapColorMode, type MapView } from './USChoroplethMapHeader';
import { USChoroplethMapLegend } from './USChoroplethMapLegend';
import { USChoroplethMapStates } from './USChoroplethMapStates';
import { MAP_TABLE } from './USChoroplethMapTable.lazy';
import { MapUnavailable } from './USChoroplethMapUnavailable';
import { buildMapTableRows, offMapCaption, type MapTableGroups } from './USChoroplethMap.table';
import { campaignPrefillPath as buildCampaignPath } from './USChoroplethMap.campaign';
import { USChoroplethMapTooltip } from './USChoroplethMapTooltip';
import { buildMapCard } from './USChoroplethMap.hover';
import { useMapHover } from './useMapHover';
import { snapStep, useMapColoring } from './useMapModeParams';
import { USChoroplethMapZipLevel, ZCTA_TILES_STATUS, zipPopulated } from './USChoroplethMapZipLevel';
import type { ZipRung } from './USChoroplethMapZctaLevel';
import { ZCTA_LEVEL } from './zctaLevel.lazy';
import { useChoroplethLiveFacts, type GeoRead } from './useChoroplethLiveFacts';
import { indexRateScenario, scenarioView } from './rateScenario.logic';
import { RATE_SCENARIO_CONTROL } from './rateScenario.lazy';
import { useLazyModule } from './useLazyModule';
import { preloadBestEffort } from '../../lib/lazyPreload';
import { safeSegmentName } from '../../lib/segmentMetadata';
import { formatCount, ratePct } from '../../lib/formatters';
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
 * drill. Without a `selection` prop it keeps its own (a unit test or fixture spec).
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
 *
 * Rate Lever (audit wow-stage-1): a third colouring, "Rate scenario", over
 * the whole book (disabled under a segment or portfolio filter). The map owns
 * the step; the fill, the table and the legend total follow a deferred copy
 * of it (`useDeferredValue`) while the thumb follows the input, and
 * `.map-wrap[data-scenario-pending]` marks the gap. The table is the
 * accessible equivalent of the scenario fill (the hover card and the state
 * names keep the borrower facts: route-budget cut line). The fill uses one
 * scale over every state at every step, so a colour change is a count change.
 * ZIP tiles keep borrower colouring (no sub-state scenario). The Lead Queue
 * links and "Start campaign" keep today's cohort.
 *
 * Runtime-06 (map slice): a changed cohort keeps the previous fill up,
 * labelled "Updating…" (`.stable-refresh-region.is-updating` on the stage and
 * the legend, one announcement in `.map-status`), instead of blanking.
 *
 * ZIP areas (W5c, audit dataviz-01 / visual-09 / motion-10;
 * deviation:zcta-level): with `zipAreas` (Segment Intelligence), a map-view
 * drill draws the state's committed Census ZCTAs in a lazy rung
 * (USChoroplethMapZctaLevel); the legend's scale covers every populated ZIP
 * and its caption says what a ZCTA is. The densest-ZIP tiles are the degraded
 * fallback (a status line says so). Home never sets it, so it never loads the
 * rung or fetches geometry. The root carries data-rum-target="map"
 * (D-platform-process-d2, runtime-09).
 */

interface USChoroplethMapProps {
  height?: number;
  /** Optional segment-code filter. The rollups re-read with it, so the fill shows the filtered counts. */
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
  /** The colouring and Rate Lever step, owned by the route (see useMapModeParams). Omit to let the map keep its own. */
  mode?: MapColorMode;
  step?: number;
  onModeChange?: (mode: MapColorMode) => void;
  /** A committed step (pointerup, a key, Reset, or the snap to the grid). */
  onStepCommit?: (step: number) => void;
  /**
   * The map read on screen (state rollups at the national view, ZIP rollups
   * when drilled) was retained by the server after a failed refresh: its last
   * good read, or null (delivery-06). A host that passes this owns the stale
   * note; without it the legend shows the note itself.
   */
  onReadStale?: (lastGoodAt: string | null) => void;
  /**
   * Draw the ZIP level as committed Census ZIP areas (the lazy ZCTA rung)
   * when drilled in the map view; tiles otherwise. Opt-in per host: only
   * Segment Intelligence sets it (Home must never fetch geometry).
   */
  zipAreas?: boolean;
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
  mode: modeProp,
  step: stepProp,
  onModeChange,
  onStepCommit,
  onReadStale,
  zipAreas = false,
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

  // The hover / focus card: only the hovered unit's id is state; the tip
  // follows the pointer outside React (useMapHover, D-dataviz-geo-d1).
  const { hovered, stage: hoverStage, hide: hideCard, tipRef, placeTip } = useMapHover();
  const { mode, setMode, rateStep, setRateStep } = useMapColoring({ mode: modeProp, step: stepProp, onModeChange });
  // The rate grid is the whole book: under any cohort filter it would recolour
  // a different population than the one on screen, so the mode falls back.
  const rateAvailable = !segmentFilter?.length && !Object.keys(portfolioCriteria ?? {}).length;
  const effectiveMode: MapColorMode = mode === 'rate' && !rateAvailable ? 'borrowers' : mode;
  const overlayOn = effectiveMode === 'unattended';
  const rateOn = effectiveMode === 'rate';
  // The control's lazy chunk. A failed load (a chunk a redeploy retired, a
  // network blip) turns the scenario off here, over the borrower fill, with
  // the legend saying so; it never throws into the route error boundary.
  const lever = useLazyModule(RATE_SCENARIO_CONTROL, rateOn);
  const scenarioOn = rateOn && !lever.failed;
  const shownStep = useDeferredValue(rateStep);
  const [view, setView] = useState<MapView>('map');
  // The table view's own chunk, loaded the first time the table is picked.
  const tableView = useLazyModule(MAP_TABLE, view === 'table');
  // The ZCTA rung (dataviz-01): its chunk loads on a map-view drill; the
  // rung reports polygons or tiles per state, and registers its zoom buttons.
  const zipRungHost = zipAreas && drillBehavior === 'filter';
  const zctaWanted = zipRungHost && level === 'zip' && view === 'map';
  const zcta = useLazyModule(ZCTA_LEVEL, zctaWanted);
  const [rungFor, setRungFor] = useState<{ state: string; rung: ZipRung } | null>(null);
  const zipRung: ZipRung | 'loading' | null = !zctaWanted
    ? null
    : zcta.failed ? 'tiles' : rungFor?.state === drillStateUC ? rungFor.rung : 'loading';
  const polygons = zctaWanted && zipRung !== 'tiles';
  const onRung = useCallback((rung: ZipRung) => setRungFor({ state: drillStateUC, rung }), [drillStateUC]);
  const [zoomControls, setZoomControls] = useState<ReactNode>(null);
  // A keyboard drill, and any drill from a table row, removes the control
  // that had focus (the state path, the row's button). Focus then moves on to
  // the drilled level (a ZIP tile, the empty state's action or the ZIP table)
  // rather than falling to <body>; a warming / failed stage holds it
  // meanwhile. The request names the drilled state and stays open until a
  // final target mounts; a pointer drill or a Back navigation makes none.
  const [drillFocusFor, setDrillFocusFor] = useState<string | null>(null);
  const drillFocus = drillFocusFor !== null && drillFocusFor === current.state;
  const onDrillFocused = useCallback(() => setDrillFocusFor(null), []);
  // Escape at the ZIP level backs out to the national view (dataviz-10) and
  // asks the state stage (or the state table) to give focus back to the state
  // it left, once it mounts; the header's crumb waits for it.
  const [returnFocusTo, setReturnFocusTo] = useState<string | null>(null);
  const onReturnFocused = useCallback(() => setReturnFocusTo(null), []);
  const navigate = useNavigate();
  const footprint = useOptionalFootprint();

  // The configured footprint, keyed like the map's location ids.
  const footprintStates = useMemo(() => footprintStateMap(footprint.stateCodes), [footprint.stateCodes]);
  const leadQueuePath = useMemo(() => {
    return (geo: { state?: string; county?: string; zip?: string }) => {
      return buildLeadQueuePath({ geo, segmentFilter, segmentFilterMode, portfolioCriteria });
    };
  }, [portfolioCriteria, segmentFilter, segmentFilterMode]);

  const { usaMap, states, zips, overlayData, overlayError, overlayLoading, rate } = useChoroplethLiveFacts({
    drillState: current.state,
    segmentFilter,
    segmentFilterMode,
    portfolioCriteria,
    overlayOn,
    rateOn: scenarioOn,
  });
  const stateFacts = states.data;
  const zipFacts = zips.data;

  // The overlay only drives colouring once its payload is loaded; while
  // loading or degraded the base borrower colouring stays up.
  const overlayActive = overlayOn && overlayData !== null;
  // Rate Lever: the grid drives the state fill once it is indexed; while it
  // is warming, failed or not built the fill stays on borrowers.
  // (The React Compiler memoizes both on their inputs.)
  const rateIndex = scenarioOn ? indexRateScenario(rate.data) : null;
  const scenarioAtShown = rateIndex ? scenarioView(rateIndex, shownStep) : null;
  // A linked step off the grid snaps once the grid is up, and the URL follows (one replace).
  useEffect(() => {
    const snapped = rateIndex ? snapStep(rateIndex.steps, rateStep) : rateStep;
    if (snapped === rateStep) return;
    setRateStep(snapped);
    onStepCommit?.(snapped);
  }, [onStepCommit, rateIndex, rateStep, setRateStep]);
  // ZIP tiles keep borrower colouring: there is no sub-state scenario.
  const shownScenario = level === 'state' ? scenarioAtShown : null;
  // Overlay units keyed like the map's units, for O(1) lookup.
  const overlayByUnit = useMemo(() => overlayUnitsById(overlayData), [overlayData]);

  // One scale per painted level: the legend prints the same breaks the
  // fill uses (see USChoroplethMap.scale). The ZIP scale is built over the
  // tiles the grid shows, and the legend says so when ZIPs are left out.
  const scale = useMemo(() => {
    if (shownScenario) return rateIndex?.scale ?? null;
    if (overlayActive && overlayData) return buildChoroplethScale(overlayData.units.map((u) => u.unattended_count));
    if (level === 'zip') {
      // Polygons paint every populated ZIP; the tiles, the densest they show.
      const shown = zipFacts && (polygons ? Object.values(zipFacts).filter(zipPopulated) : densestZips(zipFacts));
      return shown ? buildChoroplethScale(shown.map((r) => r.addressable_borrowers)) : null;
    }
    return stateFacts ? buildChoroplethScale(Object.values(stateFacts).map((r) => r.addressable)) : null;
  }, [level, overlayActive, overlayData, polygons, rateIndex, shownScenario, stateFacts, zipFacts]);
  const zipCount = zipFacts ? Object.keys(zipFacts).length : 0;
  const scaleScope = level === 'zip' && !overlayActive && !polygons && zipCount > ZIP_TILE_CAP
    ? `over the ${ZIP_TILE_CAP} densest of ${formatCount(zipCount)} ZIPs`
    : null;

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
    hideCard();
  }, [hideCard, level, current.state, current.zip, view, stateFacts, overlayActive]);

  // Borrowers in view; null (rendered "—") until the level's rollup is known.
  const totalCount = useMemo(() => {
    if (level === 'state') {
      return stateFacts ? Object.values(stateFacts).reduce((a, b) => a + b.addressable, 0) : null;
    }
    return zipFacts ? Object.values(zipFacts).reduce((a, r) => a + (r.addressable_borrowers ?? 0), 0) : null;
  }, [level, stateFacts, zipFacts]);
  const primary: GeoRead<unknown> = level === 'zip' ? zips : states;
  // delivery-06: the age of a retained read on screen, reported to the host or shown in the legend.
  const staleAt = primary.data !== null ? primary.lastGoodAt : null;
  useEffect(() => {
    onReadStale?.(staleAt);
  }, [onReadStale, staleAt]);
  const mapBusy = !usaMap || primary.loading || (level === 'zip' && states.loading) || zipRung === 'loading';
  // Borrowers in the drilled state that the ZIP layer cannot show. The
  // backend derives it as (state total - sum of ZIP tiles) off one refresh
  // anchor, so it IS the on-screen gap rather than a second estimate of it.
  const zipUnassignedForDrill = drillStateId ? stateFacts?.[drillStateId]?.zip_unassigned_count ?? 0 : 0;
  const drillStateName = useMemo(() => {
    if (!drillStateId) return '';
    return usaMap?.locations.find((l) => l.id === drillStateId)?.name ?? drillStateUC;
  }, [drillStateId, drillStateUC, usaMap]);
  // The Rate Lever's state names (largest gains); off the map, the USPS code.
  const stateName = useCallback(
    (id: string) => usaMap?.locations.find((l) => l.id === id)?.name ?? id.toUpperCase(),
    [usaMap],
  );
  const levelWhat = level === 'zip' ? `ZIP rollups for ${drillStateName || 'state'}` : 'State borrower rollups';
  // runtime-06: the previous cohort stays painted while the new one reads,
  // labelled; a warming loop over it keeps the fill and says so in the pill.
  const updating = primary.updating;
  const mapStatus = !usaMap
    ? 'Loading geography.'
    : updating
      ? `Updating ${level === 'zip' ? levelWhat : 'state borrower rollups'}.`
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

  // S9 "Start campaign from this geography" (USChoroplethMap.campaign).
  const activeStateCode = current.state;
  const campaignPrefillPath = useMemo(
    () => buildCampaignPath({
      activeStateCode, current: { zip: current.zip }, overlayOn, overlayData, overlayByUnit, segmentFilter, segmentFilterMode,
    }),
    [activeStateCode, current.zip, overlayOn, overlayData, overlayByUnit, segmentFilter, segmentFilterMode],
  );

  const activateState = useCallback(
    (location: UsaSvgMapLocation, populated: boolean, moveFocus: boolean) => {
      // Only a state with borrowers in this selection activates (dataviz-10).
      if (!populated) return;
      if (drillBehavior === 'navigate') {
        // Home-page teaser → deep-link to the filtered queue.
        navigate(leadQueuePath({ state: location.id.toUpperCase() }));
        return;
      }
      // Straight to ZIPs — there is no honest county rung.
      setDrillFocusFor(moveFocus ? location.id.toUpperCase() : null);
      changeSelection({ state: location.id.toUpperCase(), county: null, zip: null });
    },
    [changeSelection, drillBehavior, leadQueuePath, navigate],
  );

  // Table rows for the level on screen: the numbers the map paints.
  const tableGroups = useMemo<MapTableGroups>(
    () => buildMapTableRows({
      shownScenario, overlayActive, overlayByUnit, level, stateFacts, usaMap, scale, drillBehavior, activateState, zipFacts,
      footprintStates,
    }),
    [activateState, drillBehavior, footprintStates, level, overlayActive, overlayByUnit, scale, shownScenario, stateFacts, usaMap, zipFacts],
  );

  // The card for the hovered unit, built once per unit (USChoroplethMap.hover).
  const card = hovered
    ? buildMapCard(hovered.level, hovered.id, {
        usaMap,
        stateFacts,
        overlayByUnit: overlayActive ? overlayByUnit : null,
        footprintStates,
        selectedId: drillBehavior === 'navigate' ? null : drillStateId,
        drillStateName,
        zipFacts,
        selectedZip: current.zip,
        scenario: shownScenario,
        zipAreas: zipRung === 'polygons',
      })
    : null;

  const renderStage = () => {
    if (!usaMap) {
      return <div className="map-stage map-stage--empty">Loading geography…</div>;
    }
    if ((primary.warmingUp && !updating) || primary.error) {
      return (
        <MapUnavailable
          read={primary}
          what={levelWhat}
          fallback={level === 'zip' ? (
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate(leadQueuePath({ state: drillStateUC }))}>
              Open Lead Queue for {drillStateName}
            </button>
          ) : undefined}
          // A keyboard drill waiting on this read, or an Escape out of a ZIP
          // level whose national stage is down: park focus here, not on <body>.
          autoFocus={drillFocus || (level === 'state' && returnFocusTo !== null)}
        />
      );
    }
    if (view === 'table') {
      if (level === 'state' ? !stateFacts : !zipFacts) {
        return <div className="map-stage map-stage--empty">Loading rollups…</div>;
      }
      const MapTable = tableView.module?.USChoroplethMapTable ?? null;
      if (!MapTable) {
        return tableView.failed ? (
          <div className="map-stage map-stage--empty" role="status">
            The table view could not load.{' '}
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        ) : (
          <div className="map-stage map-stage--empty">Loading the table…</div>
        );
      }
      // Rate mode: change versus today as labelled numbers (wow-stage-1, D-dataviz-geo-b).
      const scenarioRate = shownScenario ? ratePct(shownScenario.ratePct) : null;
      return (
        <MapTable
          unitLabel={level === 'state' ? 'State' : 'ZIP'}
          caption={level === 'zip'
            ? `Marketable borrowers by ZIP in ${drillStateName}, ${segmentCaption}`
            : scenarioRate
              ? `Marketable (addressable) borrowers by state, with in-the-money counts at ${scenarioRate} par, ${segmentCaption}`
              : `Marketable borrowers by state, ${segmentCaption}`}
          groups={tableGroups}
          extraColumn={scenarioRate
            ? `In the money at ${scenarioRate}`
            : overlayActive ? 'Unattended leads' : null}
          changeColumn={shownScenario?.changeById && shownScenario.step !== 0 ? 'In the money: change vs today' : null}
          scenarioContactableColumn={scenarioRate ? `Contactable in the money at ${scenarioRate}` : null}
          autoFocus={drillFocus}
          onAutoFocused={onDrillFocused}
          focusRowId={level === 'state' ? returnFocusTo : null}
          onFocusRowDone={onReturnFocused}
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
          scenario={shownScenario}
          footprintStates={footprintStates}
          selectedId={drillBehavior === 'navigate' ? null : drillStateId}
          hover={hoverStage}
          focusRequest={returnFocusTo}
          onFocusRequestDone={onReturnFocused}
          onActivate={activateState}
        />
      );
    }
    if (!zipFacts) {
      return <div className="map-stage map-stage--empty">Loading ZIPs…</div>;
    }
    const zipProps = {
      drillStateName,
      byZip: zipFacts,
      stateFacts: drillStateId ? stateFacts?.[drillStateId] : undefined,
      scale,
      overlayActive,
      overlayByUnit,
      selectedZip: current.zip,
      autoFocus: drillFocus,
      onAutoFocused: onDrillFocused,
      hover: hoverStage,
      onSelectZip: (zip: string) => {
        // Record the ZIP on this entry, then open its queue: Back returns
        // to this drill with the tile (or ZIP area) selected.
        changeSelection({ state: drillStateUC, county: null, zip }, { replace: true });
        navigate(leadQueuePath({ state: drillStateUC, zip }));
      },
      onOpenStateQueue: () => navigate(leadQueuePath({ state: drillStateUC })),
    };
    const ZctaLevel = zctaWanted ? zcta.module?.USChoroplethMapZctaLevel ?? null : null;
    if (zctaWanted && !zcta.failed) {
      return ZctaLevel ? (
        <ZctaLevel
          key={drillStateUC}
          {...zipProps}
          usps={drillStateUC}
          nationalViewBox={usaMap.viewBox}
          onRung={onRung}
          onZoomControls={setZoomControls}
        />
      ) : (
        <div className="map-stage map-stage--empty">Loading ZIP areas…</div>
      );
    }
    // The rung's chunk failed (a retired chunk, a network blip): the tiles say so.
    return <USChoroplethMapZipLevel {...zipProps} status={zctaWanted ? ZCTA_TILES_STATUS : null} />;
  };

  return (
    <div
      className="map-wrap"
      data-rum-target="map"
      // Rate mode grows the map by the lever instead of squeezing the stage
      // (USChoroplethMap.css keeps the stage's floor); `height` stays the floor.
      style={rateOn ? { minHeight: height } : { height }}
      data-map-view={view}
      data-scenario-pending={rateOn && rateStep !== shownStep ? '' : undefined}
    >
      <USChoroplethMapHeader
        drilled={level === 'zip'}
        drillStateUC={drillStateUC}
        drillStateName={drillStateName}
        onBackToUs={() => changeSelection(EMPTY_MAP_SELECTION)}
        drillExitFocusPending={returnFocusTo !== null}
        coverageZipCount={footprint.dataScope?.zip_count ?? null}
        drillHint={primary.warmingUp === null && primary.error === null}
        zipUnassigned={zipUnassignedForDrill}
        mode={effectiveMode}
        setMode={setMode}
        rateAvailable={rateAvailable}
        view={view}
        setView={setView}
        campaignPrefillPath={campaignPrefillPath}
        onStartCampaign={(path) => navigate(path)}
        zoomControls={zipRung === 'polygons' ? zoomControls : null}
      />

      {/* Animated level transitions (Buyer-Wow #4): keying on `level`
          re-mounts this wrapper on each drill so the CSS zoom/fade enter
          replays. `.map-levels` occupies `.map-wrap`'s flex:1 slot and its
          child stage fills it. Not busy while a rollup is warming up or
          failed: aria-busy would silence the WarmingUpBlock's live region. */}
      <div
        className={`map-levels stable-refresh-region ${updating ? 'is-updating' : ''}${zctaWanted ? ' map-levels--tween' : ''}`}
        key={level}
        aria-busy={mapBusy}
        // Warm the ZCTA rung's JS (never geometry) when the national stage is pointed at.
        onPointerEnter={zipRungHost && level === 'state' ? () => preloadBestEffort(ZCTA_LEVEL.load) : undefined}
        // Escape hides an open card and stops there, so the same keypress
        // never also closes a menu that listens on window. With no card, at
        // the ZIP level (map or table) it backs out one level, a history push
        // like the US crumb; at the national level it passes through
        // (dataviz-10, deviation:map-escape-and-populated-roving).
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return;
          if (hovered !== null) {
            hideCard();
          } else if (level === 'zip') {
            setReturnFocusTo(drillStateId);
            changeSelection(EMPTY_MAP_SELECTION);
          } else {
            return;
          }
          event.stopPropagation();
        }}
      >
        <div className="map-status" role="status" aria-live="polite">
          {mapStatus}
        </div>
        {updating && (
          <span className="stable-refresh-status" aria-hidden="true">
            Updating…{primary.warmingUp ? ` ${primary.warmingUp.label}` : ''}
          </span>
        )}
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
        scaleScope={scaleScope}
        caption={level === 'zip' && polygons ? 'ZIP areas are Census 2020 ZCTAs, an approximation of USPS delivery areas' : null}
        offMapNote={offMapCaption(tableGroups.offMap, shownScenario || overlayActive ? 'extra' : 'count')}
        segmentCaption={segmentCaption}
        segmentFilter={segmentFilter}
        updating={updating}
        staleLastGoodAt={onReadStale ? null : staleAt}
        rate={rateOn ? {
          read: rate,
          index: rateIndex,
          view: scenarioAtShown,
          step: rateStep,
          onStepChange: setRateStep,
          onStepCommit,
          scope: drillStateId ? { id: drillStateId, name: drillStateName } : null,
          stateName,
          control: lever.module?.default ?? null,
          controlFailed: lever.failed,
        } : null}
      />

      {/* Hover / focus card, a top-layer popover portaled to document.body
          so `.map-wrap { overflow: hidden }` can never clip it and no overlay
          covers it. The per-state count is segment-aware (the state rollup
          re-reads with `segment_codes`). */}
      {card && hovered && (
        <USChoroplethMapTooltip
          card={card}
          unitKey={`${hovered.level}:${hovered.id}`}
          tipRef={tipRef}
          placeTip={placeTip}
          activeSegNames={activeSegNames}
        />
      )}
    </div>
  );
}
