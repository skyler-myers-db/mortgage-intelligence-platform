/**
 * useLeadTableKeyboardFlow — the ranked-borrower table's triage flow (wave
 * 1c, audit tables-03 / wow-power-4 / flow-03 / states-06), composed from:
 *
 *   - the active-row cursor (useLeadTableCursor),
 *   - the approve review (useLeadApproveReview): the first Approve or A
 *     opens it, Confirm approves that exact draft,
 *   - the result toast with "View receipt",
 *   - the table's keymap bindings (LeadTable.keymap + useLeadTableHotkeys),
 *   - the selection context the Cmd-K verbs read (commandSelection).
 *
 * Every decision still goes through useLeadApprovalActions' guarded
 * handlers; this hook only decides WHICH row and WHEN.
 */
import { useEffect, useRef, useState, type RefObject } from 'react';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { publishCommandSelection, type CommandVerb } from '../command/commandSelection';
import { isLeadApprovalEligible, isLeadSelectableForSalesOps, isTerminalApproval } from './LeadTable.logic';
import { isInsideLeadApproveReview, leadApproveReviewId } from './LeadApproveReview.ids';
import { leadReceiptAnchorId } from './LeadRowPreview';
import type { LeadDecisionToastState } from './LeadTableDecisionToast';
import type { useLeadApprovalActions } from './useLeadApprovalActions';
import { useLeadApproveReview } from './useLeadApproveReview';
import { useLeadTableCursor } from './useLeadTableCursor';
import { useLeadTableHotkeys } from './useLeadTableHotkeys';

type ApprovalActions = ReturnType<typeof useLeadApprovalActions>;

export interface UseLeadTableKeyboardFlowInput {
  sortedLeads: readonly LeadSummary[];
  leadsById: Map<string, LeadSummary>;
  approvals: Record<string, 'approved' | 'rejected'>;
  expanded: string | null;
  setExpanded: (borrowerId: string | null) => void;
  approval: ApprovalActions;
  approverGate: string | null;
  campaignBindingBlocked: boolean;
  /** An assignment target exists and nothing is in flight. */
  canAssign: boolean;
  assigneeRef: RefObject<HTMLSelectElement | null>;
  tableWrapRef: RefObject<HTMLDivElement | null>;
  virtualized: boolean;
  scrollToIndex: (index: number) => void;
}

/** Focus an element once it is rendered (a virtualized row may need a frame or two). */
function focusWhenRendered(id: string, attempts = 12) {
  requestAnimationFrame(() => {
    const element = document.getElementById(id);
    if (element) {
      element.focus();
      element.scrollIntoView?.({ block: 'nearest' });
      return;
    }
    if (attempts > 0) focusWhenRendered(id, attempts - 1);
  });
}

