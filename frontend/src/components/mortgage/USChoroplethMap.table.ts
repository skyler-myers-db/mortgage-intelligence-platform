/**
 * The geography map's "View as table" rows (audit a11y-04 slice 3), as a
 * pure function of the map's derived facts. Moved out of USChoroplethMap.tsx
 * (wave 5a, d2 step 0): the input's field names are the closure identifiers
 * the body read there, so the body is the one the component ran.
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
}

/** Table rows for the level on screen: the numbers the map paints. */
export function buildMapTableRows(inputs: MapTableInputs): MapTableRow[] {
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
  } = inputs;
  const value = (count: number, unitKey: string) =>
    shownScenario ? shownScenario.inTheMoneyById[unitKey] : overlayActive ? overlayByUnit[unitKey]?.unattended_count : count;
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
          // The value the fill encodes when it is not borrowers (table column).
          extra: shownScenario || overlayActive ? value(0, location.id) ?? null : undefined,
          cls: classify(scale, value(rollup.addressable, location.id)),
          // The row's button is gone after the drill, whatever pressed it.
          onOpen: drillBehavior === 'filter' ? () => activateState(location, true, true) : undefined,
        };
      });
  }
  return Object.values(zipFacts ?? {}).map((rollup) => ({
    id: rollup.zip,
    name: rollup.zip,
    count: rollup.addressable_borrowers ?? 0,
    avgScore: rollup.avg_opportunity_score ?? null,
    topSegment: rollup.top_segment_code ? safeSegmentName(rollup.top_segment_code) ?? undefined : undefined,
    extra: overlayActive ? overlayByUnit[rollup.zip]?.unattended_count ?? null : undefined,
    cls: classify(scale, value(rollup.addressable_borrowers ?? 0, rollup.zip)),
  }));
}
