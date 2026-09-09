/**
 * USChoroplethMapZipLevel — the ZIP rung of the geography drill: the densest-N
 * tile grid for the drilled state, the honest empty / loading states, and the
 * reconcile note that discloses what the grid cannot show. Extracted from
 * USChoroplethMap.tsx's `renderZipLevel` closure (file-size gate, plan item
 * 3); markup, class names, and copy are unchanged.
 */

import type { CSSProperties, Dispatch, SetStateAction } from 'react';
import type { GeoAssignmentOverlayUnit } from '../../lib/api';
import { safeSegmentName } from '../../lib/segmentMetadata';
import type { StateRollup, ZipRollup } from '../../types';
import type { HoverState } from './USChoroplethMap.utils';

/** Densest-N ZIP tiles rendered per state. The grid stays readable, but
 *  the remainder MUST be disclosed — see the reconcile note below. */
export const ZIP_TILE_CAP = 24;

interface USChoroplethMapZipLevelProps {
  /** Display name for the drilled state, used in copy and aria labels. */
  drillStateName: string;
  /** ZIP rollups for the drilled state. `undefined` = still loading. */
  byZip: Record<string, ZipRollup> | undefined;
  /** State-level rollup for the drilled state — the reconcile denominator. */
  stateFacts: StateRollup | undefined;
  zipBucketer: (count: number | null | undefined) => 1 | 2 | 3 | 4;
  overlayActive: boolean;
  overlayByUnit: Record<string, GeoAssignmentOverlayUnit>;
  overlayBucketer: (count: number | null | undefined) => 1 | 2 | 3 | 4;
  /** ZIP currently selected, or null. Gates covering-officer disclosure. */
  selectedZip: string | null;
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
  zipBucketer,
  overlayActive,
  overlayByUnit,
  overlayBucketer,
  selectedZip,
  setHover,
  onSelectZip,
  onOpenStateQueue,
}: USChoroplethMapZipLevelProps) {
  const zipsFromApi = byZip ? Object.values(byZip) : [];

  if (byZip && zipsFromApi.length === 0) {
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

  if (!byZip) {
    return (
      <div className="map-stage map-stage--empty">
        Loading ZIPs…
      </div>
    );
  }

  // HTML/CSS grid (not SVG) so
  // the tiles use design-system tokens (r-md, sp, typography) and sit
  // cleanly in the map viewport, not overlapping the breadcrumbs above.
  // Sorted descending by count so densest ZIPs land top-left (Pareto).
  // Click deep-links to the filtered Lead Queue — seeing all borrowers
  // in the ZIP is the user's actual goal, not a single random sample.
  const sorted = [...zipsFromApi].sort(
    (a, b) => (b.addressable_borrowers ?? 0) - (a.addressable_borrowers ?? 0),
  );
  const visible = sorted.slice(0, ZIP_TILE_CAP);
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
    <div className="zip-tiles" role="list" aria-label={`ZIPs in ${drillStateName}`}>
      {visible.map((rollup, tileIndex) => {
        const count = rollup.addressable_borrowers ?? null;
        const avgScore = rollup.avg_opportunity_score ?? null;
        const topSegCode = rollup.top_segment_code ?? null;
        const topSegment = topSegCode ? (safeSegmentName(topSegCode) ?? undefined) : undefined;
        const overlayUnit = overlayActive ? overlayByUnit[rollup.zip] : undefined;
        const unattended = overlayUnit ? overlayUnit.unattended_count : null;
        const lvl = overlayActive
          ? (overlayUnit ? overlayBucketer(overlayUnit.unattended_count) : 1)
          : zipBucketer(count);
        const isSelected = selectedZip === rollup.zip;
        const classes = [
          'zip-tile',
          `zip-tile--lvl-${lvl}`,
          isSelected ? 'is-selected' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <button
            key={rollup.zip}
            type="button"
            className={classes}
            // Staggered "settle into the grid" entrance (Buyer-Wow #4):
            // the stage cap keeps later tiles from lagging; CSS gates the
            // animation behind prefers-reduced-motion.
            style={{ '--tile-i': Math.min(tileIndex, 24) } as CSSProperties}
            role="listitem"
            aria-label={`ZIP ${rollup.zip}, ${count !== null ? `${count.toLocaleString()} borrowers` : 'no data'}`}
            onMouseEnter={(e) =>
              setHover({
                x: e.clientX,
                y: e.clientY,
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
              })
            }
            onMouseMove={(e) =>
              setHover((h) => (h ? { ...h, x: e.clientX, y: e.clientY } : h))
            }
            onMouseLeave={() => setHover(null)}
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
        );
      })}
    </div>
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
