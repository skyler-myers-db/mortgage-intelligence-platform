import type { ReactNode } from 'react';
import { formatCount } from '../../lib/formatters';

/**
 * The actions under every Genie rows block (audit 2026-09-21 `genie-06`,
 * slice 2): "Show all" replaces the capped compact table in place with every
 * row and column the answer holds. `.genie-answer__rows-actions` is a
 * documented BEM extension of `.genie-answer` (the prototype's Genie bubble,
 * design_files/index.html:741-760, has no row controls).
 */

export interface GenieRowsExtent {
  /** Rows the answer holds (what "Show all" can show). */
  held: number;
  /** The row count the answer reports; larger than `held` only when a
   *  History replay kept fewer rows than the query returned. */
  reported: number | null;
  /** Rows beyond the compact table's cap. */
  hiddenRows: number;
  /** Every column the rows hold. */
  columns: number;
  /** Columns beyond the compact table's cap. */
  hiddenColumns: number;
}

/** A History replay kept fewer rows than the query returned. */
export function rowsTrimmed(extent: Pick<GenieRowsExtent, 'held' | 'reported'>): boolean {
  return extent.reported !== null && extent.reported > extent.held;
}

/** "Show all 120 rows and 7 columns", or null when nothing is hidden. */
export function showAllLabel(extent: GenieRowsExtent): string | null {
  const trimmed = rowsTrimmed(extent);
  const rows =
    extent.hiddenRows > 0 || trimmed
      ? trimmed
        ? `${formatCount(extent.held)} rows held (of ${formatCount(extent.reported)})`
        : `${formatCount(extent.held)} rows`
      : null;
  const columns = extent.hiddenColumns > 0 ? `${formatCount(extent.columns)} columns` : null;
  if (!rows && !columns) return null;
  return `Show all ${[rows, columns].filter(Boolean).join(' and ')}`;
}

/** The note the expanded view carries for a trimmed History replay. */
export function heldRowsNote(extent: Pick<GenieRowsExtent, 'held' | 'reported'>): string | null {
  if (!rowsTrimmed(extent)) return null;
  return (
    `Restored from History: ${formatCount(extent.held)} of ${formatCount(extent.reported)} rows were kept. ` +
    'Ask again for the complete result.'
  );
}

export function GenieAnswerRowsActions({
  extent,
  expanded,
  onToggle,
  children,
}: {
  extent: GenieRowsExtent;
  expanded: boolean;
  onToggle: () => void;
  /** Further row actions (the audited CSV download). */
  children?: ReactNode;
}) {
  const label = showAllLabel(extent);
  if (!label && !children) return null;
  return (
    <div className="genie-answer__rows-actions">
      {label && (
        <button
          type="button"
          className="btn btn--ghost btn--sm genie-answer__show-all"
          aria-expanded={expanded}
          onClick={onToggle}
        >
          {expanded ? 'Show fewer' : label}
        </button>
      )}
      {children}
    </div>
  );
}
