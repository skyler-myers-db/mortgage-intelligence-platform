/**
 * USChoroplethMapStates — the national rung of the geography drill: one
 * us-atlas path per state, filled by the shared class scale, with a USPS
 * label on every populated state. Extracted from USChoroplethMap.tsx so the
 * hero map file shrinks as it gains keyboard and screen-reader support
 * (audit a11y-04):
 *
 *  - one Tab stop for all 51 states (roving tabindex), arrow keys / Home /
 *    End move between them in alphabetical order, Enter / Space drill;
 *  - each state's accessible name carries its count, average score and top
 *    segment (aggregates only, never borrower data);
 *  - the hover card also opens on keyboard focus, anchored to the state's
 *    box, and Escape dismisses it.
 *
 * Every populated state paints its class's ramp step at full opacity, on
 * every route (audit dataviz-02). Under a segment filter the rollups are
 * re-read with `segment_codes`, so the class already encodes the filtered
 * count; the old "dim a state whose top segment is outside the filter"
 * cue (opacity 0.3) painted colours the legend never showed and hid states
 * that hold many borrowers of the selected segment.
 */
import { useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import { safeSegmentName } from '../../lib/segmentMetadata';
import type { StateRollup } from '../../types';
import type { MapScenarioView } from './rateScenario.logic';
import { moveRovingFocus, showCardOnFocus, stateAriaLabel } from './USChoroplethMap.a11y';
import { classify, type ChoroplethScale, type MapClass } from './USChoroplethMap.scale';
import type { HoverState, UsaSvgMap, UsaSvgMapLocation } from './USChoroplethMap.utils';

interface USChoroplethMapStatesProps {
  usaMap: UsaSvgMap;
  /** Per-state rollups keyed by lowercase USPS code; null while loading. */
  stateFacts: Record<string, StateRollup> | null;
  /** The scale the fill follows (borrowers, or unattended leads under the overlay). */
  scale: ChoroplethScale | null;
  /** Overlay units keyed by lowercase USPS code when the overlay colours the map; else null. */
  overlayByUnit: Record<string, GeoAssignmentOverlayUnit> | null;
  /** The Rate Lever view at the shown step, when the rate colouring fills the map; else null. */
  scenario?: MapScenarioView | null;
  footprintStates: Record<string, string>;
  /** Lowercase id of the selected state, if any. */
  selectedId: string | null;
  setHover: Dispatch<SetStateAction<HoverState | null>>;
  /** Click / Enter / Space on a state. `viaKeyboard` lets the drill move focus on. */
  onActivate: (location: UsaSvgMapLocation, hasFacts: boolean, viaKeyboard: boolean) => void;
}

interface StateView {
  location: UsaSvgMapLocation;
  rollup: StateRollup | undefined;
  topSegment: string | undefined;
  overlayUnit: GeoAssignmentOverlayUnit | undefined;
  cls: MapClass | null;
  inFootprint: boolean;
}

const SOURCE_IN_SCOPE = 'mip.gold.funnel_snapshot_daily + mip.gold.state_top_segment';
const SOURCE_OUT_OF_SCOPE = 'Outside Cotality evaluation scope';

export function USChoroplethMapStates({
  usaMap,
  stateFacts,
  scale,
  overlayByUnit,
  scenario = null,
  footprintStates,
  selectedId,
  setHover,
  onActivate,
}: USChoroplethMapStatesProps) {
  const loading = stateFacts === null;
  // Alphabetical by display name: the order the arrow keys walk.
  const views = useMemo<StateView[]>(() => {
    return [...usaMap.locations]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((location) => {
        const rollup = stateFacts?.[location.id];
        const overlayUnit = overlayByUnit?.[location.id];
        const value = scenario
          ? scenario.inTheMoneyById[location.id]
          : overlayByUnit ? overlayUnit?.unattended_count : rollup?.addressable;
        return {
          location,
          rollup,
          topSegment: rollup?.top_segment_code ? safeSegmentName(rollup.top_segment_code) || undefined : undefined,
          overlayUnit,
          cls: classify(scale, value),
          inFootprint: Boolean(footprintStates[location.id]),
        };
      });
  }, [footprintStates, overlayByUnit, scale, scenario, stateFacts, usaMap.locations]);

  const [activeId, setActiveId] = useState<string | null>(null);
  // The single tab stop: the last focused state, else the selected one, else
  // the first populated state, else the first state.
  const tabStopId =
    (activeId && views.some((view) => view.location.id === activeId) ? activeId : null)
    ?? (selectedId && views.some((view) => view.location.id === selectedId) ? selectedId : null)
    ?? views.find((view) => view.rollup)?.location.id
    ?? views[0]?.location.id
    ?? null;

  const hoverFor = (view: StateView, x: number, y: number): HoverState => {
    const { location, rollup, overlayUnit } = view;
    return {
      x,
      y,
      name: location.name,
      count: rollup ? rollup.addressable : null,
      avgScore: rollup ? rollup.avg_score : null,
      topSegment: view.topSegment,
      // In-footprint states surface the live rollup; out-of-footprint states
      // an honest "outside the evaluation scope" card, so no hover is blank.
      sourceHint: view.inFootprint ? SOURCE_IN_SCOPE : SOURCE_OUT_OF_SCOPE,
      // Both gaps are disclosed on the tile before the click: `contactable`
      // is the subset the Lead Queue behind this tile shows, `zipUnassigned`
      // the subset the ZIP drill cannot show.
      contactable: rollup?.contactable ?? null,
      zipUnassigned: rollup?.zip_unassigned_count ?? null,
      overlay: overlayUnit
        ? {
            leadCount: overlayUnit.lead_count,
            assignedCount: overlayUnit.assigned_count,
            unattendedCount: overlayUnit.unattended_count,
            coveringOfficerCount: overlayUnit.covering_officer_count,
            coveringOfficers: selectedId === location.id ? overlayUnit.covering_officers : undefined,
          }
        : undefined,
    };
  };

  return (
    <svg
      viewBox={usaMap.viewBox}
      preserveAspectRatio="xMidYMid meet"
      className="map-svg-stage"
      role="group"
      aria-label="States: use the arrow keys to move between states"
      // Escape (hide the card) is handled once, by the map's .map-levels.
      onKeyDown={moveRovingFocus}
    >
      {views.map((view) => {
        const { location, rollup, cls } = view;
        const hasFill = !loading && cls !== null;
        const classes = [
          'map-region',
          loading ? 'is-loading' : '',
          hasFill ? 'has-data' : '',
          !loading && !hasFill ? 'is-empty' : '',
          hasFill ? `lvl-${cls}` : '',
          selectedId === location.id ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        const activate = (viaKeyboard: boolean) => onActivate(location, Boolean(rollup), viaKeyboard);
        return (
          <path
            key={location.id}
            d={location.path}
            className={classes}
            role="button"
            tabIndex={location.id === tabStopId ? 0 : -1}
            data-map-unit={location.id}
            data-map-class={hasFill ? cls : 0}
            data-target-size-exempt="geographic-shape"
            aria-label={stateAriaLabel(
              location.name,
              rollup
                ? {
                    count: rollup.addressable,
                    avgScore: rollup.avg_score,
                    topSegment: view.topSegment,
                    unattended: overlayByUnit ? view.overlayUnit?.unattended_count ?? null : undefined,
                  }
                : undefined,
              loading ? 'loading' : 'ready',
              view.inFootprint,
            )}
            aria-keyshortcuts="Enter"
            onMouseEnter={(event) => setHover(hoverFor(view, event.clientX, event.clientY))}
            onMouseMove={(event) => setHover((h) => (h ? { ...h, x: event.clientX, y: event.clientY } : h))}
            onMouseLeave={() => setHover(null)}
            onFocus={(event) => {
              setActiveId(location.id);
              showCardOnFocus(event.currentTarget, (anchor) => setHover(hoverFor(view, anchor.x, anchor.y)));
            }}
            onBlur={() => setHover(null)}
            onClick={() => activate(false)}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' && event.key !== ' ') return;
              event.preventDefault();
              activate(true);
            }}
          />
        );
      })}
      {/* USPS labels on populated states, painted after every path so no
          neighbour covers them. Decorative for assistive technology: the
          path's accessible name already starts with the state name. */}
      <g className="map-labels" aria-hidden="true">
        {views.map((view) =>
          !loading && view.cls !== null && view.location.labelAt ? (
            <text
              key={view.location.id}
              x={view.location.labelAt[0]}
              y={view.location.labelAt[1]}
              className={`map-label map-label--lvl-${view.cls}`}
            >
              {view.location.id.toUpperCase()}
            </text>
          ) : null,
        )}
      </g>
    </svg>
  );
}
