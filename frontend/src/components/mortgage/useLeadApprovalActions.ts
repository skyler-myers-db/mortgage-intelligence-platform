/**
 * useLeadApprovalActions — the human-approval half of the ranked-borrower
 * table: single-row approve/reject, which row's reject panel is open, the
 * selection set feeding the bulk toolbar, the bulk runs' state and context
 * (the runs themselves ride the lazy bulk chunk: leadBulkDecisions), the
 * cut-short run's one shell toast on remount, and the post-bulk focus
 * restore. Extracted from LeadTable.tsx (file-size gate, plan item 2).
 *
 * The writes run on the governed outreach mutations (lib/mutations/outreach,
 * audit stack-09 / tables-05): pessimistic (a row reads Approved only after
 * the POST returned approved=true), networkMode 'always', one request_id per
 * intent, and the pending state read from the MutationCache so a table that
 * remounted mid-write still knows the row is on the wire.
 */

import { useEffect, useEffectEvent, useRef, useState, type RefObject } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import {
  decisionFailure,
  draftForApproval as draftGovernedCopy,
  invalidateAfterDecision,
  isDecisionPending,
  useApproveLead,
  usePendingDecisions,
  useRejectLead,
} from '../../lib/mutations/outreach';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import {
  bulkActionFocusTarget,
  isLeadApprovalEligible,
  isLeadSelectableForSalesOps,
  type CampaignBinding,
} from './LeadTable.logic';
import type { RejectReasonCode } from './LeadTable.types';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { APPROVER_ROLE_REQUIRED } from './approverGate';
import { clearCancelledBulk, readCancelledBulk, type CancelledBulkApprove } from './bulkApproveStash';
import { toast } from '../../lib/toast';
import { useLeadBulkRun, type BulkRunCanary, type BulkRunResult } from './useLeadBulkRun';
import {
  approveWithReport as writeApprove,
  DECIDED_SINCE,
  requestScopeParts,
  type ApproveExtras,
  type DecisionWriteDeps,
} from './leadDecisionWrites';
import type { BulkDecisionContext, runBulkApprove, runBulkReject } from './leadBulkDecisions';
import { pruneTo, rangeIds } from './LeadTable.selection';
import { toastWriteFailure, toastWriteRefusal } from './leadWriteFailureToast';

/** The toast title's object when a gate refuses a selection, not one row. */
const SELECTION_SUBJECT = 'the selected leads';

/** Verification state of a `?campaign_id=&variant_name=` URL binding. */
export type CampaignBindingState = 'absent' | 'invalid' | 'verified' | 'validating';

/**
 * The R5-21 partial-run restore (states-07 item 5b): ONE shell toast for a
 * bulk run the last unmount cut short. Success when nothing failed or was
 * cut; an error with the same copy when anything failed; and when rows were
 * cut in flight (audit-ambiguous: the server may have committed them) an
 * error whose one action opens Recent activity, never a blind retry.
 */
export function raiseCancelledBulkToast(
  cancelled: CancelledBulkApprove & { fail?: number; network?: number },
  openRecentActivity: (() => void) | undefined,
): void {
  const fail = cancelled.fail ?? 0;
  const network = cancelled.network ?? 0;
  const title = `${cancelled.ok} ${cancelled.kind === 'reject' ? 'rejected' : 'approved'}${fail > 0 ? `, ${fail} failed` : ''}`;
  const detail = [
    network > 0 ? `${network} network dropped; retry.` : '',
    cancelled.aborted > 0 ? `${cancelled.aborted} cancelled in flight. Confirm the outcome in Recent activity.` : '',
  ].filter(Boolean).join(' ') || null;
  if (cancelled.aborted > 0) {
    toast.error(title, {
      detail,
      action: openRecentActivity ? { label: 'Review recent activity', onAction: openRecentActivity } : null,
    });
  } else if (fail > 0 || network > 0) {
    toast.error(title, { detail });
  } else {
    toast.success(title, { detail });
  }
}

export type LeadDecisionOutcome = 'ok' | 'network' | 'backend' | 'aborted' | 'duplicate';

/** What a bulk run needs from the lazy bulk chunk (LeadBulkApproveReview's re-exports). */
export interface BulkRunsChunk {
  runBulkApprove: typeof runBulkApprove;
  runBulkReject: typeof runBulkReject;
}

