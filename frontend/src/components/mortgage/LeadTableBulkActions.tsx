/**
 * LeadTableBulkActions — the sticky `.bulk-actions` toolbar under the ranked
 * borrower table (selection label, shared approval rationale, assignee
 * select, assign / distribute / approve controls) and the `.bulk-actions`
 * result toast. Presentation only: every decision stays in
 * useLeadApprovalActions / useLeadSalesActions. Extracted from LeadTable.tsx
 * (file-size gate, plan item 2); markup and class names are unchanged.
 */

import type { ReactNode, RefObject } from 'react';
import type { SalesTeamMember } from '../../types';
import { Button } from '../Primitives';
import type { BulkToast } from './useLeadApprovalActions';
import type { BulkRunKind } from './useLeadBulkRun';
import { APPROVER_ROLE_STATUS_ID, describedBy } from './approverGate';

interface LeadTableBulkActionsProps {
  selectionCount: number;
  selectedApprovalEligibleCount: number;
  bulkApproving: boolean;
  bulkRationaleOpen: boolean;
  bulkRationale: string;
  onBulkRationaleChange: (value: string) => void;
  campaignBindingBlocked: boolean;
  /** Non-null = the actor may not approve; the text is the accessible reason. */
  approverGate?: string | null;
  salesTeam: SalesTeamMember[];
  salesBusy: boolean;
  selectedAssignee: string;
  onSelectedAssigneeChange: (email: string) => void;
  onAssign: (mode: 'selected-lo' | 'round-robin') => void;
  onClearSelection: () => void;
  onBulkApprove: () => void;
  bulkApproveBtnRef: RefObject<HTMLButtonElement | null>;
  /** The shared-rationale field (Shift+A and the Cmd-K verb focus it). */
  bulkRationaleRef?: RefObject<HTMLInputElement | null>;
  /** The gate's review block (count by offer, sample drafts), under the rationale. */
  gateReview?: ReactNode;
  /** Sample drafts are on screen: the toolbar stops being sticky. */
  samplesShown?: boolean;
  /** The assignee select (the Cmd-K "Assign selected…" verb focuses it). */
  assigneeRef?: RefObject<HTMLSelectElement | null>;
  /** Single-key shortcuts are on: advertise Shift+A. */
  shortcutsLive?: boolean;
  /** The bulk run on the wire, if any (audit tables-07). */
  runKind?: BulkRunKind | null;
  /** The run's progress line (LeadBulkRunProgress, lazy), shown full width while it runs. */
  runStatus?: ReactNode;
}

