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
import { BULK_APPROVE_CONCURRENCY } from './LeadTable.constants';
import {
  _newBulkId,
  bulkActionFocusTarget,
  chunk,
  isLeadApprovalEligible,
  isLeadSelectableForSalesOps,
  type CampaignBinding,
} from './LeadTable.logic';
import type { RejectReasonCode } from './LeadTable.types';
import type { LeadDecisionReceipt } from './DecisionReceipt';
import { APPROVER_ROLE_REQUIRED } from './approverGate';
import { clearCancelledBulk, readCancelledBulk, stashCancelledBulk } from './bulkApproveStash';

/** Verification state of a `?campaign_id=&variant_name=` URL binding. */
export type CampaignBindingState = 'absent' | 'invalid' | 'verified' | 'validating';

export interface BulkToast {
  ok: number;
  fail: number;
  network: number;
  aborted: number;
}

export type LeadDecisionOutcome = 'ok' | 'network' | 'backend' | 'aborted' | 'duplicate';

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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkApproving, setBulkApproving] = useState<boolean>(false);
  // R5-04 (2026-04-23): synchronous in-flight latches. `setState` is
  // async so two rapid clicks can both read `bulkApproving=false` before
  // either commit schedules, producing two parallel approve loops that
  // each write an audit row per borrower. `useRef` gives us a
  // synchronous read/write we can flip before returning from the click
  // handler; the existing React state still drives the disabled UI.
  const bulkInFlightRef = useRef<boolean>(false);
  const rowInFlightRef = useRef<Record<string, boolean>>({});
  // Wave 1c (flow-03): an approve review can stay open while its row is
  // decided another way (the row's Reject panel, a bulk run). These two
  // synchronous mirrors let the review refuse to approve such a row even
  // from a handler of the render BEFORE the decision's state committed:
  // `decidedRef` holds rows whose approve / reject write returned ok in this
  // mount, `bulkRunIdsRef` the rows of the bulk run on the wire.
  const decidedRef = useRef<Set<string>>(new Set());
  const bulkRunIdsRef = useRef<ReadonlySet<string>>(new Set());
  // Tracks the bulk-approve loop's AbortController so unmount can
  // cancel the remaining in-flight POSTs. Round-2 hole-finder #10/#11,
  // 2026-04-23.
  const bulkAbortRef = useRef<AbortController | null>(null);
  // Last bulk result surfaced as a compact toast. Clears on the next bulk
  // run or when the user dismisses it (auto-dismiss after 4s).
  //
  // `network` is the subset of `fail` that failed with an unreachable
  // backend (ApiError.status === null) — these rows never reached the
  // audit table and the approver should retry them explicitly.
  // Hole-finder finding #2, 2026-04-23.
  //
  // `aborted` rows fall in an ambiguous state: the client cancelled the
  // POST mid-flight on unmount, but the server may have already
  // committed the audit row. Server-side idempotency protects retries
  // that reuse the original request_id; this bulk UI does not persist
  // those per-row ids after unmount, so the honest operator guidance is
  // still to review their actor-scoped recent activity instead of blindly
  // retrying. R5-21
  // (2026-04-23). A run the last unmount cut short is flashed once on this
  // mount (read in the initializer, so StrictMode's double render is safe;
  // the effect below only removes the key).
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
   * mid-write (the MutationCache outlives the table).
   */
  function decisionInFlight(borrowerId: string): boolean {
    return rowInFlightRef.current[borrowerId] === true || isDecisionPending(queryClient, borrowerId);
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
    extras: {
      rationale?: string | null;
      bulk_id?: string | null;
      bulk_rationale?: string | null;
      suppressInvalidation?: boolean;
    } = {},
    reviewedDraft: OutreachDraftResult | null = null,
  ): Promise<LeadDecisionOutcome> {
    if (decisionInFlight(borrowerId)) return Promise.resolve('duplicate');
    if (!canStartApproval()) return Promise.resolve('backend');
    rowInFlightRef.current[borrowerId] = true;
    setApprovalError(null);
    const lead = leadsById.get(borrowerId);
    const intent = extras.bulk_id
      ? intentFingerprint('approve-bulk', extras.bulk_id, borrowerId)
      : intentFingerprint('approve', borrowerId, reviewedDraft?.generation_id);
    const outcome = approveMutation.mutateAsync({
      decision: 'approve',
      borrowerId,
      requestId: requestIds.idFor(intent),
      reviewedDraft,
      campaignBinding,
      evidenceIds: lead?.evidence_ids ?? [],
      offerCode: lead?.recommended_offer_code ?? null,
      rationale: extras.rationale ?? null,
      bulkId: extras.bulk_id ?? null,
      bulkRationale: extras.bulk_rationale ?? null,
      signal,
      suppressInvalidation: extras.suppressInvalidation,
    }).then(
      (res): LeadDecisionOutcome => {
        if (!res.approved) {
          setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
          return 'backend';
        }
        requestIds.settle(intent);
        decidedRef.current.add(borrowerId);
        setApproval(borrowerId, 'approved');
        recordDecision(borrowerId, { auditEventId: res.audit_event_id ?? null, decision: 'approved' });
        return 'ok';
      },
      (err: unknown): LeadDecisionOutcome => {
        const failure = decisionFailure(err);
        if (failure === 'aborted') return 'aborted';
        // The session ended mid-click (on the draft step or the approve POST):
        // the session dialog must say this approval was NOT recorded.
        if (clientFailureReason(err) === 'session_expired') markUnrecordedWrite('approval');
        setApprovalError(
          err instanceof Error
            ? `Couldn't approve ${borrowerId}: ${err.message}`
            : `Couldn't approve ${borrowerId}.`,
        );
        return failure;
      },
    );
    // Released before the caller sees the outcome (this reaction is first).
    const release = () => {
      rowInFlightRef.current[borrowerId] = false;
    };
    void outcome.then(release, release);
    return outcome;
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
    if (decisionInFlight(borrowerId)) return Promise.resolve(false);
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
   * Toggle one row's selection. Called by the row checkbox onChange; the
   * checkbox click is stopped from bubbling in the markup so the row
   * still expands/collapses independently.
   */
  function toggleSelect(borrowerId: string) {
    setSelectedIds((cur) => {
      const next = new Set(cur);
      if (next.has(borrowerId)) next.delete(borrowerId);
      else next.add(borrowerId);
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
  const approvalEligibleIds = displayLeads
    .filter((l) => {
      const localStatus = approvals[l.borrower_id];
      return isLeadApprovalEligible(l.approval_status, localStatus, l);
    })
    .map((l) => l.borrower_id);

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

  /**
   * Bulk-approve: loop `api.approve()` per selected id in chunks of
   * BULK_APPROVE_CONCURRENCY. We deliberately do NOT invent a server-side
   * bulk endpoint — the audit trail wants one row per approval.
   *
   * Successes drop out of the selection set; failures stay selected so
   * the operator can retry. A compact toast summarizes ok/fail counts.
   *
   * @param sampleDrafts drafts the approver previewed through "Preview 3
   *   sample drafts": those rows are approved with exactly that copy, so
   *   what was shown is what the audit rows certify, and no second draft
   *   is generated for them.
   */
  async function bulkApprove(sampleDrafts?: ReadonlyMap<string, OutreachDraftResult>) {
    // R5-04: synchronous latch. React setState is async, so two rapid
    // clicks can both read `bulkApproving=false` before either commit
    // schedules — producing two parallel loops with the same selection
    // and two audit rows per borrower. Flip the ref before any await.
    if (bulkInFlightRef.current || bulkApproving) return;
    if (!passesDecisionGate('approval')) return;
    const drafts = sampleDrafts ?? new Map<string, OutreachDraftResult>();
    bulkInFlightRef.current = true;
    // Snapshot which ids to run: skip already-decided rows silently.
    const eligibleForApproval = new Set(approvalEligibleIds);
    const ids = [...selectedIds].filter((id) => eligibleForApproval.has(id));
    if (ids.length === 0) {
      bulkInFlightRef.current = false;
      return;
    }
    const bulkId = ids.length > 1 ? _newBulkId() : null;
    const sharedRationale = ids.length > 1 ? bulkRationale.trim() : '';
    if (ids.length > 1 && sharedRationale.length === 0) {
      openBulkRationale();
      bulkInFlightRef.current = false;
      return;
    }
    bulkRunIdsRef.current = new Set(ids);
    // One controller for the whole bulk loop; unmount aborts every
    // still-inflight POST. sessionStorage stashes the partial result so
    // the next mount can flash "N landed, rest aborted" — otherwise
    // the user sees no feedback that their bulk action got cut short.
    const ctrl = new AbortController();
    bulkAbortRef.current = ctrl;
    setBulkApproving(true);
    setBulkToast(null);
    let ok = 0;
    let fail = 0;
    let network = 0;
    let aborted = 0;
    // `failedIds` is only the subset safe to retry (backend/network
    // rejections — the server definitely did not commit). Aborted ids
    // stay out of this list because the server may have committed and
    // a retry would duplicate the audit row. R5-21.
    const failedIds: string[] = [];
    const abortedIds: string[] = [];
    for (const group of chunk(ids, BULK_APPROVE_CONCURRENCY)) {
      if (ctrl.signal.aborted) {
        aborted += group.length;
        abortedIds.push(...group);
        continue;
      }
      const results = await Promise.all(group.map((id) => approveLead(id, ctrl.signal, {
        bulk_id: bulkId,
        bulk_rationale: sharedRationale || null,
        suppressInvalidation: true,
      }, drafts.get(id) ?? null)));
      results.forEach((outcome, i) => {
        if (outcome === 'ok') {
          ok += 1;
        } else if (outcome === 'aborted') {
          aborted += 1;
          abortedIds.push(group[i]);
        } else {
          fail += 1;
          if (outcome === 'network') network += 1;
          failedIds.push(group[i]);
        }
      });
    }
    if (ctrl.signal.aborted) {
      // Stash the partial result so the next mount can flash it. We
      // accept that the user may never come back to this page; the
      // alternative (loud toast on unmount) wouldn't render anyway.
      stashCancelledBulk(ok, aborted);
      bulkRunIdsRef.current = new Set();
      bulkInFlightRef.current = false;
      return;
    }
    // Quieten unused-var lint: abortedIds is tracked for future reuse
    // (R5-01 idempotency can retry by id) but not needed in this frame.
    void abortedIds;
    // Replace selection with the retryable subset so retries are
    // trivial. Aborted ids are deliberately NOT re-selected — the
    // server may have committed them and a blind re-click would
    // duplicate the audit row. R5-21 (2026-04-23).
    setSelectedIds(new Set(failedIds));
    if (ok > 0) void invalidateOperationalQueries(queryClient);
    setBulkRationaleOpen(false);
    setBulkRationale('');
    setBulkApproving(false);
    setBulkToast({ ok, fail, network, aborted });
    bulkAbortRef.current = null;
    bulkRunIdsRef.current = new Set();
    bulkInFlightRef.current = false;
    // A11y: restore keyboard focus once React commits the cleared/retained
    // selection. `failedIds` is exactly what drives the next selection, so
    // we can pick the target synchronously, then defer the .focus() to the
    // next frame so the toolbar's mount/unmount has settled. On a full
    // success the toolbar unmounts -> focus the always-present table region;
    // on a partial outcome the trigger button survives -> refocus it.
    const focusTarget = bulkActionFocusTarget(failedIds.length);
    requestAnimationFrame(() => {
      if (focusTarget === 'trigger' && bulkApproveBtnRef.current) {
        bulkApproveBtnRef.current.focus();
      } else {
        tableWrapRef.current?.focus();
      }
    });
  }

  // The bulkToast initializer read any partial run the previous mount left
  // (R5-21: aborted ids are audit-ambiguous, so the toast offers Recent
  // activity, never a retry). This mount has shown it: drop the key.
  useEffect(() => {
    clearCancelledBulk();
  }, []);

  // Abort the bulk-approve loop on unmount so the remaining POSTs
  // cancel cleanly.
  useEffect(() => {
    return () => {
      bulkAbortRef.current?.abort();
    };
  }, []);

  // Auto-dismiss settled results after 4s. Ambiguous cancelled requests stay
  // visible until the operator opens Recent activity to resolve them.
  useEffect(() => {
    if (!bulkToast || bulkToast.aborted > 0) return;
    const t = window.setTimeout(() => setBulkToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [bulkToast]);

  /**
   * A row that must not be approved from an open review, read synchronously:
   * its approve / reject write already returned ok in this mount, it is in
   * the bulk run on the wire, or an approve / reject for it is still on the
   * wire in the MutationCache (from this mount or one that unmounted
   * mid-write). The review checks this before it drafts and on Confirm, so a
   * remounted table writes no second DRAFT_OUTREACH row for it.
   */
  function isDecisionLocked(borrowerId: string): boolean {
    return decidedRef.current.has(borrowerId)
      || bulkRunIdsRef.current.has(borrowerId)
      || isDecisionPending(queryClient, borrowerId);
  }

  /** A bulk run is on the wire (the synchronous latch, not the render state). */
  function isBulkRunInFlight(): boolean {
    return bulkInFlightRef.current;
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
    clearSelection,
    toggleSelectAll,
    selectableIds,
    approvalEligibleIds,
    selectedApprovalEligibleCount,
    headerCheckboxState,
    bulkApprove,
    openBulkRationale,
    bulkApproving,
    isDecisionLocked,
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
