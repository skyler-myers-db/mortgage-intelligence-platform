/**
 * USChoroplethMapLegend — the geography map's `.map-legend` block: the
 * selection headline, the S9 overlay facts with their evidence chip, the
 * gradient bar, the "Colored by" caption, the degraded-overlay note, and the
 * keyboard affordance. Extracted from USChoroplethMap.tsx (file-size gate,
 * plan item 3); markup, class names, and copy are unchanged.
 */

import type { GeoAssignmentOverlayResponse } from '../../lib/api';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { EvidenceChip } from '../Primitives';

interface USChoroplethMapLegendProps {
  overlayOn: boolean;
  overlayData: GeoAssignmentOverlayResponse | null;
  overlayLoading: boolean;
  overlayError: string | null;
  /** Borrowers in the current selection, used when the overlay is off. */
  totalCount: number;
  /** "marketable population" / "opportunity within <segments>". */
  segmentCaption: string;
  /** Active segment filter — drives the overlay's scope-mismatch note. */
  segmentFilter?: string[];
}

export function USChoroplethMapLegend({
  overlayOn,
  overlayData,
  overlayLoading,
  overlayError,
  totalCount,
  segmentCaption,
  segmentFilter,
}: USChoroplethMapLegendProps) {
  return (
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
  );
}