export function LeadTableBulkActions({
  selectionCount,
  selectedApprovalEligibleCount,
  bulkApproving,
  bulkRationaleOpen,
  bulkRationale,
  onBulkRationaleChange,
  campaignBindingBlocked,
  approverGate = null,
  salesTeam,
  salesBusy,
  selectedAssignee,
  onSelectedAssigneeChange,
  onAssign,
  onClearSelection,
  onBulkApprove,
  bulkApproveBtnRef,
  bulkRationaleRef,
  gateReview = null,
  samplesShown = false,
  assigneeRef,
  shortcutsLive = true,
  runKind = null,
  runStatus = null,
}: LeadTableBulkActionsProps) {
  const gateOpen = selectionCount > 1 && bulkRationaleOpen;
  const running = runKind !== null;
  return (
    <div
      role="toolbar"
      aria-label="Bulk actions"
      data-testid="lead-bulk-actions"
      className={[
        'bulk-actions',
        gateOpen ? 'bulk-actions--gate' : '',
        gateOpen && samplesShown ? 'bulk-actions--samples' : '',
        running ? 'bulk-actions--running' : '',
      ].filter(Boolean).join(' ')}
    >
      <div className="bulk-actions__label">
        <span className="mono num">{selectionCount}</span> {selectionCount === 1 ? 'lead' : 'leads'} selected
      </div>
      {gateOpen && (
        <label className="bulk-actions__rationale">
          <span className="field__label">Shared approval rationale</span>
          <input
            ref={bulkRationaleRef}
            value={bulkRationale}
            onChange={(e) => onBulkRationaleChange(e.target.value)}
            maxLength={500}
            placeholder="Example: Q3 retention sweep, all reviewed against current rules."
          />
        </label>
      )}
      <div className="bulk-actions__controls">
        {salesTeam.length > 0 && (
          <>
            <label className="bulk-actions__assignee">
              <span className="field__label">Assign to</span>
              <select
                ref={assigneeRef}
                value={selectedAssignee}
                onChange={(e) => onSelectedAssigneeChange(e.target.value)}
                disabled={salesBusy}
                aria-label="Loan officer assignment target"
              >
                {salesTeam.map((member) => (
                  <option key={member.email} value={member.email}>
                    {member.display_label}
                  </option>
                ))}
              </select>
            </label>
            <Button
              variant="default"
              size="sm"
              icon="user"
              onClick={() => onAssign('selected-lo')}
              disabled={salesBusy || !selectedAssignee || selectionCount === 0}
              aria-label={`Assign ${selectionCount} selected leads to selected loan officer`}
            >
              {salesBusy ? 'Assigning…' : 'Assign'}
            </Button>
            <Button
              variant="default"
              size="sm"
              icon="layers"
              onClick={() => onAssign('round-robin')}
              disabled={salesBusy || salesTeam.length === 0 || selectionCount === 0}
              aria-label={`Distribute ${selectionCount} selected leads across active loan officers`}
            >
              Distribute
            </Button>
          </>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={onClearSelection}
          disabled={bulkApproving}
          data-testid="lead-bulk-clear"
        >
          Clear selection
        </Button>
        <Button
          ref={bulkApproveBtnRef}
          variant="primary"
          size="sm"
          icon={bulkApproving ? undefined : 'check'}
          onClick={onBulkApprove}
          disabled={
            approverGate !== null || campaignBindingBlocked || bulkApproving
            || selectedApprovalEligibleCount === 0
          }
          aria-describedby={describedBy(
            approverGate !== null && APPROVER_ROLE_STATUS_ID,
            campaignBindingBlocked && 'campaign-binding-status',
          )}
          title={approverGate ?? undefined}
          data-testid="lead-bulk-approve"
          aria-label={`Approve ${selectedApprovalEligibleCount} eligible leads`}
          aria-keyshortcuts={approverGate === null && shortcutsLive ? 'Shift+A' : undefined}
        >
          {bulkApproving ? 'Approving…' : `Approve ${selectedApprovalEligibleCount} eligible`}
        </Button>
      </div>
      {gateOpen && gateReview}
      {running && runStatus}
    </div>
  );
}

interface LeadTableBulkToastProps {
  toast: BulkToast;
  onReviewRecentActivity: () => void;
}

/**
 * Compact result banner for the last bulk run. `aborted` and `network` rows
 * are called out separately from plain failures: aborted rows may or may not
 * have committed server-side, so the copy sends the operator to Recent
 * activity instead of offering a blind retry.
 */
export function LeadTableBulkToast({ toast, onReviewRecentActivity }: LeadTableBulkToastProps) {
  return (
    <div className="bulk-actions" data-testid="lead-bulk-toast">
      <span
        role="status"
        aria-live="polite"
        className={`bulk-actions__toast ${
          toast.aborted > 0 || toast.network > 0
            ? 'bulk-actions__toast--danger'
            : toast.fail > 0
              ? 'bulk-actions__toast--warn'
              : 'bulk-actions__toast--ok'
        }`}
      >
        {toast.ok} approved
        {toast.fail > 0 ? `, ${toast.fail} failed` : ''}
        {toast.network > 0
          ? ` (${toast.network} network dropped; retry)`
          : ''}
        {toast.aborted > 0
          ? ` · ${toast.aborted} cancelled in flight. Confirm the outcome in Recent activity.`
          : ''}
      </span>
      {toast.aborted > 0 && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onReviewRecentActivity}
        >
          Review recent activity
        </button>
      )}
    </div>
  );
}
