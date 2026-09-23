/**
 * USChoroplethMapZipLevel — the ZIP rung of the geography drill: the densest-N
 * tile grid for the drilled state, the honest empty state, and the reconcile
 * note that discloses what the grid cannot show. Extracted from
 * USChoroplethMap.tsx's `renderZipLevel` closure (file-size gate, plan item 3).
 *
 * Audit a11y-04 / dataviz-02 (2026-09-21): the tiles are real buttons inside
 * list items (they used to be `<button role="listitem">`, which stripped the
 * button role), one roving Tab stop with arrow-key movement, a data-rich
 * accessible name, and the hover card on keyboard focus. Tiles paint the
 * same four-step ramp as the state map and the legend (`--map-ramp-*`), not a
 * private alpha ramp the legend never showed.
 */

import { useEffect, useRef, useState, type CSSProperties, type Dispatch, type SetStateAction } from 'react';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import { safeSegmentName } from '../../lib/segmentMetadata';
import type { StateRollup, ZipRollup } from '../../types';
import { claimDrillFocus, moveRovingFocus, showCardOnFocus, zipAriaLabel } from './USChoroplethMap.a11y';
import { classify, type ChoroplethScale } from './USChoroplethMap.scale';
import type { HoverState } from './USChoroplethMap.utils';

/** Densest-N ZIP tiles rendered per state. The grid stays readable, but
 *  the remainder MUST be disclosed — see the reconcile note below. */
export const ZIP_TILE_CAP = 24;

interface USChoroplethMapZipLevelProps {
  /** Display name for the drilled state, used in copy and aria labels. */
  drillStateName: string;
  /** ZIP rollups for the drilled state, keyed by ZIP. */
  byZip: Record<string, ZipRollup>;
  /** State-level rollup for the drilled state — the reconcile denominator. */
  stateFacts: StateRollup | undefined;
  /** The scale the fill follows (borrowers, or unattended leads under the overlay). */
  scale: ChoroplethScale | null;
  overlayActive: boolean;
  overlayByUnit: Record<string, GeoAssignmentOverlayUnit>;
  /** ZIP currently selected, or null. Gates covering-officer disclosure. */
  selectedZip: string | null;
  /** Move focus here once the rung renders (keyboard drill): to the tab-stop
   *  tile, or to "Open Lead Queue" when the state has no ZIP rollup. */
  autoFocus?: boolean;
  /** Called once the rung has a focus target, so a later Back navigation does not steal focus. */
  onAutoFocused?: () => void;
  setHover: Dispatch<SetStateAction<HoverState | null>>;
  /** Select the ZIP and deep-link to its filtered Lead Queue. */
  onSelectZip: (zip: string) => void;
  /** Open the whole drilled state in the Lead Queue. */
  onOpenStateQueue: () => void;
}

