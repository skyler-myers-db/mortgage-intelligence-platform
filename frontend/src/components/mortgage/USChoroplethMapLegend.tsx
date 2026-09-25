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
 *
 * Rate Lever (audit wow-stage-1): in rate mode the legend recounts "in the
 * money at scenario" ('—' until the grid is usable), always shows the
 * "Scenario, not a forecast" chip itself (while the control chunk loads, and
 * while the read is warming, failed or not built), says the Lead Queue keeps
 * today's par, and hosts the lazy RateScenarioControl in a fixed-height slot.
 * The control's chunk also carries the warming / failed / not-built lines,
 * so their copy stays out of the map chunk both hero routes load (route
 * budget). The one line that cannot live there is the chunk's own failure
 * (a chunk a redeploy retired, a network blip): the legend says it, over
 * the borrower fill the map keeps, with Reload (the next document asks for
 * the current build's chunk). `.map-legend__lever*` are declared app
 * extensions of the prototype legend (USChoroplethMap.css).
 */

import type { ComponentType } from 'react';
import type { GeoAssignmentOverlayResponse } from '../../lib/api';
import { DRAWER_SOURCES } from '../../lib/drawerSources';
import type { RateSensitivityResponse } from '../../types/rateScenario';
import { Chip, EvidenceChip } from '../Primitives';
import type { MapScenarioView, RateScenarioIndex } from './rateScenario.logic';
import { classRanges, formatBreak, type ChoroplethScale } from './USChoroplethMap.scale';
import type { GeoRead } from './useChoroplethLiveFacts';
import { formatCount } from '../../lib/formatters';

/** What the lazy RateScenarioControl reads. */
export interface RateLeverInputs {
  read: GeoRead<RateSensitivityResponse>;
  /** The indexed grid; null while warming, failed or not built. */
  index: RateScenarioIndex | null;
  /** The grid at the map's shown (deferred) step. */
  view: MapScenarioView | null;
  /** The thumb's step (undeferred). */
  step: number;
  onStepChange: (step: number) => void;
  /** The drilled state (ZIP level, where tiles keep borrower colouring), or null for the whole book. */
  scope: { id: string; name: string } | null;
}

/** The Rate Lever's legend inputs; present only while the rate colouring is on. */
export interface LegendRate extends RateLeverInputs {
  /** The control once its lazy chunk loaded; null while it loads or after it failed. */
  control: ComponentType<{ rate: RateLeverInputs }> | null;
  /** The control's chunk failed to load: the map keeps the borrower fill. */
  controlFailed: boolean;
}

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
  /** Rate Lever inputs while the rate colouring is on; null otherwise. */
  rate?: LegendRate | null;
  /** A placeholder cohort is on screen (runtime-06): the legend desaturates with the fill. */
  updating?: boolean;
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
  rate = null,
  updating = false,
}: USChoroplethMapLegendProps) {
  // What the fill encodes once the grid is up: the scenario over states, or
  // (drilled) the borrower tiles the scenario cannot split.
  const rateCaption = !rate?.index
    ? null
    : rate.scope
      ? 'ZIP tiles show borrowers; scenarios are computed per state.'
      : 'borrowers in the money at the scenario par rate';
  // The recount: the drilled state, or the whole book.
  const rateTotal = rate?.scope ? rate.view?.inTheMoneyById[rate.scope.id] : rate?.view?.total;
  const Control = rate?.control ?? null;
  const ranges = scale ? classRanges(scale) : [];
  const barLabel = scale
    ? `Fill classes: no borrowers or no data; ${ranges
        .map((range) => (range.to === null
          ? `${range.from.toLocaleString('en-US')} or more`
          : `${range.from.toLocaleString('en-US')} to ${range.to.toLocaleString('en-US')}`))
        .join('; ')}`
    : 'Fill classes: lower to higher';
  return (
    <div className={`map-legend stable-refresh-region ${updating ? 'is-updating' : ''}`}>
      <div className="map-legend__header">
        <span>
          {rate ? 'In the money at scenario' : overlayOn ? 'Unattended leads in selection' : 'Borrowers in selection'}{' '}
          <span className="map-legend__value">
            {/* Unknown renders the formatter's '—', never a fabricated 0. */}
            {formatCount(rate ? rateTotal : overlayOn ? overlayData?.total_unattended : totalCount)}
          </span>
        </span>
      </div>
      {overlayOn && overlayData && (
        // S9 overlay facts: the two inputs of the subtraction, plus the
        // evidence affordance tracing them to Lakebase + Unity Catalog.
        <div className="map-legend__overlay-facts">
          <span>
            {formatCount(overlayData.total_leads)} leads ·{' '}
            {formatCount(overlayData.total_assigned)} assigned
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
          {rateCaption ?? (overlayOn
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
            : segmentCaption)}
        </span>
        {scale && (
          <span className="map-legend__scale">
            {' · '}
            {scale.kind === 'sqrt' ? 'square-root scale' : 'quartiles'}
            {scaleScope ? ` ${scaleScope}` : ''}
          </span>
        )}
      </div>
      {rate && (
        // The label never waits on the lazy chunk or the read: it is the
        // legend's own, in every rate state.
        <div className="map-legend__lever">
          <div className="map-legend__lever-note">
            <Chip variant="neutral" icon="info">
              Scenario, not a forecast
            </Chip>
            <span>The Lead Queue and campaigns use today&apos;s par rate.</span>
          </div>
          {/* The slot reserves the control's height, so neither the chunk
              load nor a status line moves the stage. */}
          <div className="map-legend__lever-slot">
            {rate.controlFailed ? (
              <div className="map-legend__caption map-legend__caption--degraded" role="status">
                Rate scenarios could not load. Showing borrower counts.{' '}
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => window.location.reload()}>
                  Reload
                </button>
              </div>
            ) : (
              Control && <Control rate={rate} />
            )}
          </div>
        </div>
      )}
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
