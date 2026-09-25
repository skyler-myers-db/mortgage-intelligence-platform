/**
 * useLeadApprovalActions — the human-approval half of the ranked-borrower
 * table: single-row approve/reject, the reject panel's form state, the
 * selection set feeding the bulk toolbar, the chunked bulk-approve loop with
 * its synchronous in-flight latches, the bulk toast lifecycle, and the
 * post-bulk focus restore. Extracted from LeadTable.tsx (file-size gate,
 * plan item 2).
 *
 * The writes run on the governed outreach mutations (lib/mutations/outreach,
 * audit stack-09 / tables-05): pessimistic (a row reads Approved only after
 * the POST returned approved=true), networkMode 'always', one request_id per
 * intent, and the pending state read from the MutationCache so a table that
 * remounted mid-write still knows the row is on the wire.
 */

import { useEffect, useRef, useState, type RefObject } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { LeadSummary } from '../../types';
import { clientFailureReason } from '../../lib/apiTransport';
import { markUnrecordedWrite } from '../../lib/sessionStatus';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { invalidateOperationalQueries } from '../../lib/queryKeys';
import {
  decisionFailure,
  draftForApproval as draftGovernedCopy,
  isDecisionPending,
  useApproveLead,
  usePendingDecisions,
  useRejectLead,
} from '../../lib/mutations/outreach';
import { intentFingerprint, useIntentRequestIds } from '../../lib/mutations/requestIds';
import {
  _newBulkId,
  bulkActionFocusTarget,
  isLeadApprovalEligible,
  isLeadSelectableForSalesOps,
  type CampaignBinding,
} from './LeadTable.logic';
import type { RejectReasonCode } from './LeadTable.types';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { APPROVER_ROLE_REQUIRED } from './approverGate';
import { clearCancelledBulk, readCancelledBulk } from './bulkApproveStash';
import { useLeadBulkRun, type BulkRowReport, type BulkRunResult } from './useLeadBulkRun';
import { pruneTo, rangeIds } from './LeadTable.selection';

const BULK_TOAST_DISMISS_MS = 4000;

/** Verification state of a `?campaign_id=&variant_name=` URL binding. */
export type CampaignBindingState = 'absent' | 'invalid' | 'verified' | 'validating';

export interface BulkToast {
  ok: number;
  fail: number;
  network: number;
  aborted: number;
}

export type LeadDecisionOutcome = 'ok' | 'network' | 'backend' | 'aborted' | 'duplicate';

/** What an approval certifies about its row, snapshotted when a bulk run starts. */
interface DecisionSnapshot {
  evidenceIds: readonly string[];
  offerCode: string | null;
}

