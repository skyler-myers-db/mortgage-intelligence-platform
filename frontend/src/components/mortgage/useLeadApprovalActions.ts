/**
 * useLeadApprovalActions — the human-approval half of the ranked-borrower
 * table: single-row approve/reject, the reject panel's form state, the
 * selection set feeding the bulk toolbar, the chunked bulk-approve loop with
 * its synchronous in-flight latches, the bulk toast lifecycle, and the
 * post-bulk focus restore. Extracted from LeadTable.tsx (file-size gate,
 * plan item 2); state ownership and effect order are unchanged.
 */

import { useEffect, useRef, useState, type RefObject } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { LeadSummary } from '../../types';
import { api, ApiError, isAbortError } from '../../lib/api';
import { invalidateOperationalQueries } from '../../lib/queryKeys';
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

/** Verification state of a `?campaign_id=&variant_name=` URL binding. */
export type CampaignBindingState = 'absent' | 'invalid' | 'verified' | 'validating';

export interface BulkToast {
  ok: number;
  fail: number;
  network: number;
  aborted: number;
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
  tableWrapRef,
  setApprovalError,
}: UseLeadApprovalActionsInput) {
  'use no memo';

  // A11y: the bulk-approve button is the launch point for the bulk flow.
  // After the action settles we restore focus deterministically — to this
  // button when it survives (partial outcome keeps the toolbar mounted) or
  // to the table scroll region when a full success unmounts the toolbar.
  // Without this, keyboard focus silently drops to <body> after a bulk run.
  const bulkApproveBtnRef = useRef<HTMLButtonElement | null>(null);
  const [pendingReject, setPendingReject] = useState<string | null>(null);
  const [rejectReasonCode, setRejectReasonCode] = useState<RejectReasonCode>('low_intent');
  const [rejectRationale, setRejectRationale] = useState('');
  const [bulkRationaleOpen, setBulkRationaleOpen] = useState(false);
  const [bulkRationale, setBulkRationale] = useState('');
  const [pendingApproval, setPendingApproval] = useState<Record<string, boolean>>({});
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
  // (2026-04-23).
  const [bulkToast, setBulkToast] = useState<BulkToast | null>(null);

  /**
   * Approve from the queue without leaving the page. Uses the same
   * `/api/outreach/approve` endpoint Offer Orchestrator calls. We mark
   * the row as 'approved' in AppContext optimistically on success so the
   * chip flips immediately and stays flipped on route change.
   *
   * Returns a tagged outcome so bulk-approve can distinguish a network
   * drop ("request never reached the audit table") from a backend
   * rejection ("server said no"). Hole-finder finding #2, 2026-04-23.
   */
  async function approveLead(
    borrowerId: string,
    signal?: AbortSignal,
    extras: {
      rationale?: string | null;
      bulk_id?: string | null;
      bulk_rationale?: string | null;
      suppressInvalidation?: boolean;
    } = {},
  ): Promise<'ok' | 'network' | 'backend' | 'aborted' | 'duplicate'> {
    // R5-04: synchronous latch check. setState is async, so a rapid
    // second click could slip in before `pendingApproval[id]` flips
    // to true and produce a second audit row. The ref flips
    // immediately.
    if (rowInFlightRef.current[borrowerId]) return 'duplicate';
    if (campaignBindingBlocked) {
      setApprovalError(
        campaignBindingState === 'validating'
          ? 'Campaign binding is still being validated. Wait before approval.'
          : 'Campaign binding is invalid. Reopen the saved campaign before approval.',
      );
      return 'backend';
    }
    rowInFlightRef.current[borrowerId] = true;
    setApprovalError(null);
    setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
    try {
      const lead = leadsById.get(borrowerId);
      const draft = campaignBinding
        ? await api.draftOutreach(borrowerId, 'email', signal, campaignBinding)
        : await api.draftOutreach(borrowerId, 'email', signal);
      if (
        campaignBinding
        && (
          draft.campaign_id !== campaignBinding.campaign_id
          || draft.variant_name !== campaignBinding.variant_name
        )
      ) {
        throw new Error('Campaign variant proof is stale. Reopen the saved campaign before approval.');
      }
      const draftSubject = draft.subject?.trim();
      if (!draftSubject) {
        throw new Error('Governed email draft returned without a subject. Regenerate before approval.');
      }
      const res = await api.approve(
        borrowerId,
        {
          evidence_ids: lead?.evidence_ids ?? [],
          offer_code: draft.offer_code ?? lead?.recommended_offer_code ?? null,
          draft_subject: draftSubject,
          draft_body: draft.body,
          draft_generation_id: draft.generation_id,
          draft_response_hash: draft.response_hash,
          draft_source_refreshed_at: draft.source_refreshed_at,
          channel: 'email',
          rationale: extras.rationale ?? null,
          bulk_id: extras.bulk_id ?? null,
          bulk_rationale: extras.bulk_rationale ?? null,
          campaign_id: campaignBinding?.campaign_id ?? null,
          variant_name: campaignBinding?.variant_name ?? null,
        },
        signal,
      );
      if (res.approved) {
        setApproval(borrowerId, 'approved');
        if (!extras.suppressInvalidation) void invalidateOperationalQueries(queryClient);
        return 'ok';
      }
      setApprovalError(`Approve failed for ${borrowerId}: endpoint returned approved=false.`);
      return 'backend';
    } catch (err: unknown) {
      if (isAbortError(err)) return 'aborted';
      const isNetwork = err instanceof ApiError && err.status === null;
      setApprovalError(
        err instanceof Error
          ? `Couldn't approve ${borrowerId}: ${err.message}`
          : `Couldn't approve ${borrowerId}.`,
      );
      return isNetwork ? 'network' : 'backend';
    } finally {
      rowInFlightRef.current[borrowerId] = false;
      setPendingApproval((p) => {
        const { [borrowerId]: _discard, ...rest } = p;
        return rest;
      });
    }
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
   * so the user can retry. Matches the approve flow's error posture.
   */
  async function rejectLead(
    borrowerId: string,
    reasonCode: RejectReasonCode,
    rationale: string | null = null,
  ): Promise<boolean> {
    // R5-04: synchronous latch — see approveLead above.
    if (rowInFlightRef.current[borrowerId]) return false;
    if (campaignBindingBlocked) {
      setApprovalError(
        campaignBindingState === 'validating'
          ? 'Campaign binding is still being validated. Wait before rejection.'
          : 'Campaign binding is invalid. Reopen the saved campaign before rejection.',
      );
      return false;
    }
    rowInFlightRef.current[borrowerId] = true;
    setApprovalError(null);
    setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
    try {
      const lead = leadsById.get(borrowerId);
      const res = await api.reject(
        borrowerId,
        {
          evidence_ids: lead?.evidence_ids ?? [],
          offer_code: lead?.recommended_offer_code ?? null,
          rationale_code: reasonCode,
          rationale,
          campaign_id: campaignBinding?.campaign_id ?? null,
          variant_name: campaignBinding?.variant_name ?? null,
        },
      );
      if (res.rejected) {
        setApproval(borrowerId, 'rejected');
        void invalidateOperationalQueries(queryClient);
        return true;
      }
      setApprovalError(`Reject failed for ${borrowerId}: endpoint returned rejected=false.`);
      return false;
    } catch (err: unknown) {
      if (isAbortError(err)) return false;
      setApprovalError(
        err instanceof Error
          ? `Couldn't reject ${borrowerId}: ${err.message}`
          : `Couldn't reject ${borrowerId}.`,
      );
      return false;
    } finally {
      rowInFlightRef.current[borrowerId] = false;
      setPendingApproval((p) => {
        const { [borrowerId]: _discard, ...rest } = p;
        return rest;
      });
    }
  }

  async function submitReject() {
    if (!pendingReject) return;
    const rejected = await rejectLead(pendingReject, rejectReasonCode, rejectRationale.trim() || null);
    if (rejected) {
      setPendingReject(null);
      setRejectRationale('');
      setRejectReasonCode('low_intent');
    }
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
   * Bulk-approve: loop `api.approve()` per selected id in chunks of
   * BULK_APPROVE_CONCURRENCY. We deliberately do NOT invent a server-side
   * bulk endpoint — the audit trail wants one row per approval.
   *
   * Successes drop out of the selection set; failures stay selected so
   * the operator can retry. A compact toast summarizes ok/fail counts.
   */
  async function bulkApprove() {
    // R5-04: synchronous latch. React setState is async, so two rapid
    // clicks can both read `bulkApproving=false` before either commit
    // schedules — producing two parallel loops with the same selection
    // and two audit rows per borrower. Flip the ref before any await.
    if (bulkInFlightRef.current || bulkApproving) return;
    if (campaignBindingBlocked) {
      setApprovalError(
        campaignBindingState === 'validating'
          ? 'Campaign binding is still being validated. Wait before approval.'
          : 'Campaign binding is invalid. Reopen the saved campaign before approval.',
      );
      return;
    }
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
      setBulkRationaleOpen(true);
      bulkInFlightRef.current = false;
      return;
    }
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
      })));
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
      try {
        sessionStorage.setItem(
          'mip.bulkApprove.lastCancelled',
          // `bulkApprove` only ever runs from a click / Shift+A, never during
          // render, so the wall-clock read is safe here; the mount-restore
          // effect below compares it to drop stale snapshots. Unchanged from
          // the pre-split component — where it was masked from this rule by
          // the useVirtualizer (`react-hooks/incompatible-library`) bailout.
          // eslint-disable-next-line react-hooks/purity
          JSON.stringify({ ok, aborted, ts: Date.now() }),
        );
      } catch {
        // private mode or quota — ignore
      }
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

  // On mount: if the previous mount left a partial bulk-approve snapshot
  // in sessionStorage (user navigated away mid-loop), flash a compact
  // toast so the operator knows how many landed.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem('mip.bulkApprove.lastCancelled');
      if (!raw) return;
      sessionStorage.removeItem('mip.bulkApprove.lastCancelled');
      const parsed = JSON.parse(raw) as { ok?: number; aborted?: number; ts?: number };
      // Drop stale messages (older than 10 minutes) — they're probably
      // from a much-earlier session.
      if (parsed?.ts && Date.now() - parsed.ts > 10 * 60 * 1000) return;
      const ok = parsed.ok ?? 0;
      const aborted = parsed.aborted ?? 0;
      if (ok + aborted === 0) return;
      // R5-21: route unmounted mid-loop. Aborted ids are in ambiguous
      // state (server may have committed). Surface an actor-scoped recovery
      // action rather than mixing them into the retryable `fail` count.
      setBulkToast({ ok, fail: 0, network: 0, aborted });
    } catch {
      // malformed payload — ignore
    }
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

  const selectionCount = selectedIds.size;
  const selectedApprovalEligibleCount = approvalEligibleIds.filter((id) => selectedIds.has(id)).length;

  return {
    approveLead,
    rejectLead,
    submitReject,
    pendingReject,
    setPendingReject,
    rejectReasonCode,
    setRejectReasonCode,
    rejectRationale,
    setRejectRationale,
    pendingApproval,
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
    bulkApproving,
    bulkApproveBtnRef,
    bulkRationale,
    setBulkRationale,
    bulkRationaleOpen,
    bulkToast,
    setBulkToast,
  };
}
