/**
 * The geography map's "View as table" rows (audit a11y-04 slice 3), as a
 * pure function of the map's derived facts. Moved out of USChoroplethMap.tsx
 * (wave 5a, d2 step 0).
 *
 * deviation:map-table-coverage-groups (dataviz-10). At the state level the
 * table lists every drawn state, in groups: the populated states the map
 * paints and the keys visit (sortable), then a trailing "No borrowers in
 * this selection" group with one row per drawn state the keys skip (no
 * button, '—' values, a note saying why). The empty group never counts in
 * the borrower total; its extra-column values (the overlay is
 * segment-agnostic) do count in that column's total.
 */
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
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
  /** Drawn states with no borrowers in this selection (state level only). */
  empty: MapTableRow[];
}

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
  if (level === 'state') {
    if (!stateFacts || !usaMap) return { rows: [], empty: [] };
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
        cls: null,
        note: footprintStates[location.id] ? EMPTY_GROUP_LABEL : OUT_OF_SCOPE_NOTE,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
    return { rows, empty };
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
  return { rows, empty: [] };
}