interface ApproveExtras {
  rationale?: string | null;
  bulk_id?: string | null;
  bulk_rationale?: string | null;
  suppressInvalidation?: boolean;
  /** A bulk row: the evidence and offer read when the run started, never live mid-run. */
  snapshot?: DecisionSnapshot;
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
  /** Shared with the sales-ops hook: the table renders one error alert. */
  setApprovalError: (message: string | null) => void;
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
  setApprovalError,
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
  const [pendingReject, setPendingReject] = useState<string | null>(null);
  const [rejectReasonCode, setRejectReasonCode] = useState<RejectReasonCode>('low_intent');
  const [rejectRationale, setRejectRationale] = useState('');
  const [bulkRationaleOpen, setBulkRationaleOpen] = useState(false);
  const [bulkRationale, setBulkRationale] = useState('');
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
  const bulkRunIdsRef = useRef<ReadonlySet<string>>(new Set());
  // R5-21: a run the last unmount cut short is flashed once on this mount
  // (read in the initializer, so StrictMode's double render is safe; the
  // effect below only removes the key). Aborted rows are audit-ambiguous: the
  // server may have committed them, so the toast sends the operator to Recent
  // activity instead of offering a blind retry. A run that finished reports
  // through bulkRun.result instead.
  const [bulkToast, setBulkToast] = useState<BulkToast | null>(() => {
    const cancelled = readCancelledBulk();
    return cancelled ? { ok: cancelled.ok, fail: 0, network: 0, aborted: cancelled.aborted } : null;
  });
  // wow-stage-3: the audit row each row decision wrote, keyed by borrower.
  // The expanded row reads it back as a Decision receipt; the id comes from
  // the POST response and nothing else about the receipt is kept here.
  // motion-06: the receipt's reveal plays once per decision; markRevealed
  // flips `revealed` so a collapse + re-expand renders it finished.
  const [decisionReceipts, setDecisionReceipts] = useState<Record<string, LeadDecisionReceipt>>({});
  const recordDecision = (borrowerId: string, receipt: LeadDecisionReceipt) => {
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
   * review (flow-03) runs it before it drafts anything.
   */
  function passesDecisionGate(noun: 'approval' | 'rejection'): boolean {
    if (!canApprove) {
      setApprovalError(`${APPROVER_ROLE_REQUIRED}.`);
      return false;
    }
    if (campaignBindingBlocked) {
      setApprovalError(
        campaignBindingState === 'validating'
          ? `Campaign binding is still being validated. Wait before ${noun}.`
          : `Campaign binding is invalid. Reopen the saved campaign before ${noun}.`,
      );
      return false;
    }
    return true;
  }

  function canStartApproval(): boolean {
    return passesDecisionGate('approval');
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

  /** R, Reject or a reject panel's Submit on a row whose decision is on the wire. */
  function reportDecisionInFlight(borrowerId: string): void {
    setApprovalError(`A decision for ${borrowerId} is already being recorded.`);
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
      ({ outcome }) => (outcome === 'session_expired' ? 'backend' : outcome),
    );
  }

  /**
   * approveLead with the per-row report a bulk run lists: the outcome
   * (session_expired told apart) and the server's reason. A bulk row reports
   * into the run's result, not the table's single alert, and certifies the
   * evidence and offer snapshotted when the run started.
   */
  function approveWithReport(
    borrowerId: string,
    signal: AbortSignal | undefined,
    extras: ApproveExtras,
    reviewedDraft: OutreachDraftResult | null,
  ): Promise<BulkRowReport> {
    if (decisionInFlight(borrowerId)) return Promise.resolve({ outcome: 'duplicate', message: null });
    if (!canStartApproval()) return Promise.resolve({ outcome: 'backend', message: null });
    rowInFlightRef.current[borrowerId] = true;
    const inBulk = Boolean(extras.bulk_id);
    if (!inBulk) setApprovalError(null);
    const lead = leadsById.get(borrowerId);
    const snapshot = extras.snapshot ?? {
      evidenceIds: lead?.evidence_ids ?? [],
      offerCode: lead?.recommended_offer_code ?? null,
    };
    const intent = extras.bulk_id
      ? intentFingerprint('approve-bulk', extras.bulk_id, borrowerId)
      : intentFingerprint('approve', borrowerId, reviewedDraft?.generation_id);
    const report = approveMutation.mutateAsync({
      decision: 'approve',
      borrowerId,
      requestId: requestIds.idFor(intent),
      reviewedDraft,
      campaignBinding,
      evidenceIds: [...snapshot.evidenceIds],
      offerCode: snapshot.offerCode,
      rationale: extras.rationale ?? null,
      bulkId: extras.bulk_id ?? null,
      bulkRationale: extras.bulk_rationale ?? null,
      signal,
      suppressInvalidation: extras.suppressInvalidation,
    }).then(
      (res): BulkRowReport => {
        if (!res.approved) {
          if (!inBulk) setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
          return { outcome: 'backend', message: 'The endpoint returned approved=false.' };
        }
        requestIds.settle(intent);
        decidedRef.current.add(borrowerId);
        setApproval(borrowerId, 'approved');
        recordDecision(borrowerId, { auditEventId: res.audit_event_id ?? null, decision: 'approved' });
        return { outcome: 'ok', message: null };
      },
      (err: unknown): BulkRowReport => {
        const failure = decisionFailure(err);
        if (failure === 'aborted') return { outcome: 'aborted', message: null };
        const message = err instanceof Error ? err.message : null;
        if (!inBulk) {
          setApprovalError(message ? `Couldn't approve ${borrowerId}: ${message}` : `Couldn't approve ${borrowerId}.`);
        }
        // The session ended mid-click (on the draft step or the approve POST):
        // the session dialog must say this approval was NOT recorded.
        if (clientFailureReason(err) === 'session_expired') {
          markUnrecordedWrite('approval');
          return { outcome: 'session_expired', message };
        }
        return { outcome: failure, message };
      },
    );
    // Released before the caller sees the outcome (this reaction is first).
    const release = () => {
      rowInFlightRef.current[borrowerId] = false;
    };
    void report.then(release, release);
    return report;
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
    if (!passesDecisionGate('rejection')) return Promise.resolve(false);
    rowInFlightRef.current[borrowerId] = true;
    setApprovalError(null);
    const lead = leadsById.get(borrowerId);
    const intent = intentFingerprint('reject', borrowerId, reasonCode, rationale);
    const rejected = rejectMutation.mutateAsync({
      decision: 'reject',
      borrowerId,
      requestId: requestIds.idFor(intent),
      rationaleCode: reasonCode,
      rationale,
      campaignBinding,
      evidenceIds: lead?.evidence_ids ?? [],
      offerCode: lead?.recommended_offer_code ?? null,
    }).then(
      (res) => {
        if (!res.rejected) {
          setApprovalError(`Reject failed for ${borrowerId}: endpoint returned rejected=false.`);
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
        setApprovalError(
          err instanceof Error
            ? `Couldn't reject ${borrowerId}: ${err.message}`
            : `Couldn't reject ${borrowerId}.`,
        );
        return false;
      },
    );
    const release = () => {
      rowInFlightRef.current[borrowerId] = false;
    };
    void rejected.then(release, release);
    return rejected;
  }

  /** Resolves with the rejected borrower id once the write returned, else null. */
  async function submitReject(): Promise<string | null> {
    if (!pendingReject) return null;
    const borrowerId = pendingReject;
    const rejected = await rejectLead(borrowerId, rejectReasonCode, rejectRationale.trim() || null);
    if (!rejected) return null;
    setPendingReject(null);
    setRejectRationale('');
    setRejectReasonCode('low_intent');
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
    requestAnimationFrame(() => bulkRationaleRef.current?.focus());
  }

  /** What each row of a run certifies, read once when the run starts. */
  function snapshotRows(ids: readonly string[]): Map<string, DecisionSnapshot> {
    return new Map(ids.map((borrowerId) => {
      const lead = leadsById.get(borrowerId);
      return [borrowerId, {
        evidenceIds: [...(lead?.evidence_ids ?? [])],
        offerCode: lead?.recommended_offer_code ?? null,
      }];
    }));
  }

  /**
   * After a run: successes drop out of the selection; the rows that failed
   * (safe to retry: the server did not commit them) and the rows that never
   * started stay selected. Skipped rows (a decision already on the wire) and
   * unmount-aborted rows are not re-selected (R5-21). One invalidation for
   * the whole run, then focus goes back to the trigger when the toolbar
   * survives, else to the table region.
   */
  function settleRun(result: BulkRunResult) {
    const keep = [...result.failed.map((issue) => issue.borrowerId), ...result.notStarted];
    setSelectedIds(new Set(keep));
    if (result.ok > 0) void invalidateOperationalQueries(queryClient);
    const focusTarget = bulkActionFocusTarget(keep.length);
    requestAnimationFrame(() => {
      if (focusTarget === 'trigger' && bulkApproveBtnRef.current) bulkApproveBtnRef.current.focus();
      else tableWrapRef.current?.focus();
    });
  }

  /**
   * Bulk-approve: one approve POST (one audit row) per selected eligible
   * row, BULK_APPROVE_CONCURRENCY at a time, through useLeadBulkRun. We
   * deliberately do NOT invent a server-side bulk endpoint: every approval
   * keeps its own governed draft proof and audit row.
   *
   * @param sampleDrafts drafts the approver previewed through "Preview 3
   *   sample drafts": those rows are approved with exactly that copy, so
   *   what was shown is what the audit rows certify, and no second draft
   *   is generated for them.
   */
  async function bulkApprove(sampleDrafts?: ReadonlyMap<string, OutreachDraftResult>) {
    // R5-04: the run's synchronous latch, read before any await.
    if (bulkRun.isRunning() || bulkApproving) return;
    if (!passesDecisionGate('approval')) return;
    const drafts = sampleDrafts ?? new Map<string, OutreachDraftResult>();
    // Snapshot which ids to run: skip already-decided rows silently.
    const eligibleForApproval = new Set(approvalEligibleIds);
    const ids = [...selectedIds].filter((id) => eligibleForApproval.has(id));
    if (ids.length === 0) return;
    const bulkId = ids.length > 1 ? _newBulkId() : null;
    const sharedRationale = ids.length > 1 ? bulkRationale.trim() : '';
    if (ids.length > 1 && sharedRationale.length === 0) {
      openBulkRationale();
      return;
    }
    const snapshots = snapshotRows(ids);
    bulkRunIdsRef.current = new Set(ids);
    setBulkToast(null);
    const result = await bulkRun.start({
      kind: 'approve',
      rows: ids.map((borrowerId) => ({ borrowerId, posts: drafts.has(borrowerId) ? 1 : 2 })),
      decide: (borrowerId, signal) => approveWithReport(borrowerId, signal, {
        bulk_id: bulkId,
        bulk_rationale: sharedRationale || null,
        suppressInvalidation: true,
        snapshot: snapshots.get(borrowerId),
      }, drafts.get(borrowerId) ?? null),
    });
    bulkRunIdsRef.current = new Set();
    // null: unmount cut the run short (stashed, R5-21) or one was running.
    if (!result) return;
    setBulkRationaleOpen(false);
    setBulkRationale('');
    settleRun(result);
  }

  // The bulkToast initializer read any partial run the previous mount left
  // (R5-21: aborted ids are audit-ambiguous, so the toast offers Recent
  // activity, never a retry). This mount has shown it: drop the key.
  useEffect(() => {
    clearCancelledBulk();
  }, []);

  // A flashed run with nothing aborted (the unmount landed after its last
  // POST returned) clears itself after 4 s, as before the run moved to
  // useLeadBulkRun; an audit-ambiguous one (aborted > 0) stays until the
  // operator opens Recent activity.
  useEffect(() => {
    if (!bulkToast || bulkToast.aborted > 0) return undefined;
    const timer = window.setTimeout(() => setBulkToast(null), BULK_TOAST_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [bulkToast]);

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
    rejectReasonCode,
    setRejectReasonCode,
    rejectRationale,
    setRejectRationale,
    pendingDecisions,
    selectedIds,
    selectionCount,
    toggleSelect,
    selectRange,
    clearSelection,
    toggleSelectAll,
    selectableIds,
    approvalEligibleIds,
    selectedApprovalEligibleCount,
    headerCheckboxState,
    bulkApprove,
    openBulkRationale,
    bulkApproving,
    bulkRun,
    isDecisionLocked,
    isDecisionInFlight: decisionInFlight,
    reportDecisionInFlight,
    isBulkRunInFlight,
    bulkApproveBtnRef,
    bulkRationaleRef,
    bulkRationale,
    setBulkRationale,
    bulkRationaleOpen,
    bulkToast,
    setBulkToast,
    decisionReceipts,
  };
}
