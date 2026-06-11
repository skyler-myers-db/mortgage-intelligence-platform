import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { CallDisposition, LeadSummary } from '../../types';
import { Icon } from '../Icon';
import { Chip, Button } from '../Primitives';
import { useApp } from '../AppContext';
import { api, ApiError, isAbortError } from '../../lib/api';
import { invalidateOperationalQueries } from '../../lib/queryKeys';
import { buildLeadCsv } from './LeadTable.csv';
import {
  BULK_APPROVE_CONCURRENCY,
  LEAD_ROW_ESTIMATE_PX,
  LEAD_ROW_OVERSCAN,
  LEAD_TABLE_COL_COUNT,
  LEAD_VIRTUALIZATION_THRESHOLD,
} from './LeadTable.constants';
import {
  _newBulkId,
  bulkActionFocusTarget,
  chunk,
  dispositionLabel,
  isEditableTarget,
  isLeadApprovalEligible,
  isLeadSelectableForSalesOps,
  isTerminalApproval,
  sortValue,
} from './LeadTable.logic';
import { LeadTableRow } from './LeadTableRow';
import { LeadDispositionPanel, LeadRejectPanel } from './LeadTableDecisionPanels';
import type { LeadTableProps, RejectReasonCode, SortDir, SortKey } from './LeadTable.types';

export { buildLeadCsv } from './LeadTable.csv';
export {
  bulkActionFocusTarget,
  isEditableTarget,
  isLeadApprovalEligible,
  isLeadMarketingActionable,
  isLeadSelectableForSalesOps,
} from './LeadTable.logic';
export type { LeadExportContext } from './LeadTable.types';

/**
 * LeadTable — prototype `.surface` + `.tbl` BEM. Sticky thead, hover, row
 * expand into a mini borrower-detail preview. Approvals track per-row via
 * AppContext; a chip on the rightmost column shows Pending / Approved /
 * Rejected.
 *
 * LO friction fix (2026-04-22): the Approval column is now an inline
 * control, not a read-only chip. Pending rows expose an "Approve" primary
 * button + a reject icon so loan officers can burn through the queue in
 * one click per lead instead of navigating to Offer Orchestrator. Once
 * approved/rejected, the column reverts to the chip shape. Keyboard
 * shortcuts (A / R) act on the expanded row.
 *
 * Sales-ops bulk workflow (2026-04-22): a leftmost checkbox column selects
 * rows for bulk approval. When >= 1 row is selected, a sticky action bar
 * inside the table container offers "Approve N leads" / "Clear selection".
 * Bulk approve loops `api.approve()` per selected lead in chunks of 3 to
 * keep one audit row per approval (matching the single-row flow). Already
 * approved/rejected rows are skipped silently. Shift+A fires bulk-approve
 * when the table has focus; plain A still approves only the expanded row.
 */

