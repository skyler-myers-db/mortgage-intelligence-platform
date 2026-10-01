/**
 * S9 "Start campaign from this geography" link target, as a pure function of
 * the map's drill and overlay. Moved out of USChoroplethMap.tsx (wave 5a, d2
 * step 0): the input's field names are the closure identifiers the body read
 * there, so the body is the one the component ran.
 *
 * The drilled state stays the campaign context while its ZIP grid is on
 * screen; a ZIP tile click deep-links to the Lead Queue instead. Carries the
 * segment filter + mode and, when the overlay is loaded, the state's lead /
 * unattended snapshot counts.
 */
import type { GeoAssignmentOverlayResponse, GeoAssignmentOverlayUnit } from '../../lib/api';
import { buildCampaignPrefillSearch, makeCampaignPrefill } from '../../lib/campaignPrefill';
import type { MapSelection } from './USChoroplethMap.selection';

export interface MapCampaignInputs {
  activeStateCode: string | null;
  /** The drill; only its ZIP is read (a ZIP on screen has no campaign context). */
  current: Pick<MapSelection, 'zip'>;
  overlayOn: boolean;
  overlayData: GeoAssignmentOverlayResponse | null;
  overlayByUnit: Record<string, GeoAssignmentOverlayUnit>;
  segmentFilter: string[] | undefined;
  segmentFilterMode: 'any' | 'all';
}

/** `/portfolio-builder?...` for the drilled state, or null when there is no campaign context. */
export function campaignPrefillPath(inputs: MapCampaignInputs): string | null {
  const {
    activeStateCode,
    current,
    overlayOn,
    overlayData,
    overlayByUnit,
    segmentFilter,
    segmentFilterMode,
  } = inputs;
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
}
