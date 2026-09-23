/**
 * USChoroplethMapLegend — the geography map's `.map-legend` block: the
 * selection headline, the S9 overlay facts with their evidence chip, the
 * class bar with its real break values, the "Colored by" caption, the
 * degraded-overlay note, and the keyboard affordance. Extracted from
 * USChoroplethMap.tsx (file-size gate, plan item 3).
 *
 * Audit dataviz-02 (2026-09-21): the bar's five swatches are the ramp steps
 * the map paints (`--map-ramp-0..4`), and `.map-legend__range` prints the
 * scale's breaks between the prototype's Lower / Higher words. Declared
 * accessibility deviation from design_files/Module 0 Prototype.html:1864-1871
 * (an accent 15/30/50/70% bar with Lower / Higher only).
 */

import type { GeoAssignmentOverlayResponse } from '../../lib/api';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import { EvidenceChip } from '../Primitives';
import { classRanges, formatBreak, type ChoroplethScale } from './USChoroplethMap.scale';

interface USChoroplethMapLegendProps {
  overlayOn: boolean;
  overlayData: GeoAssignmentOverlayResponse | null;
  overlayLoading: boolean;
  overlayError: string | null;
  /** Borrowers in the current selection, used when the overlay is off. Null = unknown. */
  totalCount: number | null;
  /** The class scale the map is painted with; null when nothing is painted. */
  scale: ChoroplethScale | null;
  /** Which units the scale was built over, when not all of them ("over the 24 densest of 212 ZIPs"). */
  scaleScope?: string | null;
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
  scale,
  scaleScope = null,
  segmentCaption,
  segmentFilter,
}: USChoroplethMapLegendProps) {
  const ranges = scale ? classRanges(scale) : [];
  const barLabel = scale
    ? `Fill classes: no borrowers or no data; ${ranges
        .map((range) => (range.to === null
          ? `${range.from.toLocaleString('en-US')} or more`
          : `${range.from.toLocaleString('en-US')} to ${range.to.toLocaleString('en-US')}`))
        .join('; ')}`
    : 'Fill classes: lower to higher';
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
              : totalCount !== null
                ? totalCount.toLocaleString()
                : '—'}
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
      <div className="map-legend__bar" role="img" aria-label={barLabel}>
        <span className="lvl-0" />
        <span className="lvl-1" />
        <span className="lvl-2" />
        <span className="lvl-3" />
        <span className="lvl-4" />
      </div>
      <div className="map-legend__range" data-scale={scale?.kind ?? 'none'} aria-hidden="true">
        <span className="map-legend__end">Lower</span>
        {scale?.breaks.map((value, index) => {
          const label = formatBreak(value);
          // A break that prints like the one before it (a tie collapsed a
          // class, or compact rounding) is printed once: "1 1 2" read as a
          // typo. The exact value stays in data-break and the title; the
          // bar's accessible name lists the exact ranges.
          const repeat = index > 0 && formatBreak(scale.breaks[index - 1]) === label;
          return (
            <span
              key={index}
              className={`map-legend__break map-legend__break--${index + 1}`}
              data-break={value}
              title={value.toLocaleString('en-US')}
            >
              {repeat ? '' : label}
            </span>
          );
        })}
        <span className="map-legend__end map-legend__end--high">Higher</span>
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
        {scale && (
          <span className="map-legend__scale">
            {' · '}
            {scale.kind === 'sqrt' ? 'square-root scale' : 'quartiles'}
            {scaleScope ? ` ${scaleScope}` : ''}
          </span>
        )}
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
          focused. Copy matches the actual handlers: arrows move the single
          tab stop, Enter/Space drill in, Escape hides the card; backing out
          is via the breadcrumb trail above the map. */}
      <div className="map-legend__hint">
        <kbd>Arrow keys</kbd> move · <kbd>Enter</kbd> or <kbd>Space</kbd> drills in · <kbd>Esc</kbd> hides
        the card · use the breadcrumbs to go back
      </div>
    </div>
  );
}
