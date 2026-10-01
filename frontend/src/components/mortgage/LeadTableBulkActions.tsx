/**
 * LeadTableBulkActions — the sticky `.bulk-actions` toolbar under the ranked
 * borrower table (selection label, shared approval rationale, assignee
 * select, assign / distribute / approve controls) and the `.bulk-actions`
 * result toast. Every decision stays in useLeadApprovalActions /
 * useLeadSalesActions; the one state here is the gate's shared rationale
 * (audit runtime-04 slice 2), so typing it re-renders this toolbar, never
 * the table. It is cleared once a run settles. Extracted from LeadTable.tsx
 * (file-size gate, plan item 2); markup and class names are unchanged.
 *
 * Arming (audit flow-03 / states-06, D-approval-flow-a1;
 * deviation:bulk-approve-arming): while the gate is open, Approve stays
 * aria-disabled (never native `disabled`, which would drop focus) until the
 * shared rationale is written and every offer in the run has a previewed
 * sample. A status line says what is missing, and activating Approve then
 * moves focus to the first missing piece instead of submitting.
 *
 * Bulk Reject (audit tables-07, D-approval-flow-d; deviation:bulk-reject-gate):
 * "Reject N" sits before Approve, as in design_files/Module 0
 * Prototype.html:1501-1502. It opens the reject gate (one eligible row: that
 * row's reject panel); the gate itself (reason, shared note, counts) rides
 * the lazy bulk chunk and mounts in the `rejectGate` slot.
 *
 * Scope (audit tables-07 / tables-02, D-approval-flow-a3;
 * deviation:bulk-scope-handoff): there is no "select all N matching". When
 * every loaded row is selected and more borrowers match, a full-width
 * `.bulk-actions__scope` line says the bulk actions apply only to the rows
 * shown, and links to Portfolio Builder with the queue's filters (the link
 * preloads that route's code only; it reads nothing). The link rides the
 * lazy bulk chunk (LeadBulkCampaignHandoff, the `campaignHandoff` slot); the
 * sentence stays here, so it shows even if that chunk cannot load.
 */

import { useId, useState, type ReactNode, type RefObject } from 'react';
import type { SalesTeamMember } from '../../types';
import { formatCount } from '../../lib/formatters';
import { Button } from '../Primitives';
import type { BulkToast } from './useLeadApprovalActions';
import { bulkRunVerb, type BulkRunKind } from './useLeadBulkRun';
import { APPROVER_ROLE_STATUS_ID, describedBy } from './approverGate';

interface LeadTableBulkActionsProps {
  selectionCount: number;
  selectedApprovalEligibleCount: number;
  bulkApproving: boolean;
  bulkRationaleOpen: boolean;
  campaignBindingBlocked: boolean;
  /** Non-null = the actor may not approve; the text is the accessible reason. */
  approverGate?: string | null;
  salesTeam: SalesTeamMember[];
  salesBusy: boolean;
  selectedAssignee: string;
  onSelectedAssigneeChange: (email: string) => void;
  onAssign: (mode: 'selected-lo' | 'round-robin') => void;
  onClearSelection: () => void;
  /** Approve with the gate's shared rationale; resolves true once a run settled. */
  onBulkApprove: (rationale: string) => Promise<boolean>;
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
  /** Every offer in the run has a previewed sample (the gate's coverage). */
  samplesCoverAllOffers?: boolean;
  /** The lazy bulk chunk failed to load: bulk decisions fail closed. */
  bulkChunkFailed?: boolean;
  /** Why the last run stopped at its first row ("Nothing else was sent: ..."; bulk chunk copy). */
  runNotice?: string | null;
  /** The bulk reject gate is open (at most one gate is). */
  bulkRejectOpen?: boolean;
  /** "Reject N": opens the reject gate, or one eligible row's reject panel. */
  onOpenBulkReject?: () => void;
  bulkRejectBtnRef?: RefObject<HTMLButtonElement | null>;
  /** The reject gate (LeadBulkRejectGate, lazy), shown while it is open. */
  rejectGate?: ReactNode;
  /** Every loaded selectable row is selected (the header checkbox is checked). */
  allLoadedSelected?: boolean;
  /** Rows loaded on screen, and how many borrowers match the filters. */
  loadedCount?: number;
  totalMatching?: number | null;
  /** The campaign handoff link (LeadBulkCampaignHandoff, lazy), on the scope line. */
  campaignHandoff?: ReactNode;
}

const PREVIEW_SAMPLES_SELECTOR = '[data-testid="lead-bulk-preview-samples"]';

