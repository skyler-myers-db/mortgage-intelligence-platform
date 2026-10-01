/**
 * The geography map's hover / focus card content, as a pure function of the
 * hovered unit (D-dataviz-geo-d1, audit runtime-07). The stages used to build
 * a card closure per unit and per render (USChoroplethMapStates `hoverFor`,
 * the ZIP tiles' `hover()`); the map now holds only the hovered unit's id in
 * React state and builds its card here, once per hovered unit.
 *
 * The two bodies are moved verbatim. Counts are never coerced: a contactable
 * of 0 stays 0 and a missing one stays null ("not reported"), and covering
 * officers are named only for the selected unit.
 */
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import { safeSegmentName } from '../../lib/segmentMetadata';
import type { StateRollup, ZipRollup } from '../../types';
import type { HoverState, Level, UsaSvgMap } from './USChoroplethMap.utils';

/** What the card shows: HoverState without the pointer position (the tip is placed outside React). */
export type MapCard = Omit<HoverState, 'x' | 'y'>;

export const SOURCE_IN_SCOPE = 'mip.gold.funnel_snapshot_daily + mip.gold.state_top_segment';
export const SOURCE_OUT_OF_SCOPE = 'Outside Cotality evaluation scope';
export const SOURCE_ZIP = 'mip.gold.zip_rollup';

export interface MapCardInputs {
  usaMap: UsaSvgMap | null;
  /** Per-state rollups keyed by lowercase USPS code; null while loading. */
  stateFacts: Record<string, StateRollup> | null;
  /** Overlay units keyed by lowercase USPS code / ZIP while the overlay colours the map; else null. */
  overlayByUnit: Record<string, GeoAssignmentOverlayUnit> | null;
  footprintStates: Record<string, string>;
  /** Lowercase id of the selected state, if any. */
  selectedId: string | null;
  /** Display name of the drilled state (ZIP cards). */
  drillStateName: string;
  /** ZIP rollups of the drilled state, keyed by ZIP; null while loading. */
  zipFacts: Record<string, ZipRollup> | null;
  /** ZIP currently selected, or null. Gates covering-officer disclosure. */
  selectedZip: string | null;
}

/** The card for one hovered or focused unit; null when the unit is not on the stage any more. */
export function buildMapCard(level: Level, id: string, inputs: MapCardInputs): MapCard | null {
  return level === 'state' ? stateCard(id, inputs) : zipCard(id, inputs);
}

function stateCard(id: string, inputs: MapCardInputs): MapCard | null {
  const location = inputs.usaMap?.locations.find((candidate) => candidate.id === id);
  if (!location) return null;
  const rollup = inputs.stateFacts?.[id];
  const overlayUnit = inputs.overlayByUnit?.[id];
  const view = {
    topSegment: rollup?.top_segment_code ? safeSegmentName(rollup.top_segment_code) || undefined : undefined,
    inFootprint: Boolean(inputs.footprintStates[id]),
  };
  const { selectedId } = inputs;
  return {
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
}

function zipCard(zip: string, inputs: MapCardInputs): MapCard | null {
  const rollup = inputs.zipFacts?.[zip];
  if (!rollup) return null;
  const { drillStateName } = inputs;
  const count = rollup.addressable_borrowers ?? null;
  const avgScore = rollup.avg_opportunity_score ?? null;
  const topSegCode = rollup.top_segment_code ?? null;
  const topSegment = topSegCode ? (safeSegmentName(topSegCode) ?? undefined) : undefined;
  const overlayUnit = inputs.overlayByUnit?.[rollup.zip];
  const isSelected = inputs.selectedZip === rollup.zip;
  return {
    name: `ZIP ${rollup.zip}, ${drillStateName}`,
    count,
    avgScore,
    topSegment,
    sourceHint: SOURCE_ZIP,
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
  };
}
