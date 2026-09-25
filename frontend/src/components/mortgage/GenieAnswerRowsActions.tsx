import { useId, useRef, useState, type ReactNode } from 'react';
import { formatCount } from '../../lib/formatters';
import { postGenieExportReceipt } from '../../lib/apiClients/genieExport';
import { Icon } from '../Icon';
import {
  GENIE_EXPORT_MAX_ROWS,
  GENIE_EXPORT_NOT_RECORDED,
  type GenieRowsExportTarget,
} from './GenieAnswer.exportTarget';
import type { GenieAnswerCsvRequest, GenieExportOutcome } from './GenieAnswer.export';

/** The export flow is an interaction chunk, loaded on the click. Never rejects. */
function runGenieExport(request: GenieAnswerCsvRequest): Promise<GenieExportOutcome> {
  return import('./GenieAnswer.export').then(
    (module) => module.exportGenieAnswerCsv(request, { post: postGenieExportReceipt }),
    (): GenieExportOutcome => ({ kind: 'refused', message: GENIE_EXPORT_NOT_RECORDED }),
  );
}

/**
 * The actions under every Genie rows block (audit 2026-09-21 `genie-06`,
 * slice 2): "Show all" replaces the capped compact table in place with every
 * row and column the answer holds, and "Download CSV" hands over those rows
 * once the audit ledger has recorded the export. `.genie-answer__rows-actions`
 * is a documented BEM extension of `.genie-answer` (the prototype's Genie
 * bubble, design_files/index.html:741-760, has no row controls).
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

/**
 * "Download CSV" for one rows block. Offered only on a trusted answer with a
 * live conversation and message id (the caller decides and passes `target`).
 * One click is one ledger attempt: a ref latch and the disabled "Recording
 * export…" state hold a second POST. The file downloads only after the
 * receipt answers; the outcome is shown here and spoken through the
 * surface's one announcer. Never prefetched, never fired on hover.
 */
export function GenieRowsCsvDownload({
  rows,
  columns,
  target,
  reportedRowCount,
  onAnnounce,
}: {
  rows: ReadonlyArray<Record<string, unknown>>;
  columns: readonly string[];
  target: GenieRowsExportTarget;
  reportedRowCount: number | null;
  onAnnounce?: (text: string) => void;
}) {
  const latchRef = useRef(false);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const reasonId = useId();
  const tooMany = rows.length > GENIE_EXPORT_MAX_ROWS;
  const download = () => {
    if (latchRef.current || tooMany) return;
    latchRef.current = true;
    setRecording(true);
    setStatus(null);
    void runGenieExport({ rows, columns, target, reportedRowCount }).then((outcome) => {
      latchRef.current = false;
      setRecording(false);
      setStatus(outcome.message);
      onAnnounce?.(outcome.message);
    });
  };
  return (
    <>
      <button
        type="button"
        className="btn btn--ghost btn--sm genie-answer__download"
        onClick={download}
        disabled={recording || tooMany}
        aria-describedby={tooMany ? reasonId : undefined}
      >
        <Icon name="export" size={12} />
        {recording ? 'Recording export…' : 'Download CSV'}
      </button>
      {tooMany && (
        <span id={reasonId} className="genie-answer__rows-status">
          Download holds at most {formatCount(GENIE_EXPORT_MAX_ROWS)} rows; this answer has {formatCount(rows.length)}.
        </span>
      )}
      {/* Visible outcome only, not a live region: the surface's one announcer
          speaks it (a11y-06). */}
      {status && (
        <span className="genie-answer__rows-status" data-export-status="">
          {status}
        </span>
      )}
    </>
  );
}