export function useLeadTableKeyboardFlow({
  sortedLeads,
  leadsById,
  approvals,
  expanded,
  setExpanded,
  approval,
  approverGate,
  campaignBindingBlocked,
  canAssign,
  assigneeRef,
  tableWrapRef,
  virtualized,
  scrollToIndex,
}: UseLeadTableKeyboardFlowInput) {
  'use no memo';

  const effectiveStatus = (lead: LeadSummary) => approvals[lead.borrower_id] ?? lead.approval_status;
  const isPending = (lead: LeadSummary) => isLeadApprovalEligible(
    lead.approval_status, approvals[lead.borrower_id], lead,
  );
  /**
   * May this row still be approved from a review? Eligible on this render's
   * approvals AND not locked by a decision the render has not seen yet (a
   * reject or approve that just returned, a bulk run on the wire).
   */
  const canStillApprove = (borrowerId: string) => {
    const lead = leadsById.get(borrowerId);
    return isLeadApprovalEligible(lead?.approval_status, approvals[borrowerId], lead)
      && !approval.isDecisionLocked(borrowerId);
  };
  const cursor = useLeadTableCursor({
    sortedLeads,
    tableWrapRef,
    virtualized,
    scrollToIndex,
    statusOf: (lead) => effectiveStatus(lead) ?? 'pending',
    isPending,
  });
  const [toast, setToast] = useState<LeadDecisionToastState | null>(null);
  const [refocusTable, setRefocusTable] = useState(false);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const review = useLeadApproveReview({
    canStartApproval: approval.canStartApproval,
    draftForApproval: approval.draftForApproval,
    approveLead: approval.approveLead,
    isEligible: canStillApprove,
    onApproved: (borrowerId) => {
      setToast({ borrowerId });
      cursor.advanceAfter(borrowerId);
      setRefocusTable(true);
    },
  });
  // The review's Confirm unmounts with it (and a modal dialog keeps the page
  // inert until it closes). After that commit, keep the keyboard in the
  // table so J / K / A carry on from the advanced cursor; a focus the reader
  // already moved elsewhere on the page is left alone.
  useEffect(() => {
    if (!refocusTable) return;
    setRefocusTable(false);
    const region = tableWrapRef.current;
    const active = document.activeElement;
    if (!region || active === region) return;
    if (active === null || active === document.body || region.contains(active)) {
      region.focus({ preventScroll: true });
    }
  }, [refocusTable, tableWrapRef]);
  const current = review.review;

  // flow-03: a review whose row was decided another way while it was open
  // (the row's Reject panel, a bulk run that includes it, a decision from
  // elsewhere in the app) closes as soon as this table sees it, so there is
  // no Confirm left to approve a rejected or already-approved borrower.
  // Runs after every commit (a cheap lookup); an approval on the wire is
  // left alone (`cancel` refuses it) and settles through `confirm`.
  useEffect(() => {
    if (!current || current.phase === 'submitting' || canStillApprove(current.borrowerId)) return;
    const hadFocus = isInsideLeadApproveReview(document.activeElement, current.borrowerId);
    if (review.cancel() && hadFocus && current.mode === 'inline') {
      tableWrapRef.current?.focus({ preventScroll: true });
    }
  });

  function eligibleSelectedIds(): string[] {
    return approval.approvalEligibleIds.filter((id) => approval.selectedIds.has(id));
  }

  /** The first Approve (click or A): open the review; never approve here. */
  function openReview(borrowerId: string) {
    // Never start a review (or its draft) while a bulk run is on the wire.
    if (approval.bulkApproving || approval.isBulkRunInFlight()) return;
    if (!canStillApprove(borrowerId)) return;
    cursor.setCursorId(borrowerId);
    // review.open runs the approver / campaign-binding gate before it drafts.
    const result = review.open(borrowerId, expanded === borrowerId ? 'inline' : 'dialog');
    if (result === 'already-open') {
      document.getElementById(leadApproveReviewId(borrowerId))?.scrollIntoView?.({ block: 'nearest' });
      confirmRef.current?.focus();
    }
  }

  function cancelReview() {
    const mode = current?.mode;
    if (!review.cancel()) return;
    // The dialog's focus trap restores focus itself; an inline review's
    // controls unmount, so hand focus back to the table.
    if (mode === 'inline') tableWrapRef.current?.focus({ preventScroll: true });
  }

  /** Expand / collapse a row; collapsing abandons its inline review. */
  function toggleRow(lead: LeadSummary, isOpen: boolean) {
    if (isOpen && current?.borrowerId === lead.borrower_id && current.mode === 'inline') {
      if (!review.cancel()) return;
    }
    cursor.setCursorId(lead.borrower_id);
    setExpanded(isOpen ? null : lead.borrower_id);
  }

  function viewReceipt(borrowerId: string) {
    setExpanded(borrowerId);
    cursor.moveTo(borrowerId);
    focusWhenRendered(leadReceiptAnchorId(borrowerId));
  }

  /** Toolbar "Approve N eligible": one row opens its review, several run the gated bulk. */
  function bulkApproveFromToolbar(sampleDrafts: ReadonlyMap<string, OutreachDraftResult>) {
    const ids = eligibleSelectedIds();
    if (ids.length === 1) {
      openReview(ids[0]);
      return;
    }
    void approval.bulkApprove(sampleDrafts);
  }

  /** Shift+A and the Cmd-K verb: open the SAME gate; never submit. */
  function openBulkGate() {
    if (approval.bulkApproving || approval.isBulkRunInFlight()) return;
    const ids = eligibleSelectedIds();
    if (ids.length === 0) return;
    // A gate that cannot approve is not opened: the approver / campaign
    // binding check says why in the table's alert instead (states-06).
    if (!approval.canStartApproval()) return;
    if (ids.length === 1) {
      openReview(ids[0]);
      return;
    }
    approval.openBulkRationale();
  }

  async function submitReject() {
    const rejected = await approval.submitReject();
    if (rejected) cursor.advanceAfter(rejected);
  }

  const targetId = cursor.cursorId ?? expanded;
  useLeadTableHotkeys({
    approverActive: approverGate === null,
    move: cursor.move,
    toggleCursorRow: () => {
      const lead = cursor.cursorId ? leadsById.get(cursor.cursorId) : undefined;
      if (!lead) return false;
      toggleRow(lead, expanded === lead.borrower_id);
      return true;
    },
    toggleSelectCursorRow: () => {
      const lead = targetId ? leadsById.get(targetId) : undefined;
      if (!lead || approval.bulkApproving) return;
      if (!isLeadSelectableForSalesOps(lead.approval_status, approvals[lead.borrower_id], lead)) return;
      approval.toggleSelect(lead.borrower_id);
    },
    reviewCursorRow: () => {
      if (targetId) openReview(targetId);
    },
    rejectCursorRow: () => {
      if (!targetId || campaignBindingBlocked) return;
      const lead = leadsById.get(targetId);
      if (isTerminalApproval(approvals[targetId] ?? lead?.approval_status)) return;
      approval.setPendingReject(targetId);
    },
    openBulkGate,
  }, tableWrapRef);

  /**
   * Draft ready: may Confirm take focus (so Enter approves)? Not when the
   * reader has moved on: the cursor left the row, or focus sits outside the
   * table, the review and its dialog. The review asks when its draft lands.
   */
  function shouldConfirmTakeFocus(borrowerId: string): boolean {
    const active = document.activeElement;
    const inTable = active !== null && tableWrapRef.current?.contains(active) === true;
    const inDialog = active instanceof HTMLDialogElement || active?.closest('dialog') != null;
    const idle = active === null || active === document.body;
    if (!(inTable || inDialog || idle || isInsideLeadApproveReview(active, borrowerId))) return false;
    return cursor.cursorId === null || cursor.cursorId === borrowerId;
  }

  // Cmd-K verbs: publish what the selection can do, with this table's own
  // guarded handlers. `run` reads the latest handlers through a ref.
  const verbRunRef = useRef<(verb: CommandVerb) => void>(() => undefined);
  useEffect(() => {
    verbRunRef.current = (verb) => {
      if (verb === 'approve-selected') openBulkGate();
      else requestAnimationFrame(() => assigneeRef.current?.focus());
    };
  });
  const selectedCount = approval.selectionCount;
  const approveCount = approval.selectedApprovalEligibleCount;
  const canApproveVerb = approverGate === null && !campaignBindingBlocked && !approval.bulkApproving;
  useEffect(() => {
    if (selectedCount === 0) return undefined;
    return publishCommandSelection({
      selectedCount,
      approveCount,
      canApprove: canApproveVerb,
      canAssign: canAssign && selectedCount > 0,
      run: (verb) => verbRunRef.current(verb),
    });
  }, [selectedCount, approveCount, canApproveVerb, canAssign]);

  return {
    cursor,
    review,
    confirmRef,
    shouldConfirmTakeFocus,
    toast,
    dismissToast: () => setToast(null),
    openReview,
    cancelReview,
    toggleRow,
    viewReceipt,
    bulkApproveFromToolbar,
    submitReject,
    eligibleSelectedIds,
  };
}