export function LeadTable({ leads, totalMatching = null, truncatedAt = null, exportContext, salesTeam = [] }: LeadTableProps) {
  'use no memo';

  const queryClient = useQueryClient();
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  // A11y: the bulk-approve button is the launch point for the bulk flow.
  // After the action settles we restore focus deterministically — to this
  // button when it survives (partial outcome keeps the toolbar mounted) or
  // to the table scroll region when a full success unmounts the toolbar.
  // Without this, keyboard focus silently drops to <body> after a bulk run.
  const bulkApproveBtnRef = useRef<HTMLButtonElement | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [pendingReject, setPendingReject] = useState<string | null>(null);
  const [rejectReasonCode, setRejectReasonCode] = useState<RejectReasonCode>('low_intent');
  const [rejectRationale, setRejectRationale] = useState('');
  const [bulkRationaleOpen, setBulkRationaleOpen] = useState(false);
  const [bulkRationale, setBulkRationale] = useState('');
  const [selectedAssignee, setSelectedAssignee] = useState<string>('');
  const [salesToast, setSalesToast] = useState<string | null>(null);
  const [salesBusy, setSalesBusy] = useState(false);
  const [salesOverrides, setSalesOverrides] = useState<Record<string, Partial<LeadSummary>>>({});
  const [pendingDisposition, setPendingDisposition] = useState<string | null>(null);
  const [dispositionOutcome, setDispositionOutcome] = useState<CallDisposition['outcome']>('called_left_voicemail');
  const [dispositionLo, setDispositionLo] = useState<string>('');
  const [dispositionCallbackAt, setDispositionCallbackAt] = useState('');
  const [dispositionNotes, setDispositionNotes] = useState('');
  const { approvals, setApproval, setLastBorrowerId } = useApp();
  const displayLeads = leads.map((lead) => ({ ...lead, ...(salesOverrides[lead.borrower_id] ?? {}) }));
  const leadsById = new Map(displayLeads.map((lead) => [lead.borrower_id, lead]));
  const sortedLeads = sortKey === 'rank'
    ? displayLeads
    : [...displayLeads].sort((a, b) => {
        const direction = sortDir === 'asc' ? 1 : -1;
        const av = sortValue(a, sortKey);
        const bv = sortValue(b, sortKey);
        if (typeof av === 'number' && typeof bv === 'number') {
          return (av - bv) * direction;
        }
        return String(av).localeCompare(String(bv)) * direction;
      });
  const shouldVirtualize = sortedLeads.length > LEAD_VIRTUALIZATION_THRESHOLD;
  // TanStack Virtual returns imperative instance methods tied to the scroll
  // element. The hook stays local to this table and its methods are not passed
  // into memoized children, so React Compiler's library advisory is expected.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: sortedLeads.length,
    enabled: shouldVirtualize,
    estimateSize: () => LEAD_ROW_ESTIMATE_PX,
    getScrollElement: () => tableWrapRef.current,
    overscan: LEAD_ROW_OVERSCAN,
  });
  const virtualItems = shouldVirtualize ? rowVirtualizer.getVirtualItems() : [];
  const visibleRows = shouldVirtualize
    ? virtualItems.map((item) => ({
        lead: sortedLeads[item.index],
        virtualIndex: item.index,
      }))
    : sortedLeads.map((lead, virtualIndex) => ({ lead, virtualIndex }));
  const topSpacerHeight = virtualItems[0]?.start ?? 0;
  const bottomSpacerHeight = virtualItems.length > 0
    ? Math.max(0, rowVirtualizer.getTotalSize() - virtualItems[virtualItems.length - 1].end)
    : 0;
  const [pendingApproval, setPendingApproval] = useState<Record<string, boolean>>({});
  const [approvalError, setApprovalError] = useState<string | null>(null);
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
  // still to check the audit log instead of blindly retrying. R5-21
  // (2026-04-23).
  const [bulkToast, setBulkToast] = useState<
    { ok: number; fail: number; network: number; aborted: number } | null
  >(null);

  useEffect(() => {
    if (expanded) setLastBorrowerId(expanded);
  }, [expanded, setLastBorrowerId]);

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
    rowInFlightRef.current[borrowerId] = true;
    setApprovalError(null);
    setPendingApproval((p) => ({ ...p, [borrowerId]: true }));
    try {
      const lead = leadsById.get(borrowerId);
      const draft = await api.draftOutreach(borrowerId, 'email', signal);
      const res = await api.approve(
        borrowerId,
        {
          evidence_ids: lead?.evidence_ids ?? [],
          offer_code: draft.offer_code ?? lead?.recommended_offer_code ?? null,
          draft_body: draft.body,
          channel: 'email',
          rationale: extras.rationale ?? null,
          bulk_id: extras.bulk_id ?? null,
          bulk_rationale: extras.bulk_rationale ?? null,
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

  useEffect(() => {
    if (!selectedAssignee && salesTeam.length > 0) {
      setSelectedAssignee(salesTeam[0].email);
    }
  }, [salesTeam, selectedAssignee]);

  function openDisposition(borrowerId: string) {
    const lead = leadsById.get(borrowerId);
    setPendingDisposition(borrowerId);
    setDispositionLo(lead?.assigned_to_email ?? selectedAssignee ?? salesTeam[0]?.email ?? '');
    setDispositionOutcome('called_left_voicemail');
    setDispositionCallbackAt('');
    setDispositionNotes('');
  }

  useEffect(() => {
    if (!salesToast) return;
    const t = window.setTimeout(() => setSalesToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [salesToast]);

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

  function applyAssignmentOverrides(assignments: { borrower_id: string; assigned_to_email: string; assigned_to_label?: string | null; assigned_at: string; expires_at?: string | null }[]) {
    setSalesOverrides((current) => {
      const next = { ...current };
      assignments.forEach((assignment) => {
        next[assignment.borrower_id] = {
          ...(next[assignment.borrower_id] ?? {}),
          assigned_to_email: assignment.assigned_to_email,
          assigned_to_label: assignment.assigned_to_label ?? assignment.assigned_to_email,
          assigned_at: assignment.assigned_at,
          assignment_expires_at: assignment.expires_at ?? null,
        };
      });
      return next;
    });
  }

  async function assignSelected(mode: 'selected-lo' | 'round-robin') {
    if (salesBusy) return;
    const borrowerIds = [...selectedIds].filter((id) => selectableIds.includes(id));
    if (borrowerIds.length === 0) return;
    const loEmails = mode === 'round-robin'
      ? salesTeam.map((member) => member.email)
      : selectedAssignee
        ? [selectedAssignee]
        : [];
    if (loEmails.length === 0) {
      setApprovalError('No active loan officers are available for assignment.');
      return;
    }
    setSalesBusy(true);
    setApprovalError(null);
    try {
      const result = borrowerIds.length === 1 && loEmails.length === 1
        ? {
            assigned_count: 1,
            assignments: [(await api.assignLead(borrowerIds[0], loEmails[0])).assignment],
          }
        : await api.distributeLeads(borrowerIds, loEmails, mode === 'round-robin' ? 'round_robin' : 'score_balanced');
      applyAssignmentOverrides(result.assignments);
      void invalidateOperationalQueries(queryClient);
      setSalesToast(`${result.assigned_count} ${result.assigned_count === 1 ? 'lead' : 'leads'} assigned`);
      setSelectedIds(new Set());
    } catch (err: unknown) {
      setApprovalError(
        err instanceof Error
          ? `Couldn't assign selected leads: ${err.message}`
          : "Couldn't assign selected leads.",
      );
    } finally {
      setSalesBusy(false);
    }
  }

  async function submitDisposition() {
    if (!pendingDisposition) return;
    if (!dispositionLo) {
      setApprovalError('Choose the loan officer who worked this lead.');
      return;
    }
    if (dispositionOutcome === 'callback_scheduled' && !dispositionCallbackAt) {
      setApprovalError('Callback scheduled dispositions require a callback time.');
      return;
    }
    setSalesBusy(true);
    setApprovalError(null);
    try {
      const result = await api.logDisposition(pendingDisposition, {
        lo_email: dispositionLo,
        outcome: dispositionOutcome,
        callback_at: dispositionCallbackAt ? new Date(dispositionCallbackAt).toISOString() : null,
        notes: dispositionNotes.trim() || null,
      });
      setSalesOverrides((current) => ({
        ...current,
        [pendingDisposition]: {
          ...(current[pendingDisposition] ?? {}),
          assigned_to_email: current[pendingDisposition]?.assigned_to_email ?? leadsById.get(pendingDisposition)?.assigned_to_email ?? dispositionLo,
          latest_disposition_outcome: result.disposition.outcome,
          latest_disposition_at: result.disposition.occurred_at,
          latest_callback_at: result.disposition.callback_at ?? null,
        },
      }));
      setSalesToast(`${dispositionLabel(result.disposition.outcome)} logged for ${pendingDisposition}`);
      void invalidateOperationalQueries(queryClient);
      setPendingDisposition(null);
    } catch (err: unknown) {
      setApprovalError(
        err instanceof Error
          ? `Couldn't log disposition: ${err.message}`
          : "Couldn't log disposition.",
      );
    } finally {
      setSalesBusy(false);
    }
  }

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
      // state (server may have committed). Surface a "check audit log"
      // message rather than mixing them into the retryable `fail` count.
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

  // Auto-dismiss the toast after 4s so it doesn't pile up next to the
  // action bar.
  useEffect(() => {
    if (!bulkToast) return;
    const t = window.setTimeout(() => setBulkToast(null), 4000);
    return () => window.clearTimeout(t);
  }, [bulkToast]);

  /**
   * Keyboard: A approves / R rejects the expanded row; Shift+A fires
   * bulk-approve when >= 1 row is selected. We listen at the window
   * level but bail out if focus is inside an editable element so typing
   * in the Genie chat or a filter input never triggers approval.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // R5-12 (2026-04-23): belt-and-suspenders check against both the
      // event target AND document.activeElement. For window-level
      // keydowns `e.target` is usually the focused element, but when
      // nothing is focused it falls back to `document.body` — which
      // would bypass an input check. Checking `activeElement` too
      // means typing "a" in the Genie textarea can never trigger the
      // approve hotkey.
      if (isEditableTarget(e.target as Element | null)) return;
      if (isEditableTarget(document.activeElement)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key.toLowerCase();
      // Shift+A: bulk approve. Takes precedence over single-row A when
      // any row is selected.
      if (key === 'a' && e.shiftKey) {
        if (selectedIds.size === 0 || bulkApproving) return;
        e.preventDefault();
        void bulkApprove();
        return;
      }
      if (!expanded) return;
      const expandedLead = leadsById.get(expanded);
      const expandedStatus = approvals[expanded] ?? expandedLead?.approval_status;
      if (key === 'a') {
        if (!isLeadApprovalEligible(expandedStatus, approvals[expanded], expandedLead)) return;
        e.preventDefault();
        void approveLead(expanded);
      } else if (key === 'r') {
        if (isTerminalApproval(expandedStatus)) return;
        e.preventDefault();
        setPendingReject(expanded);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const stop = (e: ReactKeyboardEvent | ReactMouseEvent) => e.stopPropagation();

  const selectionCount = selectedIds.size;
  const selectedApprovalEligibleCount = approvalEligibleIds.filter((id) => selectedIds.has(id)).length;

  /**
   * Export the currently-visible leads as CSV. Client-side only: the
   * bytes come straight from the `leads` prop the caller already passed
   * in (which is the real /api/leads payload after any segment/filter
   * narrowing the parent route applied). We do NOT invent a server-side
   * export endpoint or synthesize fields the payload doesn't carry — so
   * PII stays suppressed by construction.
   */
  function exportCsv() {
    if (leads.length === 0) return;
    const csv = buildLeadCsv(leads, approvals, exportContext);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const stamp = new Date().toISOString().slice(0, 10);
    a.download = `mip-leads-${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function toggleSort(key: SortKey) {
    if (key === 'rank') {
      setSortKey('rank');
      setSortDir('desc');
      return;
    }
    setSortKey((current) => {
      if (current === key) {
        setSortDir((dir) => (dir === 'desc' ? 'asc' : 'desc'));
        return current;
      }
      setSortDir('desc');
      return key;
    });
  }

  const renderSortHeader = (key: SortKey, label: string) => (
    <button
      type="button"
      className="tbl__sort"
      onClick={() => toggleSort(key)}
      aria-label={`Sort by ${label}`}
      aria-pressed={sortKey === key}
    >
      <span>{label}</span>
      {sortKey === key && key !== 'rank' && (
        <Icon name={sortDir === 'desc' ? 'down' : 'up'} size={10} />
      )}
    </button>
  );

  return (
    // 2026-05-04 fix (alignment): removed inline `overflow: hidden`.
    // It was establishing a new block formatting context that, combined
    // with the table's intrinsic min-content width and the wrap div's
    // overflowY: auto, was nudging the surface off the .main__inner
    // left edge on the lead-queue page. The intended scroll behaviour
    // for table-containing surfaces is provided by the
    // `.surface:has(> div > .tbl) { overflow-x: auto }` rule in
    // components.css; the inline override was both unnecessary and the
    // proximate cause of the shift the user reported.
    <div className="surface">
      <div className="surface__hdr surface__hdr--split">
        <div className="surface__hdr-main">
          <div className="surface__icon">
            <Icon name="user" size={14} />
          </div>
          <div>
            <div className="h-4">Ranked borrowers</div>
            <div className="muted fs-12">
              {/*
                Prototype-parity-audit P2 (2026-05-04): keyboard hints
                were rendered as `.mono` spans, which read as inline code
                rather than a keycap. Switching to `<kbd>` (styled in
                design-system/components.css as a subtle keycap chip)
                makes the affordance scannable — an LO scrolling the
                queue can spot the shortcut without reading prose.
              */}
              Click a row to expand the preview. Keyboard: <kbd>A</kbd> approve, <kbd>R</kbd> reject the expanded row.
            </div>
          </div>
        </div>
        <div className="lead-table__header-actions">
          <Chip variant="neutral" icon="shield">PII suppressed</Chip>
          <Button
            size="sm"
            icon="export"
            onClick={exportCsv}
            disabled={leads.length === 0}
            data-testid="lead-export"
            aria-label={`Export ${leads.length} leads as CSV`}
          >
            Export list
          </Button>
        </div>
      </div>
      {pendingReject && (
        <LeadRejectPanel
          borrowerId={pendingReject}
          reasonCode={rejectReasonCode}
          rationale={rejectRationale}
          onReasonChange={setRejectReasonCode}
          onRationaleChange={setRejectRationale}
          onCancel={() => {
            setPendingReject(null);
            setRejectRationale('');
            setRejectReasonCode('low_intent');
          }}
          onSubmit={() => void submitReject()}
        />
      )}
      {pendingDisposition && (
        <LeadDispositionPanel
          borrowerId={pendingDisposition}
          salesTeam={salesTeam}
          salesBusy={salesBusy}
          loEmail={dispositionLo}
          outcome={dispositionOutcome}
          callbackAt={dispositionCallbackAt}
          notes={dispositionNotes}
          onLoChange={setDispositionLo}
          onOutcomeChange={setDispositionOutcome}
          onCallbackAtChange={setDispositionCallbackAt}
          onNotesChange={setDispositionNotes}
          onCancel={() => setPendingDisposition(null)}
          onSubmit={() => void submitDisposition()}
        />
      )}
      {salesToast && (
        <div role="status" aria-live="polite" className="table-success">
          {salesToast}
        </div>
      )}
      <div ref={tableWrapRef} className="tbl-wrap" tabIndex={0} aria-label="Ranked borrowers table scroll region">
        <table className="tbl lead-table__table" aria-rowcount={sortedLeads.length + 1}>
          <colgroup>
            <col className="lead-table__col-select" />
            <col className="lead-table__col-expand" />
            <col className="lead-table__col-borrower" />
            <col className="lead-table__col-location" />
            <col className="lead-table__col-relationship" />
            <col className="lead-table__col-assignment" />
            <col className="lead-table__col-outreach" />
            <col className="lead-table__col-disposition" />
            <col className="lead-table__col-segments" />
            <col className="lead-table__col-equity" />
            <col className="lead-table__col-rate" />
            <col className="lead-table__col-offer" />
            <col className="lead-table__col-score" />
            <col className="lead-table__col-confidence" />
            <col className="lead-table__col-approval" />
          </colgroup>
          <thead>
            <tr>
              <th className="tbl-cell--select">
                <input
                  type="checkbox"
                  aria-label="Select all eligible leads"
                  checked={headerCheckboxState.checked}
                  ref={(el) => {
                    if (el) el.indeterminate = headerCheckboxState.indeterminate;
                  }}
                  disabled={selectableIds.length === 0 || bulkApproving}
                  onChange={toggleSelectAll}
                  onClick={stop}
                  data-testid="lead-select-all"
                />
              </th>
              <th className="tbl-cell--narrow"></th>
              <th>Borrower</th>
              <th>Location</th>
              <th>{renderSortHeader('relationship', 'Relationship')}</th>
              <th>{renderSortHeader('assignment', 'Assigned to')}</th>
              <th>{renderSortHeader('outreach', 'Outreach')}</th>
              <th>Last touch</th>
              <th>Segments</th>
              <th className="tbl-cell--right">{renderSortHeader('equity', 'Equity')}</th>
              <th className="tbl-cell--right">{renderSortHeader('rate', 'Rate Δ (bps)')}</th>
              <th>Next-best-offer</th>
              <th className="tbl-cell--right">{renderSortHeader('score', 'Score')}</th>
              <th>{renderSortHeader('confidence', 'Signal')}</th>
              <th>Approval</th>
            </tr>
          </thead>
          <tbody>
            {shouldVirtualize && topSpacerHeight > 0 && (
              <tr aria-hidden="true" className="lead-table__virtual-spacer">
                <td colSpan={LEAD_TABLE_COL_COUNT} style={{ height: topSpacerHeight }} />
              </tr>
            )}
            {visibleRows.map(({ lead, virtualIndex }) => {
              const isOpen = expanded === lead.borrower_id;
              // Prefer in-session AppContext override (set optimistically on
              // approve/reject); fall back to the server-projected
              // approval_status so a page reload doesn't make approved
              // borrowers look pending. Round-2 hole-finder #12, 2026-04-23.
              const serverStatus = lead.approval_status;
              const approval: string | undefined = approvals[lead.borrower_id]
                ?? (isTerminalApproval(serverStatus)
                    ? serverStatus
                    : undefined);
              const isSelected = selectedIds.has(lead.borrower_id);
              const isSelectable = isLeadSelectableForSalesOps(serverStatus, approval, lead);
              const isApprovalEligible = isLeadApprovalEligible(serverStatus, approval, lead);
              return (
                <LeadTableRow
                  key={lead.borrower_id}
                  lead={lead}
                  virtualIndex={virtualIndex}
                  isOpen={isOpen}
                  approval={approval}
                  isSelected={isSelected}
                  isSelectable={isSelectable}
                  isApprovalEligible={isApprovalEligible}
                  bulkApproving={bulkApproving}
                  salesBusy={salesBusy}
                  salesTeamCount={salesTeam.length}
                  pendingApproval={Boolean(pendingApproval[lead.borrower_id])}
                  onToggleRow={(row, open) => {
                    setLastBorrowerId(row.borrower_id);
                    setExpanded(open ? null : row.borrower_id);
                  }}
                  onToggleSelect={toggleSelect}
                  onApprove={(borrowerId) => void approveLead(borrowerId)}
                  onReject={setPendingReject}
                  onOpenDisposition={openDisposition}
                />
              );
            })}
            {shouldVirtualize && bottomSpacerHeight > 0 && (
              <tr aria-hidden="true" className="lead-table__virtual-spacer">
                <td colSpan={LEAD_TABLE_COL_COUNT} style={{ height: bottomSpacerHeight }} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {selectionCount > 0 && (
        <div
          role="toolbar"
          aria-label="Bulk actions"
          data-testid="lead-bulk-actions"
          className="bulk-actions"
        >
          <div className="bulk-actions__label">
            <span className="mono num">{selectionCount}</span> {selectionCount === 1 ? 'lead' : 'leads'} selected
          </div>
          {selectionCount > 1 && bulkRationaleOpen && (
            <label className="bulk-actions__rationale">
              <span className="field__label">Shared approval rationale</span>
              <input
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
                    value={selectedAssignee}
                    onChange={(e) => setSelectedAssignee(e.target.value)}
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
                  onClick={() => void assignSelected('selected-lo')}
                  disabled={salesBusy || !selectedAssignee || selectionCount === 0}
                  aria-label={`Assign ${selectionCount} selected leads to selected loan officer`}
                >
                  {salesBusy ? 'Assigning…' : 'Assign'}
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  icon="layers"
                  onClick={() => void assignSelected('round-robin')}
                  disabled={salesBusy || salesTeam.length === 0 || selectionCount === 0}
                  aria-label={`Distribute ${selectionCount} selected leads across active loan officers`}
                >
                  Distribute
                </Button>
              </>
            )}
            {bulkToast && (
              <span
                role="status"
                aria-live="polite"
                data-testid="lead-bulk-toast"
                className={`bulk-actions__toast ${
                  bulkToast.aborted > 0 || bulkToast.network > 0
                    ? 'bulk-actions__toast--danger'
                    : bulkToast.fail > 0
                      ? 'bulk-actions__toast--warn'
                      : 'bulk-actions__toast--ok'
                }`}
              >
                {bulkToast.ok} approved
                {bulkToast.fail > 0 ? `, ${bulkToast.fail} failed` : ''}
                {bulkToast.network > 0
                  ? ` (${bulkToast.network} network dropped — retry)`
                  : ''}
                {/*
                  R5-21: aborted rows are in an ambiguous state. Server
                  idempotency is safe only when the same request_id is
                  reused; this bulk toast no longer owns those ids after
                  unmount, so direct the user to the audit log instead.
                */}
                {bulkToast.aborted > 0
                  ? ` · ${bulkToast.aborted} cancelled in flight — unknown state, check the audit log`
                  : ''}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={clearSelection}
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
              onClick={() => void bulkApprove()}
              disabled={bulkApproving || selectedApprovalEligibleCount === 0}
              data-testid="lead-bulk-approve"
              aria-label={`Approve ${selectedApprovalEligibleCount} eligible leads`}
            >
              {bulkApproving ? 'Approving…' : `Approve ${selectedApprovalEligibleCount} eligible`}
            </Button>
          </div>
        </div>
      )}
      {approvalError && (
        <div
          role="alert"
          className="table-error"
        >
          {approvalError}
        </div>
      )}
      <div className="surface__ft">
        Showing {leads.length.toLocaleString()} ranked borrower{leads.length === 1 ? '' : 's'}
        {totalMatching !== null && (
          <>
            {' '}of {totalMatching.toLocaleString()} total matching filters
          </>
        )}
        {truncatedAt !== null && totalMatching !== null && totalMatching > leads.length && (
          <span className="muted"> · capped at {truncatedAt.toLocaleString()}</span>
        )}
      </div>
    </div>
  );
}
