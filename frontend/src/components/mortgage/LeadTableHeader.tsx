import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { Icon } from '../Icon';
import { Button, SurfaceTitle } from '../Primitives';
import { auditEventHref } from '../../lib/auditLinks';
import { formatCount } from '../../lib/formatters';
import { LeadTableKeyboardHint, LeadTableShortcutsButton } from './LeadTableKeyboardHint';
import { LeadTableViewControl } from './LeadTableViewControl';
import type { LeadTableView } from './LeadTable.columns';
import type { LeadCsvExportState } from './useLeadCsvExport';

export interface LeadTableHeaderProps {
  headerStatus?: ReactNode;
  singleKeysOn: boolean;
  /** The actor may approve (no approver gate). */
  approverActive: boolean;
  actorEmail: string | null | undefined;
  view: LeadTableView;
  onViewChange?: (view: LeadTableView) => void;
  exportState: LeadCsvExportState;
  canAccessAdmin: boolean;
  csvExportCount: number;
  csvExportNoun: string;
  /** Rows in scope the marketing-eligibility gate excluded from the file. */
  csvExportExcluded: number;
  exportBlockedReason: string | null;
  onExport: () => void;
}

/**
 * The ranked-borrower table's `.surface__hdr` (title, keyboard line, view
 * and export controls). Moved verbatim out of LeadTable.tsx (audit tables-05
 * step 0: the shell's header and footer are their own modules); only the
 * values it reads became props.
 */
export function LeadTableHeader({
  headerStatus,
  singleKeysOn,
  approverActive,
  actorEmail,
  view,
  onViewChange,
  exportState,
  canAccessAdmin,
  csvExportCount,
  csvExportNoun,
  csvExportExcluded,
  exportBlockedReason,
  onExport,
}: LeadTableHeaderProps) {
  // The shell's compile posture (audit runtime-04, cut 5): compiled, this
  // module's memo caches would ride the LeadTable chunk and buy nothing
  // while its caller stays uncompiled.
  'use no memo';

  const exporting = exportState.status === 'pending';
  return (
    <div className="surface__hdr surface__hdr--split">
      <div className="surface__hdr-main">
        <div className="surface__icon">
          <Icon name="user" size={14} />
        </div>
        <div>
          {/* The view's freshness sits beside the title, not in the action
              row: there it squeezed the keyboard hint onto a second line
              and pushed the 480px scroller past the fold at 1440x900. */}
          {headerStatus ? (
            <div className="inline-flex">
              <SurfaceTitle>Ranked borrowers</SurfaceTitle>
              {headerStatus}
            </div>
          ) : (
            <SurfaceTitle>Ranked borrowers</SurfaceTitle>
          )}
          <div className="muted fs-12">
            {/* Keycaps are `<kbd>` (prototype-parity P2); the header's
                "Keyboard shortcuts" button and `?` list every key. */}
            <LeadTableKeyboardHint singleKeysOn={singleKeysOn} approverActive={approverActive} />
            {approverActive && actorEmail && (
              <> Approving as <span className="mono" data-testid="lead-approving-as">{actorEmail}</span>.</>
            )}
          </div>
        </div>
      </div>
      <div className="lead-table__header-actions">
        <LeadTableShortcutsButton singleKeysOn={singleKeysOn} />
        {onViewChange && <LeadTableViewControl view={view} onChange={onViewChange} />}
        {exportState.status === 'done' && (
          <span className="muted fs-12" data-testid="lead-export-receipt">
            Exported {formatCount(exportState.rowCount)} {exportState.rowCount === 1 ? 'row' : 'rows'}
            {' · audit '}
            {canAccessAdmin ? (
              <Link className="mono" to={auditEventHref(exportState.receipt.audit_event_id)}>
                {exportState.receipt.audit_event_id}
              </Link>
            ) : (
              <span className="mono">{exportState.receipt.audit_event_id}</span>
            )}
          </span>
        )}
        <Button
          size="sm"
          icon={exporting ? undefined : 'export'}
          onClick={onExport}
          // Pending and blocked are aria-disabled, never native `disabled`:
          // a focused button that turns disabled drops keyboard focus to
          // <body>. useLeadCsvExport ignores the click in both states.
          disabled={csvExportCount === 0}
          aria-disabled={exporting || exportBlockedReason !== null || undefined}
          aria-busy={exporting || undefined}
          data-testid="lead-export"
          aria-label={exporting
            ? 'Recording the export in the audit ledger'
            : `Export ${formatCount(csvExportCount)} ${csvExportNoun} as CSV`}
          title={exportBlockedReason ?? (csvExportCount === 0 && csvExportExcluded > 0
            ? 'Every row in scope is excluded by the marketing-eligibility gate'
            : undefined)}
        >
          {exporting
            ? 'Recording export…'
            : `Export ${formatCount(csvExportCount)} ${csvExportNoun}`}
        </Button>
      </div>
    </div>
  );
}