export interface UseLeadApprovalActionsInput {
  displayLeads: LeadSummary[];
  leadsById: Map<string, LeadSummary>;
  approvals: Record<string, 'approved' | 'rejected'>;
  setApproval: (borrowerId: string, state: 'approved' | 'rejected') => void;
  queryClient: QueryClient;
  campaignBinding: CampaignBinding | null;
  campaignBindingState: CampaignBindingState;
  campaignBindingBlocked: boolean;
  /**
   * The session's `can_approve`. When false every decision entry point
   * returns BEFORE the draft call: `/outreach/draft` is not approver-gated
   * and writes a DRAFT_OUTREACH audit row, so a non-approver's click used to
   * leave an audit trace and then 403 (audit flow-02).
   */
  canApprove: boolean;
  /** The always-mounted table scroll region — the post-bulk focus fallback. */
  tableWrapRef: RefObject<HTMLDivElement | null>;
  /**
   * The lazy bulk chunk's bulk runs (leadBulkDecisions), read synchronously
   * when a run starts; null until it loaded (a run then fails closed:
   * nothing is drafted or sent).
   */
  bulkRuns?: () => BulkRunsChunk | null;
  /** The paged Lead Queue view every decision declares (D-audit-reads-a); null elsewhere. */
  leadViewId?: string | null;
  /** The approval request a request-scoped queue answers (flow-02); every decision sends it. */
  approvalRequestBatchId?: string | null;
  /** The Console's Recent activity: the cut-short run toast's action. */
  openRecentActivity?: () => void;
  /** Set by the keyboard flow: raise the decision toast for an approve its 429 Retry recorded. */
  lateApprovedRef?: RefObject<((borrowerId: string) => void) | null>;
}