export function USChoroplethMapZipLevel({
  drillStateName,
  byZip,
  stateFacts,
  scale,
  overlayActive,
  overlayByUnit,
  selectedZip,
  autoFocus = false,
  onAutoFocused,
  setHover,
  onSelectZip,
  onOpenStateQueue,
}: USChoroplethMapZipLevelProps) {
  const zipsFromApi = Object.values(byZip);
  // Sorted descending by count so densest ZIPs land top-left (Pareto).
  const sorted = [...zipsFromApi].sort(
    (a, b) => (b.addressable_borrowers ?? 0) - (a.addressable_borrowers ?? 0),
  );
  const visible = sorted.slice(0, ZIP_TILE_CAP);
  const [activeZip, setActiveZip] = useState<string | null>(null);
  const tabStopZip =
    (activeZip && visible.some((rollup) => rollup.zip === activeZip) ? activeZip : null)
    ?? (selectedZip && visible.some((rollup) => rollup.zip === selectedZip) ? selectedZip : null)
    ?? visible[0]?.zip
    ?? null;
  const listRef = useRef<HTMLUListElement | null>(null);
  const emptyActionRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!autoFocus) return;
    // The state path that had focus is gone. Without a tile (an empty
    // rollup), the empty state's action is the target, never <body>.
    const tile = listRef.current?.querySelector<HTMLElement>('[data-map-unit][tabindex="0"]') ?? null;
    const target = tile ?? emptyActionRef.current;
    if (!target) return;
    claimDrillFocus(target);
    onAutoFocused?.();
  }, [autoFocus, onAutoFocused]);

  if (zipsFromApi.length === 0) {
    // API returned empty — the state is outside the Cotality eval share
    // or the CTAS hasn't populated ZIPs for it. Give the user a graceful
    // fallback path into the state's lead queue.
    return (
      <div className="map-stage">
        <div className="map-center-card map-center-card--narrow">
          <div className="text-2 mb-2">
            No ZIP-level rollup for {drillStateName}.
          </div>
          <div className="mb-3">
            Browse this state&apos;s lead queue — the filter will narrow to borrowers in {drillStateName}.
          </div>
          <button
            ref={emptyActionRef}
            type="button"
            className="btn btn--ghost"
            onClick={onOpenStateQueue}
          >
            Open Lead Queue for {drillStateName}
          </button>
        </div>
      </div>
    );
  }

  // Reconcile what the tiles show against what the STATE tile claimed.
  // Two independent ways the drill under-counts, both previously silent:
  //   1. the tile cap hides the tail (IL: 24 of 212 ZIPs = 29.9% of the
  //      state's borrowers, 1,297,115 invisible — live audit 2026-08-10);
  //   2. borrowers with no ZIP never appear in zip_rollup at all
  //      (79,496 across the footprint; CO 8.7%, WA 5.8%).
  // A drill-down that silently shows a third of the population reads as a
  // broken widget, so state it.
  const visibleSum = visible.reduce(
    (total, rollup) => total + (rollup.addressable_borrowers ?? 0),
    0,
  );
  const stateTotal = stateFacts?.addressable ?? null;
  const unassigned = stateFacts?.zip_unassigned_count ?? null;
  const hiddenZipCount = sorted.length - visible.length;
  return (
    <>
    <ul
      ref={listRef}
      className="zip-tiles"
      role="list"
      aria-label={`ZIPs in ${drillStateName}`}
      // Escape (hide the card) is handled once, by the map's .map-levels.
      onKeyDown={moveRovingFocus}
    >
      {visible.map((rollup, tileIndex) => {
        const count = rollup.addressable_borrowers ?? null;
        const avgScore = rollup.avg_opportunity_score ?? null;
        const topSegCode = rollup.top_segment_code ?? null;
        const topSegment = topSegCode ? (safeSegmentName(topSegCode) ?? undefined) : undefined;
        const overlayUnit = overlayActive ? overlayByUnit[rollup.zip] : undefined;
        const unattended = overlayUnit ? overlayUnit.unattended_count : null;
        const cls = classify(scale, overlayActive ? unattended : count) ?? 0;
        const isSelected = selectedZip === rollup.zip;
        const classes = [
          'zip-tile',
          `zip-tile--lvl-${cls}`,
          isSelected ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        const hover = (x: number, y: number): HoverState => ({
          x,
          y,
          name: `ZIP ${rollup.zip}, ${drillStateName}`,
          count,
          avgScore,
          topSegment,
          sourceHint: 'mip.gold.zip_rollup',
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
        });
        return (
          <li key={rollup.zip} className="zip-tiles__item">
            <button
              type="button"
              className={classes}
              // Staggered "settle into the grid" entrance (Buyer-Wow #4):
              // the stage cap keeps later tiles from lagging; CSS gates the
              // animation behind prefers-reduced-motion.
              style={{ '--tile-i': Math.min(tileIndex, 24) } as CSSProperties}
              data-map-unit={rollup.zip}
              data-map-class={cls}
              tabIndex={rollup.zip === tabStopZip ? 0 : -1}
              aria-label={zipAriaLabel(rollup.zip, {
                count,
                avgScore,
                topSegment,
                unattended: overlayActive ? unattended : undefined,
              })}
              onMouseEnter={(e) => setHover(hover(e.clientX, e.clientY))}
              onMouseMove={(e) =>
                setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
              }
              onMouseLeave={() => setHover(null)}
              onFocus={(e) => {
                setActiveZip(rollup.zip);
                showCardOnFocus(e.currentTarget, (anchor) => setHover(hover(anchor.x, anchor.y)));
              }}
              onBlur={() => setHover(null)}
              onClick={() => onSelectZip(rollup.zip)}
            >
              <span className="zip-tile__code">{rollup.zip}</span>
              <span className="zip-tile__count">
                {count !== null ? count.toLocaleString() : '—'}
              </span>
              {overlayActive && (
                <span className="zip-tile__overlay">
                  {unattended !== null ? unattended.toLocaleString() : '—'} unattended
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
    {(hiddenZipCount > 0 || (unassigned ?? 0) > 0) && (
      <div className="zip-tiles__reconcile text-2" role="note">
        {hiddenZipCount > 0 && (
          <span>
            Showing the {visible.length} densest of {sorted.length.toLocaleString()} ZIPs
            {' — '}
            {visibleSum.toLocaleString()}
            {stateTotal !== null ? ` of ${stateTotal.toLocaleString()}` : ''} borrowers in view.
          </span>
        )}
        {(unassigned ?? 0) > 0 && (
          <span>
            {' '}
            {(unassigned ?? 0).toLocaleString()} borrowers in {drillStateName} carry no ZIP and
            appear in no tile.
          </span>
        )}
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onOpenStateQueue}
        >
          Open all {stateTotal !== null ? stateTotal.toLocaleString() : ''} in Lead Queue
        </button>
      </div>
    )}
    </>
  );
}
