/**
 * The geography map's "View as table" rows (audit a11y-04 slice 3), as a
 * pure function of the map's derived facts. Moved out of USChoroplethMap.tsx
 * (wave 5a, d2 step 0).
 *
 * deviation:map-table-coverage-groups (dataviz-10, wow-stage-1). At the
 * state level the table lists the whole book, in three groups: the
 * populated states the map paints and the keys visit (sortable); "Not drawn
 * on the map", every id with no map location (PR, VI: from the rollups, plus
 * the rate grid in rate mode and the overlay units in unattended mode),
 * named by its USPS code; and "No borrowers in this selection", one row per
 * drawn state the keys skip (no button, '—' values, a note saying why).
 *
 * Footers: the borrower count sums the drawn-populated and not-drawn groups
 * (the legend's whole-book total); the extra column sums every group (the
 * overlay is segment-agnostic, so an empty-group state can carry unattended
 * leads), which then equals the grid total in rate mode and the overlay's
 * total_unattended in unattended mode. Footer equals legend in every mode.
 */
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import { formatCount } from '../../lib/formatters';
import { safeSegmentName } from '../../lib/segmentMetadata';
import type { StateRollup, ZipRollup } from '../../types';
import type { MapScenarioView } from './rateScenario.logic';
import { classify, type ChoroplethScale } from './USChoroplethMap.scale';
import type { MapTableRow } from './USChoroplethMapTable';
import type { Level, UsaSvgMap, UsaSvgMapLocation } from './USChoroplethMap.utils';

export interface MapTableInputs {
  shownScenario: MapScenarioView | null;
  overlayActive: boolean;
  overlayByUnit: Record<string, GeoAssignmentOverlayUnit>;
  level: Level;
  stateFacts: Record<string, StateRollup> | null;
  usaMap: UsaSvgMap | null;
  scale: ChoroplethScale | null;
  drillBehavior: 'filter' | 'navigate';
  activateState: (location: UsaSvgMapLocation, hasFacts: boolean, moveFocus: boolean) => void;
  zipFacts: Record<string, ZipRollup> | null;
  footprintStates: Record<string, string>;
}

/** The table's row groups for the level on screen. */
export interface MapTableGroups {
  /** Populated units the map paints (sortable; ZIP level: every ZIP). */
  rows: MapTableRow[];
  /** Ids the map cannot draw (no map location), state level only. */
  offMap: MapTableRow[];
  /** Drawn states with no borrowers in this selection (state level only). */
  empty: MapTableRow[];
}

export const OFF_MAP_GROUP_LABEL = 'Not drawn on the map';
export const EMPTY_GROUP_LABEL = 'No borrowers in this selection';
const OUT_OF_SCOPE_NOTE = 'Outside Cotality evaluation scope';

/** Table rows for the level on screen: the numbers the map paints, and the drawn states it skips. */
export function buildMapTableRows(inputs: MapTableInputs): MapTableGroups {
  const {
    shownScenario,
    overlayActive,
    overlayByUnit,
    level,
    stateFacts,
    usaMap,
    scale,
    drillBehavior,
    activateState,
    zipFacts,
    footprintStates,
  } = inputs;
  const value = (count: number, unitKey: string) =>
    shownScenario ? shownScenario.inTheMoneyById[unitKey] : overlayActive ? overlayByUnit[unitKey]?.unattended_count : count;
  // Rate mode (wow-stage-1): change versus today and the contactable subset
  // at the shown step, on every state row (drawn, not drawn, empty).
  const scenarioCells = (id: string) => shownScenario
    ? { change: shownScenario.changeById?.[id] ?? null, scenarioContactable: shownScenario.contactableById?.[id] ?? null }
    : {};
  if (level === 'state') {
    if (!stateFacts || !usaMap) return { rows: [], offMap: [], empty: [] };
    const populated = (id: string) => (stateFacts[id]?.addressable ?? 0) > 0;
    const rows = usaMap.locations
      .filter((location) => populated(location.id))
      .map((location) => {
        const rollup = stateFacts[location.id];
        return {
          id: location.id,
          name: location.name,
          count: rollup.addressable,
          avgScore: rollup.avg_score,
          topSegment: rollup.top_segment_code ? safeSegmentName(rollup.top_segment_code) ?? undefined : undefined,
          contactable: rollup.contactable,
          // The value the fill encodes when it is not borrowers (table column).
          extra: shownScenario || overlayActive ? value(0, location.id) ?? null : undefined,
          ...scenarioCells(location.id),
          cls: classify(scale, value(rollup.addressable, location.id)),
          // The row's button is gone after the drill, whatever pressed it.
          onOpen: drillBehavior === 'filter' ? () => activateState(location, true, true) : undefined,
        };
      });
    const empty = usaMap.locations
      .filter((location) => !populated(location.id))
      .map((location) => ({
        id: location.id,
        name: location.name,
        count: null,
        avgScore: null,
        extra: shownScenario || overlayActive ? value(0, location.id) ?? null : undefined,
        ...scenarioCells(location.id),
        cls: null,
        note: footprintStates[location.id] ? EMPTY_GROUP_LABEL : OUT_OF_SCOPE_NOTE,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
    // The whole book: ids the map has no shape for, from every source the
    // mode reads (the rollups; plus the grid, or the overlay units).
    const drawn = new Set(usaMap.locations.map((location) => location.id));
    const sourceIds = [
      ...Object.keys(stateFacts),
      ...(shownScenario ? Object.keys(shownScenario.inTheMoneyById) : []),
      ...(overlayActive ? Object.keys(overlayByUnit) : []),
    ];
    const offMap = [...new Set(sourceIds)]
      .filter((id) => !drawn.has(id))
      .sort()
      .map((id): MapTableRow => {
        const rollup = stateFacts[id];
        return {
          id,
          name: id.toUpperCase(),
          count: rollup ? rollup.addressable : null,
          avgScore: rollup ? rollup.avg_score : null,
          topSegment: rollup?.top_segment_code ? safeSegmentName(rollup.top_segment_code) ?? undefined : undefined,
          contactable: rollup?.contactable,
          extra: shownScenario || overlayActive ? value(0, id) ?? null : undefined,
          ...scenarioCells(id),
          cls: null,
        };
      });
    return { rows, offMap, empty };
  }
  const rows = Object.values(zipFacts ?? {}).map((rollup) => ({
    id: rollup.zip,
    name: rollup.zip,
    count: rollup.addressable_borrowers ?? 0,
    avgScore: rollup.avg_opportunity_score ?? null,
    topSegment: rollup.top_segment_code ? safeSegmentName(rollup.top_segment_code) ?? undefined : undefined,
    extra: overlayActive ? overlayByUnit[rollup.zip]?.unattended_count ?? null : undefined,
    cls: classify(scale, value(rollup.addressable_borrowers ?? 0, rollup.zip)),
  }));
  return { rows, offMap: [], empty: [] };
}

/**
 * The legend's note for the not-drawn group at the state level: "Includes
 * 1,234 in PR, VI (not drawn on the map)", in the value the fill encodes
 * (borrowers, or the extra column in rate / unattended mode); null when every
 * id is drawn.
 */
export function offMapCaption(offMap: readonly MapTableRow[], fill: 'count' | 'extra'): string | null {
  if (offMap.length === 0) return null;
  const total = offMap.reduce((sum, row) => sum + ((fill === 'count' ? row.count : row.extra) ?? 0), 0);
  return `Includes ${formatCount(total)} in ${offMap.map((row) => row.name).join(', ')} (not drawn on the map)`;
}