export function useLeadApprovalActions({
  displayLeads,
  leadsById,
  approvals,
  setApproval,
  queryClient,
  campaignBinding,
  campaignBindingState,
  campaignBindingBlocked,
  canApprove,
  tableWrapRef,
  bulkRuns,
  leadViewId = null,
  approvalRequestBatchId = null,
  openRecentActivity,
  lateApprovedRef,
}: UseLeadApprovalActionsInput) {
  // A11y: the bulk-approve button is the launch point for the bulk flow.
  // After the action settles we restore focus deterministically — to this
  // button when it survives (partial outcome keeps the toolbar mounted) or
  // to the table scroll region when a full success unmounts the toolbar.
  // Without this, keyboard focus silently drops to <body> after a bulk run.
  const bulkApproveBtnRef = useRef<HTMLButtonElement | null>(null);
  // The shared-rationale field: Shift+A and the Cmd-K verb open the bulk
  // gate and land focus here (they never submit the bulk run themselves).
  const bulkRationaleRef = useRef<HTMLInputElement | null>(null);
  // The bulk reject gate (tables-07): its trigger (focus returns there after
  // a run) and its Reason field (Shift+R and the Cmd-K verb land there).
  const bulkRejectBtnRef = useRef<HTMLButtonElement | null>(null);
  const bulkRejectReasonRef = useRef<HTMLSelectElement | null>(null);
  // Which row's reject panel is open. The panel's reason and rationale, and
  // the bulk gate's shared rationale, are the panels' own state (audit
  // runtime-04 slice 2): a keystroke re-renders the panel, never the table.
  const [pendingReject, setPendingReject] = useState<string | null>(null);
  const [bulkRationaleOpen, setBulkRationaleOpen] = useState(false);
  // At most one bulk gate is open: approve (rationale) or reject (reason).
  const [bulkRejectOpen, setBulkRejectOpen] = useState(false);
  const approveMutation = useApproveLead(queryClient);
  const rejectMutation = useRejectLead(queryClient);
  // Which rows have an approve or reject on the wire, and which decision.
  const pendingDecisions = usePendingDecisions(queryClient);
  const requestIds = useIntentRequestIds();
  // Bulk-approve state. `selectedIds` is a Set so toggling is O(1); we
  // copy-on-write when updating to keep React's reference check happy.
  const [storedSelection, setSelectedIds] = useState<Set<string>>(() => new Set());
  const currentIds = new Set(displayLeads.map((lead) => lead.borrower_id));
  // The last plain toggle: where a Shift range starts (audit tables-07).
  const selectionAnchorRef = useRef<string | null>(null);
  // The bulk run (useLeadBulkRun): its R5-04 latch, progress, cooperative
  // Stop, per-row report and the R5-21 unmount abort + stash.
  const bulkRun = useLeadBulkRun();
  const bulkApproving = bulkRun.progress !== null;
  // R5-04 synchronous latch per row: flipped before any await.
  const rowInFlightRef = useRef<Record<string, boolean>>({});
  // Wave 1c (flow-03): an approve review can stay open while its row is
  // decided another way (the row's Reject panel, a bulk run). These two
  // synchronous mirrors let the review refuse to approve such a row even
  // from a handler of the render BEFORE the decision's state committed:
  // `decidedRef` holds rows whose approve / reject write returned ok in this
  // mount, `bulkRunIdsRef` the rows of the bulk run on the wire.
  const decidedRef = useRef<Set<string>>(new Set());
  // Read by a 429 Retry long after its render (states-08 item 3): the rows
  // still eligible and the latest rejectLead, synced after every commit.
  const eligibleRef = useRef<ReadonlySet<string>>(new Set());
  const rejectLatestRef = useRef<(borrowerId: string, reasonCode: RejectReasonCode, rationale: string | null) => Promise<boolean>>(
    () => Promise.resolve(false),
  );
  const bulkRunIdsRef = useRef<ReadonlySet<string>>(new Set());
  // A run the canary stopped at its first row: the reopened gate says which
  // row was refused and that nothing else was sent. Cleared by the next run.
  // The row a run's canary stopped at (the gate's line; its copy rides the bulk chunk).
  const [bulkRunCanary, setBulkRunCanary] = useState<BulkRunCanary | null>(null);
  // wow-stage-3: the audit row each row decision wrote, keyed by borrower.
  // The expanded row reads it back as a Decision receipt; the id comes from
  // the POST response and nothing else about the receipt is kept here.
  // motion-06: the receipt's reveal plays once per decision; markRevealed
  // flips `revealed` so a collapse + re-expand renders it finished.
  const [decisionReceipts, setDecisionReceipts] = useState<Record<string, LeadDecisionReceipt>>({});
  // The same audit ids, readable synchronously (the decision toast is raised before the state commits).
  const receiptIdsRef = useRef<Map<string, string | null>>(new Map());
  const recordDecision = (borrowerId: string, receipt: LeadDecisionReceipt) => {
    receiptIdsRef.current.set(borrowerId, receipt.auditEventId);
    const markRevealed = () => setDecisionReceipts((cur) => {
      const current = cur[borrowerId];
      if (!current || current.auditEventId !== receipt.auditEventId || current.revealed) return cur;
      return { ...cur, [borrowerId]: { ...current, revealed: true } };
    });
    setDecisionReceipts((cur) => ({ ...cur, [borrowerId]: { ...receipt, revealed: false, markRevealed } }));
  };

  /**
   * The approver-role and campaign-binding gate every decision passes,
   * checked BEFORE any draft call: `/outreach/draft` writes a
   * DRAFT_OUTREACH audit row and is not approver-gated. The approve
   * review (flow-03) runs it before it drafts anything. A refusal is an
   * error toast titled for what was refused (states-07 item 2); a bulk row
   * refused mid-run reports in the run's result instead (`report` false).
   */
  function passesDecisionGate(noun: 'approval' | 'rejection', subject: string, report = true): boolean {
    const refusal = !canApprove
      ? `${APPROVER_ROLE_REQUIRED}.`
      : campaignBindingBlocked
        ? campaignBindingState === 'validating'
          ? `Campaign binding is still being validated. Wait before ${noun}.`
          : `Campaign binding is invalid. Reopen the saved campaign before ${noun}.`
        : null;
    if (refusal === null) return true;
    if (report) toastWriteRefusal(`Couldn't ${noun === 'approval' ? 'approve' : 'reject'} ${subject}`, refusal);
    return false;
  }

  /** The gate for an approval of one row, or of the selection when no row is named. */
  function canStartApproval(borrowerId?: string): boolean {
    return passesDecisionGate('approval', borrowerId ?? SELECTION_SUBJECT);
  }

  /** The same gate for a bulk rejection of the selection (Shift+R, the Cmd-K verb). */
  function canStartRejection(): boolean {
    return passesDecisionGate('rejection', SELECTION_SUBJECT);
  }

  /** The governed draft an approval certifies, under this table's campaign binding. */
  function draftForApproval(borrowerId: string, signal?: AbortSignal): Promise<OutreachDraftResult> {
    return draftGovernedCopy(borrowerId, campaignBinding, signal);
  }

  /**
   * R5-04 synchronous latch: a decision for this row is on the wire, from
   * this mount (the ref flips before any await) or from one that unmounted
   * mid-write (the MutationCache outlives the table). The table also reads
   * it before it opens a NEW review: that review would draft, and a draft
   * writes a DRAFT_OUTREACH audit row.
   */
  function decisionInFlight(borrowerId: string): boolean {
    return rowInFlightRef.current[borrowerId] === true || isDecisionPending(queryClient, borrowerId);
  }

  // The one-row writes (leadDecisionWrites) read this hook's state through
  // these deps, synchronously, at call time.
  const writeDeps: DecisionWriteDeps = {
    leadsById,
    campaignBinding,
    leadViewId,
    approvalRequestBatchId,
    approve: (variables) => approveMutation.mutateAsync(variables),
    reject: (variables) => rejectMutation.mutateAsync(variables),
    requestIds,
    isInFlight: decisionInFlight,
    passesGate: passesDecisionGate,
    latch: (borrowerId, inFlight) => {
      rowInFlightRef.current[borrowerId] = inFlight;
    },
    onDecided: (borrowerId, decision, auditEventId) => {
      decidedRef.current.add(borrowerId);
      setApproval(borrowerId, decision);
      recordDecision(borrowerId, { auditEventId, decision });
    },
    stillDecidable: (borrowerId) => !isDecisionLocked(borrowerId) && eligibleRef.current.has(borrowerId),
    onRetryApproved: (borrowerId) => lateApprovedRef?.current?.(borrowerId),
  };
  const approveWithReport = (
    borrowerId: string,
    signal: AbortSignal | undefined,
    extras: ApproveExtras,
    reviewedDraft: OutreachDraftResult | null,
  ) => writeApprove(writeDeps, borrowerId, signal, extras, reviewedDraft);

  /** R, Reject or a reject panel's Submit on a row whose decision is on the wire. */
  function reportDecisionInFlight(borrowerId: string): void {
    toastWriteRefusal(`Couldn't reject ${borrowerId}`, `A decision for ${borrowerId} is already being recorded.`);
  }

  /**
   * Approve from the queue without leaving the page. Uses the same
   * `/api/outreach/approve` endpoint Offer Orchestrator calls. The row is
   * marked 'approved' in AppContext only after the write returns (approve
   * stays pessimistic), so the chip flips then and stays flipped on route
   * change.
   *
   * `reviewedDraft` is the exact draft the approver was shown (the approve
   * review, or a bulk sample): its generation id and hash are what the
   * approval binds, and no second draft is generated. Without it (a bulk
   * run's unsampled rows) the draft is generated in the mutation.
   *
   * One request_id per intent: (borrower, reviewed draft) for a single
   * approve, so "Confirm again" after a 500 or a timeout replays the same
   * request and a new draft is a new one; (bulk id, borrower) for a bulk row.
   *
   * Returns a tagged outcome so bulk-approve can distinguish a network
   * drop ("request never reached the audit table") from a backend
   * rejection ("server said no"). Hole-finder finding #2, 2026-04-23.
   */
  function approveLead(
    borrowerId: string,
    signal?: AbortSignal,
    extras: ApproveExtras = {},
    reviewedDraft: OutreachDraftResult | null = null,
  ): Promise<LeadDecisionOutcome> {
    return approveWithReport(borrowerId, signal, extras, reviewedDraft).then(
      ({ outcome }) => (outcome === 'session_expired' || outcome === 'actor_changed' ? 'backend' : outcome),
    );
  }


  /**
   * Reject from the queue without leaving the page. Audit finding
   * 2026-04-22: this used to only mutate AppContext, so rejected
   * borrowers left no durable trace. Now calls `/api/outreach/reject`
   * which writes `mip_app.approvals` (action='reject') +
   * `mip_app.action_audit` (OUTREACH_REJECT) and fires the same
   * lifecycle-sync debounce the approve path uses.
   *
   * On failure we surface the error but do NOT flip the local state,
   * so the user can retry (with the same request_id while the reason and
   * rationale are unchanged). Matches the approve flow's error posture.
   */
  function rejectLead(
    borrowerId: string,
    reasonCode: RejectReasonCode,
    rationale: string | null = null,
  ): Promise<boolean> {
    if (decisionInFlight(borrowerId)) {
      reportDecisionInFlight(borrowerId);
      return Promise.resolve(false);
    }
    if (!passesDecisionGate('rejection', borrowerId)) return Promise.resolve(false);
    rowInFlightRef.current[borrowerId] = true;
    const lead = leadsById.get(borrowerId);
    const intent = intentFingerprint('reject', borrowerId, reasonCode, rationale, ...requestScopeParts(writeDeps));
    const rejected = rejectMutation.mutateAsync({
      decision: 'reject',
      borrowerId,
      requestId: requestIds.idFor(intent),
      rationaleCode: reasonCode,
      rationale,
      campaignBinding,
      evidenceIds: lead?.evidence_ids ?? [],
      offerCode: lead?.recommended_offer_code ?? null,
      bulkId: null,
      leadViewId,
      approvalRequestBatchId,
    }).then(
      (res) => {
        if (!res.rejected) {
          toastWriteRefusal(`Couldn't reject ${borrowerId}`, 'The endpoint returned rejected=false.');
          return false;
        }
        requestIds.settle(intent);
        decidedRef.current.add(borrowerId);
        setApproval(borrowerId, 'rejected');
        recordDecision(borrowerId, { auditEventId: res.audit_event_id ?? null, decision: 'rejected' });
        return true;
      },
      (err: unknown) => {
        if (decisionFailure(err) === 'aborted') return false;
        // states-08 item 3: a 429's Retry re-runs this same rejection once
        // (same request_id: the intent is unsettled), if the row is still open.
        const retry = () => {
          if (decisionInFlight(borrowerId) || !writeDeps.stillDecidable?.(borrowerId)) {
            toastWriteRefusal(`Couldn't reject ${borrowerId}`, DECIDED_SINCE);
            return;
          }
          void rejectLatestRef.current(borrowerId, reasonCode, rationale);
        };
        toastWriteFailure(`Couldn't reject ${borrowerId}`, err, 'the rejection', retry);
        return false;
      },
    );
    const release = () => {
      rowInFlightRef.current[borrowerId] = false;
    };
    void rejected.then(release, release);
    return rejected;
  }

  /**
   * The open reject panel's Submit, with the panel's own reason and
   * rationale. Resolves with the rejected borrower id once the write
   * returned, else null (the panel stays open with what was typed).
   */
  async function submitReject(reasonCode: RejectReasonCode, rationale: string): Promise<string | null> {
    if (!pendingReject) return null;
    const borrowerId = pendingReject;
    const rejected = await rejectLead(borrowerId, reasonCode, rationale.trim() || null);
    if (!rejected) return null;
    setPendingReject(null);
    return borrowerId;
  }

  /**
   * Toggle one row's selection (the row checkbox, X). The checkbox click is
   * stopped from bubbling in the markup so the row still expands/collapses
   * independently. A plain toggle is the anchor a Shift range starts from.
   * Every selection write also drops ids no longer on screen (pruneTo).
   */
  function toggleSelect(borrowerId: string) {
    selectionAnchorRef.current = borrowerId;
    setSelectedIds((cur) => {
      const next = pruneTo(cur, currentIds);
      if (next.has(borrowerId)) next.delete(borrowerId);
      else next.add(borrowerId);
      return next;
    });
  }

  /**
   * Shift-click on a row checkbox, Shift+X on the cursor row (audit
   * tables-07): select every selectable row from the anchor (the last plain
   * toggle) to `target`, in the on-screen order `orderedIds`. Without an
   * anchor it is a plain toggle.
   */
  function selectRange(target: string, orderedIds: readonly string[]) {
    const anchor = selectionAnchorRef.current;
    if (anchor === null || !orderedIds.includes(anchor)) {
      toggleSelect(target);
      return;
    }
    const range = rangeIds(orderedIds, anchor, target, new Set(selectableIds));
    setSelectedIds((cur) => {
      const next = pruneTo(cur, currentIds);
      for (const id of range) next.add(id);
      return next;
    });
  }

  function clearSelection() {
    setSelectedIds(new Set());
    setBulkRunCanary(null);
  }

  /** An approval request was sent for these rows (flow-02): they leave the selection. */
  function dropFromSelection(ids: readonly string[]) {
    const dropped = new Set(ids);
    setSelectedIds((cur) => new Set([...cur].filter((id) => !dropped.has(id))));
  }

  // Sales Manager selection is broader than approval eligibility: already
  // approved rows must still be selectable for assignment/distribution.
  // Rejected and hold rows remain locked out so operations do not
  // accidentally work a dropped or governance-held borrower.
  const selectableIds = displayLeads
    .filter((l) => isLeadSelectableForSalesOps(l.approval_status, approvals[l.borrower_id], l))
    .map((l) => l.borrower_id);
  // A row whose approve or reject is on the wire is not eligible: a sample
  // or a bulk run must never draft (DRAFT_OUTREACH) or decide it again.
  const approvalEligibleIds = displayLeads
    .filter((l) => {
      const localStatus = approvals[l.borrower_id];
      return isLeadApprovalEligible(l.approval_status, localStatus, l) && !pendingDecisions.has(l.borrower_id);
    })
    .map((l) => l.borrower_id);
  useEffect(() => {
    eligibleRef.current = new Set(approvalEligibleIds);
    rejectLatestRef.current = rejectLead;
  });
  // Audit tables-07: the selection a render may act on is the stored set
  // intersected with the rows on screen, derived here (never pruned in an
  // effect). Counts, the header checkbox, the Cmd-K verbs, the CSV scope,
  // assign and a bulk run all read this, so a row a filter change took off
  // screen can no longer be counted or acted on.
  const selectedIds = pruneTo(storedSelection, currentIds);

  // Indeterminate state for the header checkbox: some (but not all)
  // eligible rows selected. We also reflect "all eligible selected" as
  // the checked state.
  const selectedEligibleCount = selectableIds.filter((id) => selectedIds.has(id)).length;
  const headerCheckboxState = selectableIds.length === 0 || selectedEligibleCount === 0
    ? { checked: false, indeterminate: false }
    : selectedEligibleCount === selectableIds.length
      ? { checked: true, indeterminate: false }
      : { checked: false, indeterminate: true };

  function toggleSelectAll() {
    setSelectedIds((cur) => {
      // If any selectable rows remain unselected, select all selectable. Else
      // clear the selection.
      const allEligibleSelected =
        selectableIds.length > 0 && selectableIds.every((id) => cur.has(id));
      if (allEligibleSelected) return new Set();
      return new Set(selectableIds);
    });
  }

  /**
   * Open the shared-rationale gate and put focus in its field. Shift+A and
   * the Cmd-K "Approve N selected…" verb call this: they open the SAME gate
   * the toolbar button does and never submit the run themselves.
   */
  function openBulkRationale() {
    setBulkRationaleOpen(true);
    setBulkRejectOpen(false);
    requestAnimationFrame(() => bulkRationaleRef.current?.focus());
  }

  /** Open the bulk reject gate (reason + shared note) with focus on Reason; never submits. */
  function openBulkReject() {
    setBulkRejectOpen(true);
    setBulkRationaleOpen(false);
    requestAnimationFrame(() => bulkRejectReasonRef.current?.focus());
  }

  /**
   * After a run: successes drop out of the selection; the rows that failed
   * (safe to retry: the server did not commit them) and the rows that never
   * started stay selected. Skipped rows (a decision already on the wire) and
   * unmount-aborted rows are not re-selected (R5-21). One invalidation for
   * the whole run (every row's write suppressed its own), the same as after
   * a single decision: the audited reads go stale and the audit-free
   * approval-request list refetches (flow-02 item 4a: a decided request's
   * count, panel and scope row clear). Then focus goes back to the trigger
   * when the toolbar survives, else to the table region.
   */
  function settleRun(result: BulkRunResult) {
    const keep = [...result.failed.map((issue) => issue.borrowerId), ...result.notStarted];
    setSelectedIds(new Set(keep));
    if (result.ok > 0) invalidateAfterDecision(queryClient);
    const focusTarget = bulkActionFocusTarget(keep.length);
    const trigger = result.kind === 'reject' ? bulkRejectBtnRef : bulkApproveBtnRef;
    requestAnimationFrame(() => {
      if (focusTarget === 'trigger' && trigger.current) trigger.current.focus();
      else tableWrapRef.current?.focus();
    });
  }


  /** What a bulk run reads and sets here (leadBulkDecisions, lazy bulk chunk). */
  function bulkContext(canStart: () => boolean, openGate: (open: boolean) => void): BulkDecisionContext {
    const eligible = new Set(approvalEligibleIds);
    return {
      engine: bulkRun.engine,
      // R5-04: the run's synchronous latch, read before any await.
      busy: bulkRun.isRunning() || bulkApproving,
      // Skip already-decided rows silently.
      ids: [...selectedIds].filter((id) => eligible.has(id)),
      leadsById,
      writeDeps,
      canStart,
      openGate,
      openBulkRationale,
      approveBtnRef: bulkApproveBtnRef,
      begin: (ids) => {
        bulkRunIdsRef.current = new Set(ids);
        setBulkRunCanary(null);
      },
      end: () => {
        bulkRunIdsRef.current = new Set();
      },
      settle: settleRun,
      setCanary: setBulkRunCanary,
    };
  }

  /**
   * Bulk Reject (leadBulkDecisions.runBulkReject): one OUTREACH_REJECT row
   * per selected eligible row under one bulk id, one reason and a required
   * shared note. Fails closed (false, nothing sent) without the bulk chunk.
   *
   * @returns true once a run settled: the gate then closes.
   */
  function bulkReject(reasonCode: RejectReasonCode, note: string): Promise<boolean> {
    const chunk = bulkRuns?.() ?? null;
    if (!chunk) return Promise.resolve(false);
    return chunk.runBulkReject(bulkContext(canStartRejection, setBulkRejectOpen), reasonCode, note);
  }

  /**
   * Bulk-approve (leadBulkDecisions.runBulkApprove): one approve POST (one
   * audit row) per selected eligible row, under a shared rationale, once
   * every offer in the run has a previewed sample. Fails closed (false,
   * nothing drafted or sent) without the bulk chunk; the gate still opens
   * then (it renders once the chunk loads, which a selection starts).
   *
   * @returns true once a run settled: the toolbar then clears its rationale.
   */
  function bulkApprove(
    sampleDrafts: ReadonlyMap<string, OutreachDraftResult> | undefined,
    rationale: string,
  ): Promise<boolean> {
    const chunk = bulkRuns?.() ?? null;
    const context = bulkContext(canStartApproval, setBulkRationaleOpen);
    if (chunk) return chunk.runBulkApprove(context, sampleDrafts, rationale);
    if (!context.busy && context.ids.length > 1 && canStartApproval()) openBulkRationale();
    return Promise.resolve(false);
  }

  // R5-21 / states-07 item 5b: a run the last unmount cut short is raised
  // ONCE on this mount as a shell toast, and its key is dropped in the same
  // step (so StrictMode's second effect run reads nothing). A run that
  // finished reports through bulkRun.result instead.
  const flashCancelledRun = useEffectEvent(() => {
    const cancelled = readCancelledBulk();
    clearCancelledBulk();
    if (cancelled) raiseCancelledBulkToast(cancelled, openRecentActivity);
  });
  useEffect(() => {
    flashCancelledRun();
  }, []);

  /**
   * A row that must not be approved from an open review, read synchronously:
   * its approve / reject write already returned ok in this mount, or it is
   * in the bulk run on the wire. The review re-checks this on Confirm.
   */
  function isDecisionLocked(borrowerId: string): boolean {
    return decidedRef.current.has(borrowerId) || bulkRunIdsRef.current.has(borrowerId);
  }

  /** A bulk run is on the wire (the synchronous latch, not the render state). */
  function isBulkRunInFlight(): boolean {
    return bulkRun.isRunning();
  }

  const selectionCount = selectedIds.size;
  const selectedApprovalEligibleCount = approvalEligibleIds.filter((id) => selectedIds.has(id)).length;

  return {
    approveLead,
    canStartApproval,
    draftForApproval,
    rejectLead,
    submitReject,
    pendingReject,
    setPendingReject,
    pendingDecisions,
    selectedIds,
    selectionCount,
    toggleSelect,
    selectRange,
    clearSelection,
    dropFromSelection,
    toggleSelectAll,
    selectableIds,
    approvalEligibleIds,
    selectedApprovalEligibleCount,
    headerCheckboxState,
    bulkApprove,
    openBulkRationale,
    bulkReject,
    openBulkReject,
    canStartRejection,
    bulkRejectOpen,
    bulkRejectBtnRef,
    bulkRejectReasonRef,
    bulkApproving,
    bulkRun,
    isDecisionLocked,
    isDecisionInFlight: decisionInFlight,
    reportDecisionInFlight,
    isBulkRunInFlight,
    bulkApproveBtnRef,
    bulkRationaleRef,
    bulkRationaleOpen,
    bulkRunCanary,
    decisionReceipts,
    decisionAuditEventId: (borrowerId: string) => receiptIdsRef.current.get(borrowerId) ?? null,
  };
}