export function LeadTableBulkActions({
  selectionCount,
  selectedApprovalEligibleCount,
  bulkApproving,
  bulkRationaleOpen,
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
  samplesCoverAllOffers = false,
  bulkChunkFailed = false,
  runNotice = null,
  bulkRejectOpen = false,
  onOpenBulkReject,
  bulkRejectBtnRef,
  rejectGate = null,
  allLoadedSelected = false,
  loadedCount = 0,
  totalMatching = null,
  campaignHandoff = null,
}: LeadTableBulkActionsProps) {
  const armingId = useId();
  const [bulkRationale, setBulkRationale] = useState('');
  const gateOpen = selectionCount > 1 && bulkRationaleOpen;
  const rejectGateOpen = selectionCount > 1 && bulkRejectOpen;
  const running = runKind !== null;
  const rationaleMissing = bulkRationale.trim().length === 0;
  const armingCopy = !gateOpen
    ? null
    : rationaleMissing && !samplesCoverAllOffers
      ? 'Write a shared rationale and preview one draft per offer before approving.'
      : rationaleMissing
        ? 'Write a shared rationale before approving.'
        : !samplesCoverAllOffers
          ? 'Preview one draft per offer before approving.'
          : null;
  // A failed bulk chunk leaves no gate to review in: bulk decisions fail closed.
  const bulkBlocked = bulkChunkFailed && selectedApprovalEligibleCount > 1;
  const approve = (event: { currentTarget: HTMLElement }) => {
    if (bulkBlocked) return;
    if (armingCopy !== null) {
      // Unarmed: take the reader to the first missing piece; never submit.
      const toolbar = event.currentTarget.closest('[data-testid="lead-bulk-actions"]');
      const target = rationaleMissing
        ? bulkRationaleRef?.current ?? null
        : toolbar?.querySelector<HTMLElement>(PREVIEW_SAMPLES_SELECTOR) ?? null;
      target?.focus();
      return;
    }
    void onBulkApprove(bulkRationale).then((settled) => {
      if (settled) setBulkRationale('');
    });
  };
  return (
    <div
      role="toolbar"
      aria-label="Bulk actions"
      data-testid="lead-bulk-actions"
      className={[
        'bulk-actions',
        gateOpen || rejectGateOpen ? 'bulk-actions--gate' : '',
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
            onChange={(e) => setBulkRationale(e.target.value)}
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
          ref={bulkRejectBtnRef}
          variant="ghost"
          size="sm"
          onClick={() => {
            if (!bulkBlocked) onOpenBulkReject?.();
          }}
          disabled={
            approverGate !== null || campaignBindingBlocked || bulkApproving
            || selectedApprovalEligibleCount === 0
          }
          aria-disabled={bulkBlocked || undefined}
          aria-describedby={describedBy(
            approverGate !== null && APPROVER_ROLE_STATUS_ID,
            campaignBindingBlocked && 'campaign-binding-status',
          )}
          data-testid="lead-bulk-reject"
          aria-label={`Reject ${selectedApprovalEligibleCount} eligible leads`}
          aria-keyshortcuts={approverGate === null && shortcutsLive ? 'Shift+R' : undefined}
        >
          {bulkApproving && runKind === 'reject'
            ? `${bulkRunVerb('reject')}…`
            : `Reject ${selectedApprovalEligibleCount}`}
        </Button>
        <Button
          ref={bulkApproveBtnRef}
          variant="primary"
          size="sm"
          icon={bulkApproving ? undefined : 'check'}
          onClick={approve}
          disabled={
            approverGate !== null || campaignBindingBlocked || bulkApproving
            || selectedApprovalEligibleCount === 0
          }
          aria-disabled={armingCopy !== null || bulkBlocked || undefined}
          aria-describedby={describedBy(
            approverGate !== null && APPROVER_ROLE_STATUS_ID,
            campaignBindingBlocked && 'campaign-binding-status',
            armingCopy !== null && armingId,
          )}
          title={approverGate ?? undefined}
          data-testid="lead-bulk-approve"
          aria-label={`Approve ${selectedApprovalEligibleCount} eligible leads`}
          aria-keyshortcuts={approverGate === null && shortcutsLive ? 'Shift+A' : undefined}
        >
          {bulkApproving && runKind === 'approve'
            ? `${bulkRunVerb('approve')}…`
            : `Approve ${selectedApprovalEligibleCount} eligible`}
        </Button>
      </div>
      {armingCopy !== null && (
        <span id={armingId} className="muted fs-12" data-testid="lead-bulk-arming">{armingCopy}</span>
      )}
      {runNotice && (
        <span role="alert" className="text-danger fs-12" data-testid="lead-bulk-canary">{runNotice}</span>
      )}
      {bulkBlocked && (
        <span role="alert" className="text-danger fs-12" data-testid="lead-bulk-chunk-failed">
          The bulk review could not load, so nothing can be approved or rejected in bulk. Reload the page.
        </span>
      )}
      {allLoadedSelected && totalMatching !== null && totalMatching > loadedCount && (
        <p className="bulk-actions__scope" data-testid="lead-bulk-scope">
          <span>
            All {formatCount(selectionCount)} selectable borrowers shown here are selected.{' '}
            {formatCount(totalMatching)} match these filters; bulk actions apply only to borrowers shown here.
          </span>
          {campaignHandoff}
        </p>
      )}
      {gateOpen && gateReview}
      {rejectGateOpen && rejectGate}
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
        {toast.ok} {toast.kind === 'reject' ? 'rejected' : 'approved'}
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
